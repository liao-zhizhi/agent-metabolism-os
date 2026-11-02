"""L6 -- photosynthetic day/night adaptation.

Photosynthesis cannot run in the dark, so the plant does something better than
stopping: at night it consolidates. What the day accumulated is compressed into
storage that the next day can use, and the machinery of collection is allowed to
go quiet while the machinery of consolidation runs.

An agent harness has the same shape. During the day, compartments accumulate
context faster than anything reads it, and paying a model to re-read a whole
transcript later is exactly the cost L4 exists to avoid. So night mode does the
cheap structural work: it walks the compartments, deduplicates, extracts the
recurring terms, and appends a content-addressed fragment to a long-term file.

Two boundaries are worth stating, because both are easy to get wrong:

**L6 only reads.** It never mutates a compartment's ``local_context``. Night
consolidation that pruned its sources would make the day's record disappear
based on a schedule, which is the opposite of what an audit trail is for. If
memory pressure ever needs pruning, that belongs to L7's lysis, where it is
explicit and logged.

**Consolidation is idempotent by construction.** Each fragment's id is a digest
of the exact messages it summarises, so running night mode twice appends
nothing the second time. Content addressing is doing the work that a "have I
already summarised this?" flag would otherwise have to do -- and it cannot fall
out of sync, because it *is* the content.

Honest limitation: this is a structural digest, not a summary. It deduplicates,
counts, and picks out repeated terms; it does not understand the transcript and
never claims to. The output is labelled ``method: "structural-digest"`` so a
later reader can tell which kind of memory they are looking at.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "CONSOLIDATION_METHOD",
    "CircadianError",
    "CircadianScheduler",
    "ConsolidationReport",
    "KnowledgeFragment",
    "MemoryFileError",
]

CONSOLIDATION_METHOD = "structural-digest"
MEMORY_VERSION = 1
DEFAULT_MEMORY_FILENAME = "long_term_memory.json"

DAY_START_HOUR = 6
DAY_END_HOUR = 22

# Words that say nothing about a transcript's subject. Kept short on purpose:
# a large stop-word list is a language assumption this prototype cannot back up.
_STOPWORDS = frozenset(
    """
    the and for that with this from have been were was are you your then than
    they them their what when where which while into over under about after
    before here there also just only very much more most some such can will
    would could should not but its it's it is to of in on at as by or an a
    """.split()
)

_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,}")


class CircadianError(Exception):
    """Base class for the night cycle."""


class MemoryFileError(CircadianError):
    """The long-term memory file exists but cannot be read or written."""


@dataclass(frozen=True)
class KnowledgeFragment:
    """One compressed unit distilled from one compartment's day."""

    fragment_id: str
    source: str
    message_count: int
    summary: str
    keywords: Tuple[str, ...]
    method: str = CONSOLIDATION_METHOD

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["keywords"] = list(self.keywords)
        return payload

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "KnowledgeFragment":
        return cls(
            fragment_id=payload["fragment_id"],
            source=payload["source"],
            message_count=int(payload["message_count"]),
            summary=payload["summary"],
            keywords=tuple(payload.get("keywords", ())),
            method=payload.get("method", CONSOLIDATION_METHOD),
        )


@dataclass
class ConsolidationReport:
    """What one night's work actually did."""

    daytime_after: bool
    compartments_scanned: int
    messages_seen: int
    fragments_created: int
    fragments_skipped: int
    bytes_written: int
    path: str
    created_ids: Tuple[str, ...] = ()
    skipped_ids: Tuple[str, ...] = ()
    warnings: Tuple[str, ...] = ()

    @property
    def did_work(self) -> bool:
        return self.fragments_created > 0

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["created_ids"] = list(self.created_ids)
        payload["skipped_ids"] = list(self.skipped_ids)
        payload["warnings"] = list(self.warnings)
        return payload


def _digest(source: str, bodies: Sequence[str]) -> str:
    """Content address: the identity of a fragment *is* what it summarises."""
    hasher = hashlib.sha256()
    hasher.update(source.encode("utf-8"))
    for body in sorted(bodies):
        hasher.update(b"\x00")
        hasher.update(body.encode("utf-8"))
    return hasher.hexdigest()[:16]


def extract_keywords(bodies: Sequence[str], limit: int = 6) -> Tuple[str, ...]:
    """Most frequent substantive terms, ties broken alphabetically (stable)."""
    counts: Dict[str, int] = {}
    for body in bodies:
        for token in _TOKEN.findall(body.lower()):
            if token in _STOPWORDS:
                continue
            counts[token] = counts.get(token, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return tuple(word for word, _ in ranked[:limit])


def compress(source: str, bodies: Sequence[str]) -> Optional[KnowledgeFragment]:
    """Turn one compartment's messages into a fragment, or ``None`` if empty."""
    unique = sorted({body for body in bodies if body.strip()})
    if not unique:
        return None
    keywords = extract_keywords(unique)
    opening = unique[0].replace("\n", " ")
    if len(opening) > 96:
        opening = opening[:93] + "..."
    if len(unique) == 1:
        summary = "{} message: {}".format(1, opening)
    else:
        summary = "{} distinct messages; recurring: {}; first: {}".format(
            len(unique), ", ".join(keywords) or "(none)", opening
        )
    return KnowledgeFragment(
        fragment_id=_digest(source, unique),
        source=source,
        message_count=len(unique),
        summary=summary,
        keywords=keywords,
    )


class CircadianScheduler:
    """Day/night state plus the offline consolidation pass.

    The clock is injectable so tests and demos are deterministic rather than
    time-dependent -- a test that only passes at 21:00 is a bug report waiting
    to happen.
    """

    def __init__(
        self,
        manager: Any,
        memory_path: str = DEFAULT_MEMORY_FILENAME,
        *,
        clock: Optional[Callable[[], float]] = None,
        day_start_hour: int = DAY_START_HOUR,
        day_end_hour: int = DAY_END_HOUR,
        daytime: Optional[bool] = None,
    ) -> None:
        if manager is None or not hasattr(manager, "alive_names"):
            raise TypeError("manager must be a CompartmentManager")
        if not isinstance(memory_path, str) or not memory_path.strip():
            raise ValueError("memory_path must be a non-empty str")
        if not 0 <= day_start_hour < day_end_hour <= 24:
            raise ValueError(
                "require 0 <= day_start_hour < day_end_hour <= 24, got "
                "{} and {}".format(day_start_hour, day_end_hour)
            )
        if daytime is not None and not isinstance(daytime, bool):
            raise TypeError("daytime must be a bool or None")

        self._manager = manager
        self._memory_path = memory_path
        self._clock = clock or time.time
        self._day_start = day_start_hour
        self._day_end = day_end_hour
        self._daytime = self._is_day_at(self._clock()) if daytime is None else daytime
        self._last_report: Optional[ConsolidationReport] = None

    # -- day / night ------------------------------------------------------

    @property
    def memory_path(self) -> str:
        return self._memory_path

    @property
    def last_report(self) -> Optional[ConsolidationReport]:
        return self._last_report

    def _local_hour(self) -> int:
        return time.localtime(self._clock()).tm_hour

    def _is_day_at(self, timestamp: float) -> bool:
        hour = time.localtime(timestamp).tm_hour
        return self._day_start <= hour < self._day_end

    def is_daytime(self) -> bool:
        """Whether the system is currently in its collection phase.

        A recorder that has been placed in night mode stays there until
        :meth:`enter_daytime` is called: the pass is an explicit state, not a
        guess recomputed from the wall clock on every query.
        """
        return self._daytime

    def refresh_from_clock(self) -> bool:
        """Re-derive day/night from the clock. Returns the new state."""
        self._daytime = self._is_day_at(self._clock())
        return self._daytime

    def enter_daytime(self) -> bool:
        self._daytime = True
        return self._daytime

    def enter_nighttime(self) -> bool:
        self._daytime = False
        return self._daytime

    def toggle(self) -> bool:
        self._daytime = not self._daytime
        return self._daytime

    def window(self) -> Tuple[int, int]:
        return (self._day_start, self._day_end)

    # -- the night pass ---------------------------------------------------

    def run_offline_mode(self, *, dry_run: bool = False) -> ConsolidationReport:
        """Consolidate the day's compartment context into long-term memory.

        Runs only at night. Calling it during the day raises rather than
        silently doing the work anyway -- a consolidation pass that can fire at
        any moment is not a night cycle, it is a background thread, and the
        difference matters when a transcript is half-written.

        Each compartment is read through its public ``local_context`` and never
        written to. Fragments already present in the memory file are skipped by
        content address, so a second call in the same night is a no-op.
        """
        if self._daytime:
            raise CircadianError(
                "refusing to consolidate during the day; call enter_nighttime() "
                "or run_offline_mode() from the offline window"
            )

        existing = self.load_memory()
        known = {fragment.fragment_id for fragment in existing}

        created: List[KnowledgeFragment] = []
        skipped: List[str] = []
        messages_seen = 0
        scanned = 0
        warnings: List[str] = []

        for name in self._manager.alive_names():
            compartment = self._manager.get(name)
            scanned += 1
            context = tuple(compartment.local_context)
            bodies = [message.body for message in context]
            messages_seen += len(bodies)
            fragment = compress(name, bodies)
            if fragment is None:
                continue
            if fragment.fragment_id in known:
                skipped.append(fragment.fragment_id)
                continue
            known.add(fragment.fragment_id)
            created.append(fragment)

        bytes_written = 0
        if created and not dry_run:
            payload = [fragment.to_dict() for fragment in existing + created]
            bytes_written = self._write_memory(payload)
        elif created and dry_run:
            warnings.append("dry_run: {} fragment(s) not written".format(len(created)))

        report = ConsolidationReport(
            daytime_after=self._daytime,
            compartments_scanned=scanned,
            messages_seen=messages_seen,
            fragments_created=len(created) if not dry_run else 0,
            fragments_skipped=len(skipped),
            bytes_written=bytes_written,
            path=self._memory_path,
            created_ids=tuple(f.fragment_id for f in created),
            skipped_ids=tuple(skipped),
            warnings=tuple(warnings),
        )
        self._last_report = report
        return report

    # -- the memory file --------------------------------------------------

    def load_memory(self) -> List[KnowledgeFragment]:
        """Read the long-term file, tolerating its absence but not its damage.

        A missing file means "nothing has been consolidated yet", which is a
        normal first night. A corrupt one is not normal: silently starting over
        would erase the only durable record the system has, so it raises.
        """
        if not os.path.exists(self._memory_path):
            return []
        try:
            with open(self._memory_path, encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, ValueError) as problem:
            raise MemoryFileError(
                "cannot read long-term memory at {!r}: {}".format(
                    self._memory_path, problem
                )
            ) from None

        fragments = raw.get("fragments") if isinstance(raw, dict) else None
        if not isinstance(fragments, list):
            raise MemoryFileError(
                "long-term memory at {!r} has no 'fragments' list".format(
                    self._memory_path
                )
            )
        loaded: List[KnowledgeFragment] = []
        for entry in fragments:
            if not isinstance(entry, dict) or "fragment_id" not in entry:
                raise MemoryFileError(
                    "long-term memory at {!r} holds a malformed fragment".format(
                        self._memory_path
                    )
                )
            loaded.append(KnowledgeFragment.from_dict(entry))
        return loaded

    def _write_memory(self, fragments: Sequence[Dict[str, Any]]) -> int:
        """Write atomically: a half-written memory file is worse than none."""
        directory = os.path.dirname(os.path.abspath(self._memory_path))
        os.makedirs(directory, exist_ok=True)
        document = {
            "version": MEMORY_VERSION,
            "method": CONSOLIDATION_METHOD,
            "updated_at": time.strftime(
                "%Y-%m-%dT%H:%M:%S", time.localtime(self._clock())
            ),
            "fragments": list(fragments),
        }
        text = json.dumps(document, indent=2, ensure_ascii=False) + "\n"

        handle = tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=directory,
            prefix=".memory-",
            suffix=".tmp",
            delete=False,
        )
        temp_path = handle.name
        try:
            with handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self._memory_path)
        except OSError as problem:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise MemoryFileError(
                "cannot write long-term memory to {!r}: {}".format(
                    self._memory_path, problem
                )
            ) from None
        return len(text.encode("utf-8"))

    def __repr__(self) -> str:
        return "CircadianScheduler({}, {}, window={:02d}-{:02d})".format(
            "day" if self._daytime else "night",
            self._memory_path,
            self._day_start,
            self._day_end,
        )
