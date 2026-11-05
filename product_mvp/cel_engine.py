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
  ``artefact`` path it is hashed with ``digest_file`` and recorded, so a
  grounded pass is grounded in a real file and not a sentence.
* State is in memory. Restarting the process clears the books; ``CEL_LEDGER_LOG``
  optionally appends the chain to disk so a restart is auditable, but replaying
  it into live trust is out of scope for the MVP.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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

#: Refusals are not verdicts -- nothing was judged -- but they belong in the
#: audit log all the same, and the UI needs a word for them.
REFUSED = "REFUSED"

#: Worst-wins ordering for the headline health state.
_STATE_ORDER = {HEALTHY: 0, WARNING: 1, CRITICAL: 2}


class VerifyError(Exception):
    """The submission cannot be booked as given (bad types, empty fields)."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


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
    ) -> Dict[str, Any]:
        """Book one entry. Never raises for a *judged* result -- a FAIL is a
        successful audit, not an API error.

        Raises:
            VerifyError: the submission is malformed.
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

        # A supplied path is hashed here rather than taken on trust: a witness
        # whose stated hash is wrong is not a witness that was checked.
        recorded_artefact = artefact if isinstance(artefact, str) else ""
        if recorded_artefact and os.path.isfile(recorded_artefact):
            try:
                recorded_artefact = "{}#{}".format(
                    recorded_artefact, digest_file(recorded_artefact)
                )
            except OSError:
                recorded_artefact = "{}#unreadable".format(recorded_artefact)

        tx = Transaction(
            agent=agent_id.strip(),
            intent=intent.strip(),
            check=check,
            witness=witness,
            artefact=recorded_artefact,
            cost=float(cost),
        )

        with self._lock:
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

            sealed = self._append(receipt)

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
            "grounded": bool(receipt["artefact"]),
            "artefact": receipt["artefact"],
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
            "grounded": bool(sealed["artefact"]),
            "artefact": sealed["artefact"],
            "locked": True,
            "lock_reason": sealed["reason"],
            "seq": sealed["seq"],
            "ts": sealed["ts"],
            "chain_hash": sealed["hash"],
            "prev_hash": sealed["prev_hash"],
            "message": "拦截：该智能体已被隔离，本次尝试未获记账",
        }

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

            worst = HEALTHY
            for name in agents:
                state = self._state_of(name)
                if _STATE_ORDER[state] > _STATE_ORDER[worst]:
                    worst = state

            return {
                "intercepted_false_completions": intercepted,
                "blocked_unwitnessed": unwitnessed,
                "refused_attempts": refused,
                "verified_passes": passed,
                "avg_trust_score": avg_trust,
                "inflamed_agents": inflamed,
                "locked_agents": locked,
                "agents_total": len(agents),
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
        """The newest ``limit`` sealed entries, oldest-first for a terminal feed."""
        with self._lock:
            limit = max(1, min(int(limit), 500))
            window = self._entries[-limit:]
            return {
                "count": len(window),
                "total_entries": len(self._entries),
                "chain_head": self._chain_head,
                "chain_ok": self.verify_chain(),
                "entries": [_public(e) for e in window],
            }


class _Refused(Exception):
    """Internal carrier: the book is closed, and the attempt is already sealed."""

    def __init__(self, locked: AgentLocked, sealed: Dict[str, Any]) -> None:
        super().__init__(str(locked))
        self.locked = locked
        self.sealed = sealed


def _public(entry: Dict[str, Any]) -> Dict[str, Any]:
    """The wire shape of one chained entry. Hashes are kept; `reason` trimmed."""
    return {
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
        "book_locked": entry["book_locked"],
        "reason": entry.get("reason", ""),
        "hash": entry["hash"],
        "prev_hash": entry["prev_hash"],
    }


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
