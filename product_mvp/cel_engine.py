"""CEL gateway -- the product-side membrane over ``agent-metabolism-os``.

This is the layer the HTTP API talks to. It owns three things and nothing else:

1. **The membrane.** Every submitted ``(check, witness)`` pair is booked through
   :class:`agent_metabolism.double_entry.DoubleEntryLedger`, which composes the
   verdict from the two booleans rather than trusting either one:

   ============  ================  =============================================
   ``check``     ``witness``       verdict
   ============  ================  =============================================
   True          True              PASS      the effect landed and was inspected
   False         False             PASS      an *honest* failure report
   True          False             FAIL      false completion -- a caught lie
   False         True              FAIL      under-claim -- the work landed anyway
   anything      None              INVALID   nobody looked; not evidence
   ============  ================  =============================================

2. **The trust ledger.** Each agent is charged in its own
   :class:`agent_metabolism.penalty.PenaltyLedger`, so "who is slacking" is a
   real question with a real answer. One false completion is a 50-unit fine and
   25 points of trust, which crosses ``CRITICAL`` on the first lie.

3. **The audit chain.** :class:`agent_metabolism.double_entry` keeps an ordered
   journal but does not chain it. The API promises a *tamper-evident* log, so
   every accepted entry (including refusals, which are also facts) is folded
   into a SHA-256 chain here: ``hash = sha256(prev_hash || canonical_json)``.
   Re-walking the chain is how ``chain_ok`` is computed -- it is a check, not a
   stored opinion.

Honest boundaries
-----------------
* The fine weights are *policy*, not measurement. They come from
  ``penalty.DEFAULT_WEIGHTS``; nothing here computes what a false completion
  actually costs a customer.
* The warrant-based ``MembraneGateway`` in ``agent-harness-audit`` is a different
  gate: it takes a physical artefact plus a digest before an *action* is
  allowed. This MVP needs a one-shot verdict over ``check``/``witness``, which is
  precisely the ``DoubleEntryLedger`` contract. When a caller supplies an
  ``artefact`` path it is hashed with ``digest_file`` and recorded. Note what
  that does and does not buy: it binds the claim to a *specific digest*, so it
  can be falsified later by anyone holding the real file. It does not prove the
  file existed, because the server cannot read the client's disk. See
  ``WitnessHashLimits``.
* State is in memory. Restarting the process clears the books; ``CEL_LEDGER_LOG``
  optionally appends the chain to disk so a restart is auditable, but replaying
  it into live trust is out of scope for the MVP.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# The core library is not an installed package: the service is deliberately
# runnable from a bare checkout with no build step. Two layouts are supported,
# because the same file has to work in both:
#
#   workspace layout   Gradia/product_mvp/  +  Gradia/agent-metabolism-os/
#   vendored layout    agent-metabolism-os/product_mvp/  (this repository)
#
# A hard-coded "../agent-metabolism-os" works in exactly one of them, which is
# the kind of assumption that passes every local test and fails on first deploy.
_HERE = Path(__file__).resolve().parent
_CORE_MARKER = Path("agent_metabolism") / "double_entry.py"


def core_root() -> Optional[Path]:
    """Where ``agent_metabolism`` lives, or ``None`` to use what is importable.

    Raises:
        SystemExit: ``CEL_CORE_PATH`` was set and does not point at the library.
            Starting with a wrong path silently would fail at the first import
            with a message about a missing module, which is a worse clue than
            naming the variable that is wrong.
    """
    override = os.environ.get("CEL_CORE_PATH")
    if override:
        candidate = Path(override).expanduser().resolve()
        if not (candidate / _CORE_MARKER).is_file():
            raise SystemExit(
                "CEL_CORE_PATH={} does not contain agent_metabolism/double_entry.py"
                .format(candidate)
            )
        return candidate

    for candidate in (_HERE.parent / "agent-metabolism-os", _HERE.parent):
        if (candidate / _CORE_MARKER).is_file():
            return candidate
    return None


_CORE_ROOT = core_root()
if _CORE_ROOT is not None and str(_CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CORE_ROOT))

from agent_metabolism.double_entry import (  # noqa: E402
    FAIL,
    INVALID,
    PASS,
    AgentLocked,
    DoubleEntryError,
    DoubleEntryLedger,
    Transaction,
    digest_file,
)
from agent_metabolism.penalty import (  # noqa: E402
    CRITICAL,
    HEALTHY,
    MAX_TRUST,
    WARNING,
    PenaltyLedger,
)
from agent_metabolism.dynamics import (  # noqa: E402
    effective_witnesses,
    lineage_correlation,
)

#: Refusals are not verdicts -- nothing was judged -- but they belong in the
#: audit log all the same, and the UI needs a word for them.
#: Refusals are not verdicts -- nothing was judged -- but they belong in the
#: audit log all the same, and the UI needs a word for them.
REFUSED = "REFUSED"


class WitnessHashLimits:
    """What a client-supplied digest does and does not prove. Read before quoting.

    **What this gateway now enforces** (all mechanical, all testable):

    * A success claim must carry a well-formed SHA-256. A parse failure, a null,
      or free text is refused before anything is booked.
    * A success claim must carry a ``trace_id``, and an id already used is a
      replay. One physical action therefore cannot be booked twice.
    * The digest is stored next to the claim in the hash-chained entry, so the
      commitment is fixed at submission time and cannot be quietly restated
      later.

    **What it does not enforce:** that the digest describes anything that
    happened. The server cannot see the client's disk, so a client that types a
    digest of a string it invented passes every check above. This is not a bug
    awaiting a patch; it is the boundary of a remote-witness protocol.

    The honest framing is *commitment, not verification*. Before this change the
    ledger held an unfalsifiable boolean; now it holds a falsifiable commitment.
    A third party who later obtains the real artefact can check whether the
    recorded digest matches, and a mismatch is proof of a lie. That is a real
    gain in accountability and **not** a gain in presupposed truth.

    Closing the remaining gap needs a witness the client does not control:
    instrument-level audit trails signed at source (ALCOA+ / 21 CFR 11.10(e)),
    a hardware-rooted attestation, or an independent notary. Until one of those
    is wired in, a ``PASS`` here means "a well-formed commitment was made", not
    "the work was done".
    """


_WitnessHashLimits = WitnessHashLimits
#: Worst-wins ordering for the headline health state.
_STATE_ORDER = {HEALTHY: 0, WARNING: 1, CRITICAL: 2}


class VerifyError(Exception):
    """The submission cannot be booked as given (bad types, empty fields)."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class UnwitnessedClaim(VerifyError):
    """A success claim arrived with no digest for the cloud to hold onto.

    Raised instead of booking an ``INVALID`` row, because a submission that
    never became a legal claim is not a judgement about an agent -- it is a
    malformed request. The distinction is load-bearing:

    * an **honest** agent whose experiment failed submits ``witness=false`` and
      is booked (and, by policy, not punished);
    * an agent that claims success without evidence gets **rejected**, and the
      ledger does not move.

    Booking the second as INVALID would put a permanent mark on an agent for a
    client bug, and the previous design round already showed where punishing
    ambiguity leads: agents stop submitting anything.
    """

    def __init__(self, message: str, status_code: int = 422) -> None:
        super().__init__(message, status_code)


class FalsificationError(VerifyError):
    """A refutation cannot be applied as given.

    Status codes carry meaning here, and the dashboard depends on the split:

    * ``404``  no PASS commitment carries that ``trace_id``. There is nothing
      to refute, and pretending otherwise would let a refutation invent a
      history.
    * ``409``  the commitment was already refuted (a second observer), or the
      entry is an honest failure with no digest -- refuting it would be
      refuting a true statement.
    * ``422``  the payload contradicts itself, e.g. ``hash_mismatch`` with no
      observed digest, or an observed digest equal to the committed one.
    """


#: The two ways an observer can find a commitment false. Both are physical
#: observations; neither is inferred by the server.
FALSIFY_FILE_NOT_FOUND = "file_not_found"
FALSIFY_HASH_MISMATCH = "hash_mismatch"
FALSIFY_REASONS = (FALSIFY_FILE_NOT_FOUND, FALSIFY_HASH_MISMATCH)


#: A witness digest is a SHA-256, bare or scheme-prefixed. Anything else is a
#: sentence wearing a hash's clothes.
_SHA256_BARE = re.compile(r"^[0-9a-fA-F]{64}$")
_SHA256_PREFIXED = re.compile(r"^sha256:([0-9a-fA-F]{64})$")


def normalise_witness_hash(value: Any) -> Optional[str]:
    """The bare lowercase digest, or ``None`` when ``value`` is not one.

    Returns ``None`` for every non-digest, including the empty string and a
    plausible-looking short hex. A validator that accepts "close enough" is how
    a hex field becomes free text again.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    prefixed = _SHA256_PREFIXED.match(text)
    if prefixed:
        return prefixed.group(1).lower()
    if _SHA256_BARE.match(text):
        return text.lower()
    return None


def _canonical(payload: Dict[str, Any]) -> bytes:
    """Stable bytes for hashing: key order and whitespace must not matter."""
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _chain_hash(prev_hash: str, body: Dict[str, Any]) -> str:
    unsigned = {k: v for k, v in body.items() if k != "hash"}
    return hashlib.sha256((prev_hash + "|").encode("utf-8") + _canonical(unsigned)).hexdigest()


class CelGateway:
    """The membrane, the per-agent trust books, and the audit chain.

    One instance is shared by the whole process; calls are serialised with a
    lock because the ledger and the chain both have a single tail.

    Args:
        fail_limit: consecutive FAILs that quarantine an agent. The core
            additionally quarantines on a *single* INVALID (nobody looked).
        ledger_log: optional path; when set, every chained entry is appended as
            one JSON line, so the log survives the process.
    """

    def __init__(self, fail_limit: int = 3, ledger_log: Optional[str] = None) -> None:
        self.fail_limit = fail_limit
        self._lock = threading.RLock()
        self._started_at = time.time()
        self._trust: Dict[str, PenaltyLedger] = {}
        self._entries: List[Dict[str, Any]] = []
        # 64 zeroes, not the word "GENESIS": a genesis value that looks like a
        # hash is one nobody checks.
        self._chain_head = "0" * 64
        self._seq = 0
        #: ``trace_id`` -> agent that first used it. A claim is a commitment:
        #: the same id submitted twice is either a retry (nothing new happened)
        #: or a replay of someone else's real digest. Both are refused, because
        #: a ledger where one physical action can be booked twice measures the
        #: number of requests, not the number of things that occurred.
        self._trace_ids: Dict[str, str] = {}
        #: ``trace_id`` -> the sealed refutation that overturned it. Kept as a
        #: separate map rather than by rewriting the original entry: the chain
        #: hash covers the entry body, so editing a PASS into a REFUTED in place
        #: would make ``verify_chain()`` fail -- the tamper evidence would be
        #: destroyed by the very act of correcting the record. A refutation is
        #: therefore an *appended* entry that points back at the original.
        self._refuted: Dict[str, Dict[str, Any]] = {}
        #: Submissions refused before they became entries. Kept separately
        #: because they are **not** on the chain -- by construction there is no
        #: entry to derive them from -- and an operator still needs to see that
        #: someone tried. In-memory only: not tamper-evident, and labelled so.
        self._rejections: List[Dict[str, Any]] = []
        #: ``agent`` -> the lineage tokens that agent declared. **Declared, not
        #: measured**, and the direction of the incentive is why the label
        #: matters: shared lineage lowers ``n_effective`` while distinct lineage
        #: raises it, so only the flattering claim is tempting. See
        #: :meth:`declare_lineage` for what this can and cannot support.
        self._lineage: Dict[str, Tuple[str, ...]] = {}

        self._books = DoubleEntryLedger(
            penalty_factory=self._ledger_for, fail_limit=fail_limit
        )

        self._log_path: Optional[Path] = None
        if ledger_log:
            self._log_path = Path(ledger_log)
            self._log_path.parent.mkdir(parents=True, exist_ok=True)

    # -- ledger plumbing ---------------------------------------------------

    def _ledger_for(self, agent: str) -> PenaltyLedger:
        """The per-agent trust book. Used as ``penalty_factory``."""
        ledger = self._trust.get(agent)
        if ledger is None:
            ledger = PenaltyLedger()
            self._trust[agent] = ledger
        return ledger

    def _state_of(self, agent: str) -> str:
        ledger = self._trust.get(agent)
        return ledger.get_state() if ledger is not None else HEALTHY

    def _trust_of(self, agent: str) -> Optional[float]:
        ledger = self._trust.get(agent)
        if ledger is None:
            return None
        return round(float(ledger.trust_score), 2)

    def _note_rejection(
        self, agent_id: str, trace_id: Optional[str], reason: str
    ) -> None:
        """Record a refused submission without booking it.

        Not on the chain: there is no entry, so there is nothing to chain. It is
        still recorded, because an audit wall that silently drops attempts is a
        wall nobody can review. The cost of that honesty is stated in ``stats``:
        this counter is kept, not derived, and is therefore not tamper-evident.
        """
        with self._lock:
            self._rejections.append(
                {
                    "ts": time.time(),
                    "agent": agent_id.strip() if isinstance(agent_id, str) else "",
                    "trace_id": trace_id if isinstance(trace_id, str) else "",
                    "reason": reason,
                }
            )

    # -- the chain ---------------------------------------------------------

    def _append(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """Seal one entry onto the hash chain and (optionally) to disk.

        The chain has its own counter. ``DoubleEntryLedger`` also returns a
        ``seq``, but that one counts *judged* entries only, so a refusal or an
        unlock would reuse the previous number and the feed would read as if
        nothing had arrived. The receipt's number is therefore dropped, not
        trusted: a duplicate ``seq`` is a monitor that silently skips lines.
        """
        self._seq += 1
        body: Dict[str, Any] = {"seq": self._seq, "ts": time.time()}
        body.update({k: v for k, v in entry.items() if k != "seq"})
        body["prev_hash"] = self._chain_head
        body["hash"] = _chain_hash(self._chain_head, body)
        self._chain_head = body["hash"]
        self._entries.append(body)
        if self._log_path is not None:
            try:
                with self._log_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(body, ensure_ascii=False) + "\n")
            except OSError:
                # A demo must not lose the verdict because a volume is read-only.
                pass
        return body

    def verify_chain(self) -> bool:
        """Re-walk the chain from genesis. True only if every link recomputes."""
        prev = "0" * 64
        for entry in self._entries:
            if entry.get("prev_hash") != prev:
                return False
            if _chain_hash(prev, entry) != entry.get("hash"):
                return False
            prev = entry["hash"]
        return prev == self._chain_head

    # -- the public surface ------------------------------------------------

    def verify(
        self,
        *,
        agent_id: str,
        intent: str,
        check: bool,
        witness: Optional[bool],
        artefact: str = "",
        cost: float = 0.0,
        witness_hash: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Book one entry. Never raises for a *judged* result -- a FAIL is a
        successful audit, not an API error.

        A success claim must arrive with a digest. ``witness=True`` and no
        ``witness_hash`` is refused (:class:`UnwitnessedClaim`), and
        ``witness=True`` with a duplicate ``trace_id`` is refused as a replay.
        Both leave the ledger untouched.

        What the digest buys, stated plainly so nobody over-reads it: the cloud
        records a *commitment* it can check later against the real artefact,
        and cannot be replayed. It does **not** verify provenance -- the server
        has no access to the client's disk, so a client that invents a digest is
        booked as a pass. See ``WitnessHashLimits`` below.

        Raises:
            UnwitnessedClaim: success claimed, no usable digest.
            VerifyError: the submission is malformed, or the trace_id is a replay.
            AgentLocked: the agent's book is closed. The refusal is still sealed
                onto the chain, because "someone tried again" is evidence.
        """
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise VerifyError("agent_id must be a non-empty string")
        if not isinstance(intent, str) or not intent.strip():
            raise VerifyError("intent must be a non-empty string")
        if type(check) is not bool:
            raise VerifyError("check must be a JSON boolean, not {!r}".format(check))
        if witness is not None and type(witness) is not bool:
            raise VerifyError(
                "witness must be true, false, or null (null = nothing was inspected)"
            )

        # -- the witness digest ------------------------------------------------
        # A supplied digest is parsed, never taken on trust. Note what is *not*
        # here any more: the old code called os.path.isfile(artefact), which
        # resolves on the server's own disk. For a remote client that path can
        # never exist, so the hash branch never fired and every entry looked
        # exactly like an ungrounded one -- a check that reads as coverage and
        # is structurally incapable of ever running. It is gone rather than
        # fixed: the server has no standing to read a client's filesystem.
        digest = normalise_witness_hash(witness_hash)
        if witness_hash not in (None, "") and digest is None:
            raise VerifyError(
                "witness_hash must be 64 hex characters or 'sha256:<64 hex>', "
                "got {!r}".format(witness_hash),
                422,
            )

        trace = trace_id.strip() if isinstance(trace_id, str) else ""
        if witness is True:
            if digest is None:
                self._note_rejection(
                    agent_id, trace_id, "success claimed without a witness digest"
                )
                raise UnwitnessedClaim(
                    "witness=true requires witness_hash: a success claim with no "
                    "digest is not evidence the cloud can hold you to"
                )
            if not trace:
                self._note_rejection(
                    agent_id, trace_id, "success claimed without a trace_id"
                )
                raise UnwitnessedClaim(
                    "witness=true requires trace_id: without it the same digest "
                    "can be replayed and one action is booked as many"
                )

        # ``artefact`` is the client's pointer, recorded as given. It is not a
        # path the server will open -- see the note above.
        recorded_artefact = artefact if isinstance(artefact, str) else ""

        tx = Transaction(
            agent=agent_id.strip(),
            intent=intent.strip(),
            check=check,
            witness=witness,
            artefact=recorded_artefact,
            cost=float(cost),
        )

        with self._lock:
            if witness is True and trace in self._trace_ids:
                self._note_rejection(
                    agent_id, trace_id, "duplicate trace_id (replay)"
                )
                raise VerifyError(
                    "trace_id {!r} was already booked by {!r}; a claim cannot be "
                    "replayed".format(trace, self._trace_ids[trace]),
                    409,
                )
            try:
                receipt = self._books.record_transaction(tx)
            except AgentLocked as locked:
                sealed = self._append(
                    {
                        "agent": agent_id.strip(),
                        "intent": intent.strip(),
                        "check": check,
                        "witness": witness,
                        "verdict": REFUSED,
                        "artefact": recorded_artefact,
                        "witness_hash": digest or "",
                        "trace_id": trace,
                        "cost": float(cost),
                        "trust_after": self._trust_of(agent_id.strip()),
                        "penalty": 0.0,
                        "charged_as": "",
                        "book_locked": True,
                        "reason": locked.reason,
                    }
                )
                raise _Refused(locked, sealed) from None
            except DoubleEntryError as error:
                raise VerifyError(str(error), status_code=422) from None

            # Carried on the receipt rather than added to the core ``Transaction``:
            # the digest is this gateway's contract with the client, and the
            # library that composes verdicts has no opinion about it.
            receipt["witness_hash"] = digest or ""
            receipt["trace_id"] = trace
            sealed = self._append(receipt)
            if trace:
                self._trace_ids[trace] = agent_id.strip()

        state = self._state_of(agent_id.strip())
        verdict = receipt["verdict"]
        return {
            "verdict": verdict,
            "agent_id": receipt["agent"],
            "intent": receipt["intent"],
            "check": receipt["check"],
            "witness": receipt["witness"],
            "trust_score": receipt["trust_after"],
            "penalty_state": state,
            "inflamed": state == CRITICAL,
            "penalty": receipt["penalty"],
            "charged_as": receipt["charged_as"],
            # "grounded" used to be set from "a digest was supplied", which
            # reads as "the loop is closed". It is not: a liar who hashes a
            # string that never touched a disk satisfies that test. The server
            # cannot read the client's filesystem, so the honest answer to
            # "was this grounded in reality" is "unknown" -- and null says so.
            # See WitnessHashLimits and CAUSAL_HARDENING_REPORT.md §4.
            "commitment_recorded": bool(receipt.get("witness_hash")),
            "grounded": None,
            "grounding": "unverifiable_by_server",
            "artefact": receipt["artefact"],
            "witness_hash": receipt.get("witness_hash", ""),
            "trace_id": receipt.get("trace_id", ""),
            "locked": self._books.is_locked(receipt["agent"]),
            "lock_reason": self._books.lock_reason(receipt["agent"]),
            "seq": sealed["seq"],
            "ts": sealed["ts"],
            "chain_hash": sealed["hash"],
            "prev_hash": sealed["prev_hash"],
            "message": _explain(verdict, receipt, state),
        }

    def settle_refusal(self, refusal: "_Refused") -> Dict[str, Any]:
        """The 423 body for an agent whose book is closed."""
        sealed = refusal.sealed
        return {
            "verdict": REFUSED,
            "agent_id": sealed["agent"],
            "intent": sealed["intent"],
            "check": sealed["check"],
            "witness": sealed["witness"],
            "trust_score": sealed["trust_after"],
            "penalty_state": self._state_of(sealed["agent"]),
            "inflamed": self._state_of(sealed["agent"]) == CRITICAL,
            "penalty": 0.0,
            "charged_as": "",
            "commitment_recorded": bool(sealed.get("witness_hash")),
            "grounded": None,
            "grounding": "unverifiable_by_server",
            "artefact": sealed["artefact"],
            "witness_hash": sealed.get("witness_hash", ""),
            "trace_id": sealed.get("trace_id", ""),
            "locked": True,
            "lock_reason": sealed["reason"],
            "seq": sealed["seq"],
            "ts": sealed["ts"],
            "chain_hash": sealed["hash"],
            "prev_hash": sealed["prev_hash"],
            "message": "拦截：该智能体已被隔离，本次尝试未获记账",
        }

    def falsify(
        self,
        *,
        trace_id: str,
        reason: str,
        evidence: str = "",
        observed_hash: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Overturn a booked PASS after the fact, and charge the liar for it.

        The server could not check the artefact when the claim arrived (see
        ``WitnessHashLimits``), so the claim was booked as a *commitment*. This
        is the other half of that bargain: an observer who later checks the
        physical artefact can report what they found, and the commitment is
        marked false.

        Two properties are load-bearing:

        * **Nothing is rewritten.** The original entry keeps its verdict and its
          chain hash; a ``REFUTED`` entry is appended pointing at
          ``refutes_seq``. Editing the original would break ``verify_chain()``
          and destroy the tamper evidence that makes the ledger worth keeping.
        * **The charge is not proportional.** Trust goes to zero and stays
          there (``PenaltyLedger.record_falsification``). This realizes the R2
          asymmetry: a claim costs one request, a refutation is terminal.

        What this does **not** establish: that the refuter is honest. A
        falsification is a second unverifiable claim, now in the opposite
        direction. The difference is that it is attributable -- it is sealed
        with an observer id and the evidence they report, so a false accusation
        is itself on the record. See ``FalsificationLimits``.

        Raises:
            FalsificationError: 404 unknown trace, 409 already refuted, 422
                self-contradictory payload.
        """
        trace = trace_id.strip() if isinstance(trace_id, str) else ""
        if not trace:
            raise FalsificationError("trace_id must be a non-empty string", 400)
        if reason not in FALSIFY_REASONS:
            raise FalsificationError(
                "reason must be one of {}; got {!r}".format(
                    ", ".join(FALSIFY_REASONS), reason
                ),
                422,
            )
        note = evidence.strip() if isinstance(evidence, str) else ""
        if not note:
            # Not bureaucracy: the evidence string is the only thing a later
            # reviewer has to judge the refutation by. An empty one is an
            # assertion with no way to audit it.
            raise FalsificationError(
                "evidence must say what was checked and what was seen; an "
                "empty refutation is an assertion, not a finding",
                422,
            )

        observed = normalise_witness_hash(observed_hash) if observed_hash else None
        if observed_hash not in (None, "") and observed is None:
            raise FalsificationError(
                "observed_hash must be 64 hex characters or 'sha256:<64 hex>'",
                422,
            )

        with self._lock:
            if trace in self._refuted:
                prior = self._refuted[trace]
                raise FalsificationError(
                    "trace_id {!r} was already refuted at entry {}; a second "
                    "observer adds nothing without new evidence".format(
                        trace, prior["seq"]
                    ),
                    409,
                )

            target = self._find_commitment(trace)
            if target is None:
                raise FalsificationError(
                    "no PASS commitment carries trace_id {!r}; there is nothing "
                    "to refute".format(trace),
                    404,
                )
            if not target.get("witness_hash"):
                raise FalsificationError(
                    "entry {} carries no digest, so it recorded no commitment "
                    "to refute".format(target["seq"]),
                    409,
                )

            committed = str(target["witness_hash"])
            if reason == FALSIFY_HASH_MISMATCH:
                if observed is None:
                    raise FalsificationError(
                        "reason 'hash_mismatch' requires observed_hash: the "
                        "digest actually computed from the artefact",
                        422,
                    )
                if observed == committed:
                    raise FalsificationError(
                        "observed digest equals the committed digest; that "
                        "confirms the entry, it does not refute it",
                        422,
                    )

            agent = str(target["agent"])
            charge = self._books.record_falsification(agent, note)

            sealed = self._append(
                {
                    "agent": agent,
                    "intent": "falsification of entry {}".format(target["seq"]),
                    "check": target["check"],
                    "witness": target["witness"],
                    "verdict": "REFUTED",
                    "artefact": target.get("artefact", ""),
                    "witness_hash": committed,
                    "trace_id": trace,
                    "cost": 0.0,
                    "trust_after": charge["trust_after"],
                    "penalty": charge["penalty"],
                    "charged_as": charge["charged_as"],
                    "book_locked": False,
                    "reason": note,
                    # The pointers that make this a refutation and not a new
                    # verdict: which entry, what was claimed, what was seen.
                    "refutes_seq": target["seq"],
                    "refutes_hash": target["hash"],
                    "falsify_reason": reason,
                    "observed_hash": observed or "",
                    "evidence": note,
                }
            )
            self._refuted[trace] = {
                "seq": sealed["seq"],
                "ts": sealed["ts"],
                "reason": reason,
                "evidence": note,
                "observed_hash": observed or "",
                "refutes_seq": target["seq"],
            }
            self._note_rejection(agent, trace, "commitment refuted after the fact")

            state = self._state_of(agent)
            return {
                "verdict": "REFUTED",
                "refuted": True,
                "trace_id": trace,
                "agent_id": agent,
                "refutes_seq": target["seq"],
                "refutes_hash": target["hash"],
                "reason": reason,
                "evidence": note,
                "observed_hash": observed or "",
                "committed_hash": committed,
                "trust_score": charge["trust_after"],
                "penalty_state": state,
                "inflamed": state == CRITICAL,
                "penalty": charge["penalty"],
                "charged_as": charge["charged_as"],
                "locked": self._books.is_locked(agent),
                "seq": sealed["seq"],
                "ts": sealed["ts"],
                "chain_hash": sealed["hash"],
                "prev_hash": sealed["prev_hash"],
                "message": "证伪：承诺已被物理核对推翻，信任清零",
            }

    def _find_commitment(self, trace: str) -> Optional[Dict[str, Any]]:
        """The most recent PASS entry booked under ``trace``, if any."""
        for entry in reversed(self._entries):
            if entry.get("trace_id") != trace:
                continue
            if entry["verdict"] == PASS:
                return entry
        return None

    def unlock(self, agent_id: str, reason: str = "") -> Dict[str, Any]:
        """Human-supervised release. Unreachable from ``verify`` by design."""
        with self._lock:
            try:
                self._books.unlock(agent_id.strip(), reason)
            except DoubleEntryError as error:
                raise VerifyError(str(error), status_code=409) from None
            sealed = self._append(
                {
                    "agent": agent_id.strip(),
                    "intent": "manual unlock",
                    "check": False,
                    "witness": True,
                    "verdict": "UNLOCK",
                    "artefact": "",
                    "witness_hash": "",
                    "trace_id": "",
                    "cost": 0.0,
                    "trust_after": self._trust_of(agent_id.strip()),
                    "penalty": 0.0,
                    "charged_as": "",
                    "book_locked": False,
                    "reason": reason,
                }
            )
            return {
                "agent_id": agent_id.strip(),
                "locked": False,
                "trust_score": self._trust_of(agent_id.strip()),
                "seq": sealed["seq"],
                "chain_hash": sealed["hash"],
            }

    def declare_lineage(self, agent_id: str, tokens: Sequence[str]) -> Dict[str, Any]:
        """Record which lineage an agent says it descends from.

        Why this exists at all: D5 says six clones are one witness, and the only
        way to know that six agents are clones is to be told what they were built
        from. Nothing else in an entry -- ``intent``, ``artefact``,
        ``witness_hash`` -- names a model, a prompt or a context, so the
        correlation **cannot be inferred from what the ledger already holds**.
        Rather than invent a number, the gateway asks, and answers ``null`` until
        it has been told.

        What this declaration is worth, stated plainly: **it is a claim by
        whoever submits it, and it is not evidence.** Two things follow, and
        both are load-bearing:

        * The incentive is one-directional. Shared lineage *lowers*
          ``n_effective``; distinct lineage *raises* it. So an agent that wants a
          bigger quorum has every reason to declare a token nobody shares.
          Therefore ``n_effective`` is an **upper bound** on independence, never
          a measurement of it -- which is still worth having, because the
          alternative (counting agents) is the same upper bound with no bound at
          all.
        * Silence must not pay. An undeclared agent produces ``n_effective: null``
          rather than a contribution of 1, so a fleet cannot reach the flattering
          number by saying nothing.

        Not sealed onto the chain, deliberately: a chain entry is a commitment
        the system can later check, and there is nothing here to check against.
        Putting it on the chain would make an unverifiable self-description look
        like a witnessed fact -- the exact confusion ``WitnessHashLimits``
        warns about.

        Re-declaring replaces the previous tokens; the latest claim is the one
        the statistic uses, and a history of claims would imply an audit trail
        this does not have.

        Raises:
            VerifyError: empty agent id, empty token list, or a blank token.
        """
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise VerifyError("agent_id must be a non-empty string")
        if isinstance(tokens, str) or not isinstance(tokens, Sequence):
            raise VerifyError(
                "tokens must be a list of lineage tokens, got {!r}. A bare "
                "string would be read one character at a time, and None is an "
                "absence of information rather than a claim of independence."
                .format(type(tokens).__name__)
            )
        cleaned: List[str] = []
        for token in tokens:
            if not isinstance(token, str) or not token.strip():
                raise VerifyError("each lineage token must be a non-empty string")
            cleaned.append(token.strip())
        if not cleaned:
            raise VerifyError(
                "a lineage declaration needs at least one token; an empty one is "
                "not a claim of independence, it is an absence of information"
            )
        unique = tuple(sorted(set(cleaned)))
        with self._lock:
            self._lineage[agent_id.strip()] = unique
        return {"agent_id": agent_id.strip(), "tokens": list(unique)}

    def witness_independence(self, agents: Optional[Sequence[str]] = None) -> Dict[str, Any]:
        """``n_eff = n / (1 + (n-1)rho)`` over the agents in the ecosystem view.

        ``rho`` is the mean pairwise Jaccard overlap of the declared lineage
        tokens, which is the same proxy :func:`lineage_correlation` implements --
        the arithmetic is imported rather than restated, so this cannot drift from
        the law it is supposed to be applying.

        Four statuses, and the ``null`` ones are the point:

        * ``declared`` -- every agent in scope declared a lineage, so ``rho`` can
          be computed. Note the word: declared, because nothing here measured it.
        * ``uncalibrated`` -- nobody declared. No number.
        * ``partial`` -- only some declared. Still no number: averaging a measured
          overlap with an assumed one produces a figure that is precise about the
          wrong thing, and a null is at least honest.
        * ``trivial`` -- fewer than two agents, where ``n/(1+(n-1)rho)`` is ``n``
          for every ``rho``. Arithmetic rather than estimation, so a number is
          reported. ``empty`` covers the no-agents case for the same reason.
        """
        with self._lock:
            if agents is None:
                names = sorted({e.get("agent", "") for e in self._entries if e.get("agent")})
            else:
                names = sorted({str(n).strip() for n in agents if str(n).strip()})
            n_raw = len(names)

            if n_raw == 0:
                return {
                    "n_raw": 0,
                    "n_effective": 0.0,
                    "status": "empty",
                    "rho": None,
                    "reason": "no agents in scope",
                    "source": "declared-lineage",
                    "declared": 0,
                }

            declared = [n for n in names if n in self._lineage]
            if n_raw < 2:
                return {
                    "n_raw": n_raw,
                    "n_effective": float(n_raw),
                    "status": "trivial",
                    "rho": None,
                    "reason": "a single witness is its own effective count",
                    "source": "declared-lineage",
                    "declared": len(declared),
                }

            if len(declared) < n_raw:
                missing = [n for n in names if n not in self._lineage]
                return {
                    "n_raw": n_raw,
                    "n_effective": None,
                    "status": "uncalibrated" if not declared else "partial",
                    "rho": None,
                    "reason": (
                        "correlation uncalibrated: no lineage declared for any agent; "
                        "declare it with POST /api/v1/lineage"
                        if not declared
                        else "correlation uncalibrated: {} of {} agents have no "
                             "declared lineage ({})".format(
                                 len(missing), n_raw, ", ".join(missing[:5])
                             )
                    ),
                    "source": "declared-lineage",
                    "declared": len(declared),
                }

            overlaps: List[float] = []
            for i, a in enumerate(names):
                for b in names[i + 1:]:
                    set_a = set(self._lineage[a])
                    set_b = set(self._lineage[b])
                    union = set_a | set_b
                    rho_pair = lineage_correlation(len(set_a & set_b), len(union))
                    overlaps.append(rho_pair)
            rho = sum(overlaps) / len(overlaps)
            return {
                "n_raw": n_raw,
                "n_effective": round(effective_witnesses(n_raw, rho), 4),
                "status": "declared",
                "rho": round(rho, 6),
                "reason": "computed from declared lineage, not measured from failures",
                "source": "declared-lineage",
                "declared": len(declared),
                "bound": "upper",
            }

    def stats(self) -> Dict[str, Any]:
        """What the dashboard's top row shows. Every number is derived, not kept."""
        with self._lock:
            sheet = self._books.get_balance_sheet()
            totals = sheet["totals"]
            agents = [book["agent"] for book in sheet["books"]]

            trust_scores = [
                float(self._trust[a].trust_score) for a in agents if a in self._trust
            ]
            avg_trust = (
                round(sum(trust_scores) / len(trust_scores), 1)
                if trust_scores
                else round(MAX_TRUST, 1)
            )

            per_agent = []
            inflamed = 0
            locked = 0
            for book in sheet["books"]:
                name = book["agent"]
                state = self._state_of(name)
                is_locked = self._books.is_locked(name)
                # INFLAMED is the immune metaphor for the product: the ledger is
                # CRITICAL *or* the book is closed. Both mean "this agent is a
                # problem right now", which is the question an operator asks.
                is_inflamed = state == CRITICAL or is_locked
                inflamed += 1 if is_inflamed else 0
                locked += 1 if is_locked else 0
                per_agent.append(
                    {
                        "agent_id": name,
                        "trust_score": self._trust_of(name),
                        "penalty_state": state,
                        "inflamed": is_inflamed,
                        "locked": is_locked,
                        "verified": book["verified"],
                        "refuted": book["refuted"],
                        # Retrospective refutations, kept apart from the
                        # request-time contradictions above. An operator asking
                        # "who lied and got caught later" reads this one.
                        "falsified": book.get("falsified", 0),
                        "unwitnessed": book["unwitnessed"],
                        "fail_streak": book["fail_streak"],
                    }
                )

            # A false completion that was caught: claimed success, evidence said
            # otherwise. This is the headline number -- the product's whole pitch
            # is that it is non-zero instead of invisible.
            intercepted = sum(
                1 for e in self._entries if e["verdict"] == FAIL and e["check"] is True
            )
            unwitnessed = sum(
                1 for e in self._entries if e["verdict"] == INVALID and e["check"] is True
            )
            refused = sum(1 for e in self._entries if e["verdict"] == REFUSED)
            passed = sum(1 for e in self._entries if e["verdict"] == PASS)
            refuted_commitments = len(self._refuted)

            worst = HEALTHY
            for name in agents:
                state = self._state_of(name)
                if _STATE_ORDER[state] > _STATE_ORDER[worst]:
                    worst = state

            # D5 on the dashboard. ``n_raw`` is the headcount and
            # ``n_effective`` is what that headcount is worth once declared
            # lineage is accounted for; the two are shown side by side because
            # the gap between them is the finding. ``None`` is a real answer
            # here -- see ``witness_independence`` for why an uncalibrated fleet
            # gets no number rather than the flattering one.
            independence = self.witness_independence(agents)

            return {
                "intercepted_false_completions": intercepted,
                "blocked_unwitnessed": unwitnessed,
                #: Success claims refused at the door for lacking a digest, or
                #: for replaying one. These never became entries, so this is the
                #: one number below that is *kept* rather than derived, and it
                #: is not covered by the hash chain. Read it as a smoke alarm,
                #: not as evidence: an attacker who can restart the process can
                #: clear it, and no verdict was ever recorded against them.
                "rejected_unwitnessed": len(self._rejections),
                "refused_attempts": refused,
                "verified_passes": passed,
                #: PASSes that a later physical check overturned. Reported
                #: alongside the raw count instead of mutating it: "12 passes,
                #: 2 refuted" is the auditable statement, and collapsing it to
                #: "10 passes" would erase the finding.
                "refuted_commitments": refuted_commitments,
                "standing_passes": passed - refuted_commitments,
                "avg_trust_score": avg_trust,
                "inflamed_agents": inflamed,
                "locked_agents": locked,
                "agents_total": len(agents),
                #: D5. The headcount, and what it is worth once lineage is
                #: accounted for. ``agents_total`` is kept as the older name for
                #: the same count; ``n_raw`` is spelled to pair with the line
                #: below it, because a reader who sees one number will assume
                #: they are the same thing.
                "n_raw": independence["n_raw"],
                #: ``None`` = not calibrated, and that is not a bug. An agent
                #: count is a fact; an *effective* count needs to know how
                #: correlated the agents are, and the ledger cannot tell. Filling
                #: in the headcount here would be the six-fold overstatement D5
                #: exists to name.
                "n_effective": independence["n_effective"],
                "independence": independence,
                "health_state": worst,
                "total_entries": len(self._entries),
                "verdicts": {
                    "pass": totals["pass"],
                    "fail": totals["fail"],
                    "invalid": totals["invalid"],
                },
                "chain_head": self._chain_head,
                "uptime_seconds": round(time.time() - self._started_at, 1),
                "core_version": _core_version(),
                "agents": per_agent,
            }

    def ledger(self, limit: int = 50) -> Dict[str, Any]:
        """The newest ``limit`` sealed entries, oldest-first for a terminal feed.

        Each entry is annotated with its refutation, if it has one. The original
        ``verdict`` is *not* rewritten: it is what the system concluded at the
        time, on the evidence it had. ``refuted`` is what a later check found,
        and both belong on the line -- a dashboard that shows only the second
        loses the fact that the system said PASS, which is the part being
        audited.
        """
        with self._lock:
            limit = max(1, min(int(limit), 500))
            window = self._entries[-limit:]
            return {
                "count": len(window),
                "total_entries": len(self._entries),
                "chain_head": self._chain_head,
                "chain_ok": self.verify_chain(),
                "refuted_commitments": len(self._refuted),
                "entries": [
                    # The refutation entry is not itself a refuted commitment --
                    # it *is* the refutation. Annotating it would make the
                    # ledger claim every finding is also a finding against
                    # itself, which also doubled the count on the dashboard.
                    _public(
                        e,
                        None
                        if e["verdict"] == "REFUTED"
                        else self._refuted.get(e.get("trace_id", "")),
                    )
                    for e in window
                ],
            }


class _Refused(Exception):
    """Internal carrier: the book is closed, and the attempt is already sealed."""

    def __init__(self, locked: AgentLocked, sealed: Dict[str, Any]) -> None:
        super().__init__(str(locked))
        self.locked = locked
        self.sealed = sealed


def _public(
    entry: Dict[str, Any], refutation: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """The wire shape of one chained entry. Hashes are kept; `reason` trimmed.

    ``refutation`` is passed in rather than stored on the entry, because the
    entry's body is covered by its chain hash and must not change after sealing.
    """
    wire = {
        "seq": entry["seq"],
        "ts": entry["ts"],
        "agent_id": entry["agent"],
        "intent": entry["intent"],
        "check": entry["check"],
        "witness": entry["witness"],
        "verdict": entry["verdict"],
        "trust_after": entry["trust_after"],
        "penalty": entry["penalty"],
        "charged_as": entry["charged_as"],
        "artefact": entry["artefact"],
        "witness_hash": entry.get("witness_hash", ""),
        "trace_id": entry.get("trace_id", ""),
        "book_locked": entry["book_locked"],
        "reason": entry.get("reason", ""),
        "hash": entry["hash"],
        "prev_hash": entry["prev_hash"],
        "refuted": refutation is not None,
    }
    # A REFUTED entry must keep its pointers back to what it overturns, or the
    # reader has a red line with no referent. These are part of the sealed body,
    # so they are copied through rather than recomputed.
    for key in ("refutes_seq", "refutes_hash", "falsify_reason", "observed_hash"):
        if key in entry:
            wire[key] = entry[key]
    if refutation is not None:
        wire["refuted_at_seq"] = refutation["seq"]
        wire["refuted_ts"] = refutation["ts"]
        wire["refuted_reason"] = refutation["reason"]
        wire["refuted_evidence"] = refutation["evidence"]
        wire["observed_hash"] = refutation["observed_hash"]
    return wire


def _explain(verdict: str, receipt: Dict[str, Any], state: str) -> str:
    """One human sentence, because a badge alone does not teach the rule."""
    if verdict == PASS and receipt["check"] is True:
        return "通过：声明与见证一致，效果已确认落地"
    if verdict == PASS:
        return "通过（诚实失败）：如实报告未完成，不扣信任"
    if verdict == FAIL and receipt["check"] is True:
        return "拦截假完成：声称成功，但见证否认（扣除 25 信任）"
    if verdict == FAIL:
        return "拦截虚报失败：声称未完成，但见证显示效果已发生"
    return "无效：没有提交见证，无法判定（该条目直接锁账）"


def _core_version() -> str:
    try:
        import agent_metabolism  # noqa: PLC0415

        return str(getattr(agent_metabolism, "__version__", "1.0.4"))
    except Exception:  # pragma: no cover - defensive
        return "unknown"
