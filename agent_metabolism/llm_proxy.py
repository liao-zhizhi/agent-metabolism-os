"""The interface to the model: a charge is paid, a beat is chosen, a record kept.

A probe into an LLM is not an interface. Between the agent and the model there
are three things that must hold, and this class is where all three are enforced
in one place rather than trusted to every call site.

**Potential difference (L1).** The model cannot be asked to think for free. The
charge is paid *before* the call through the account, and a call that cannot be
afforded does not reach the model: it raises ``EnergyExhausted``. There is no
"just this once" path, because the argument for one is always available to the
party that wants the answer.

**Regime (L3).** The scheduler decides the beat from complexity, charge and
trust. This proxy does not overrule it. When the slow beat is deserved and
unaffordable, ``DualBeatScheduler`` degrades to the fast beat and says so in
``degraded_reason``; the proxy surfaces that rather than presenting a cheaper
answer as the one that was asked for. A caller that cannot tell a degraded
answer from a full one will build on the wrong one.

**One-way lock.** Every completed call appends a record to the ledger: beat,
tokens, latency, charge before and after. This is the LLM-shaped hole in the old
trace -- a verdict without a price is a claim nobody can cost. When the record
cannot be written the call is *not* silently returned as if it had been: see
``LedgerLockedError``.

Zero dependencies. The ledger is injected and duck-typed (``.append(...)``), so
this package keeps its ``dependencies = []`` promise; an object that satisfies
the method is enough, and nothing here imports the auditor.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from .energy import EXHAUSTED, EnergyError, EnergyExhausted
from .scheduler import FAST, SLOW, DualBeatScheduler, LLMResponse

__all__ = [
    "LLMProxy",
    "LedgerLockedError",
    "ProxyResult",
    "LLM_BEAT_FAST",
    "LLM_BEAT_SLOW",
    "LLM_RECORD_TYPE",
]

#: Record-type written for a model call. Distinct from ``"call"`` because a
#: model call carries a *cost* and no verdict: nothing was witnessed about it,
#: so calling it a PASS would fabricate a judgement. ``eco_api`` reads these as
#: cost samples for the energy gradient and keeps them out of verdict history.
LLM_RECORD_TYPE = "llm"

LLM_BEAT_FAST = "fast"
LLM_BEAT_SLOW = "slow"


class LedgerLockedError(EnergyError):
    """The call happened, but the record of it could not be written.

    Raised with ``result`` attached so the work is not lost -- but deliberately
    raised, not swallowed. A call that returns its answer without a record is
    indistinguishable from a call that never happened, and the party with the
    most to gain from that ambiguity is the party being charged for it.
    """

    def __init__(self, message: str, result: "ProxyResult") -> None:
        super().__init__(message)
        self.result = result


@dataclass(frozen=True)
class ProxyResult:
    """One model call, with what it cost and whether it was everything asked for."""

    text: str
    mode: str
    beat: str
    tokens_used: int
    latency_ms: float
    energy_before: float
    energy_after: float
    energy_cost: float
    degraded: bool
    degraded_reason: Optional[str]
    quality: float
    record: Optional[Dict[str, Any]] = None

    @property
    def is_slow(self) -> bool:
        """True only when the slow beat actually ran."""
        return self.mode == SLOW

    @property
    def answer_is_full(self) -> bool:
        """False when a degraded fast answer was substituted for a slow one.

        The distinction is the whole point of recording ``degraded``: a cheap
        answer that reads like an expensive one is a false completion waiting to
        happen in the layer above.
        """
        return not self.degraded


class LLMProxy:
    """The only sanctioned route from an agent to a model.

    Args:
        scheduler: the :class:`DualBeatScheduler` that owns the account and the
            beat policy. Required: a proxy without a scheduler would have no
            charge to enforce.
        ledger: anything with ``append(payload, record_type=...)``. When
            missing, calls still run, ``locked`` stays False, and
            :meth:`lock_status` says so -- a documented hole, not a default.
        seal: call ``ledger.seal()`` after each append when the ledger offers
            it. On by default: a head anchor is what makes tail truncation
            detectable, and a ledger that only anchors occasionally leaves the
            last batch unprotected.
    """

    def __init__(
        self,
        scheduler: DualBeatScheduler,
        ledger: Any = None,
        *,
        seal: bool = True,
    ) -> None:
        if not isinstance(scheduler, DualBeatScheduler):
            raise TypeError("scheduler must be a DualBeatScheduler")
        if ledger is not None and not callable(getattr(ledger, "append", None)):
            raise TypeError(
                "ledger must expose append(payload, record_type=...), got {!r}".format(
                    type(ledger).__name__
                )
            )
        self._scheduler = scheduler
        self._ledger = ledger
        self._seal = bool(seal)
        self._calls = 0
        self._unlocked = 0

    # -- read-only view ---------------------------------------------------

    @property
    def scheduler(self) -> DualBeatScheduler:
        return self._scheduler

    @property
    def account(self):
        return self._scheduler.account

    @property
    def calls(self) -> int:
        return self._calls

    @property
    def locked(self) -> bool:
        """True when every completed call has a record."""
        return self._ledger is not None

    def lock_status(self) -> Dict[str, Any]:
        """Whether the one-way lock is actually wired."""
        return {
            "locked": self._ledger is not None,
            "calls": self._calls,
            "unlocked_calls": self._unlocked,
            "reason": (
                "every call is written to the ledger"
                if self._ledger is not None
                else "no ledger injected: calls run unrecorded"
            ),
        }

    # -- the call ---------------------------------------------------------

    def generate(self, prompt: str, complexity_score: float) -> ProxyResult:
        """Pay for one model call, run it on the chosen beat, and record it.

        Raises:
            EnergyExhausted: the account cannot fund even the fast beat, or it
                is already at the exhausted floor. The model is never reached.
            LedgerLockedError: the call ran and its record could not be written;
                the result is carried on the exception.
        """
        # Pre-flight, deliberately duplicated from the scheduler. The scheduler
        # raises on EXHAUSTED anyway, but the message here can name the *gate*
        # that refused, which is what an operator needs at 3am. A duplicate
        # check that can only fire where the other would also fire is cheap.
        account = self._scheduler.account
        if account.state == EXHAUSTED:
            raise EnergyExhausted(
                "refusing to reach the model: the account is EXHAUSTED at "
                "{:.1f}% of budget. Restore energy first.".format(
                    account.ratio * 100.0
                )
            )

        response: LLMResponse = self._scheduler.execute(prompt, complexity_score)
        self._calls += 1

        record = None
        if self._ledger is not None:
            record = self._write(
                {
                    "event": "llm_call",
                    "tool": "llm.generate",
                    "mode": response.mode,
                    "beat": response.beat,
                    "tokens_used": response.tokens_used,
                    "elapsed_ms": response.latency_ms,
                    "energy_before": response.energy_before,
                    "energy_after": response.energy_after,
                    "energy_cost": response.energy_cost,
                    "degraded": response.degraded,
                    "degraded_reason": response.degraded_reason,
                    "account_state": response.state_after,
                }
            )
        else:
            self._unlocked += 1

        return ProxyResult(
            text=response.text,
            mode=response.mode,
            beat=response.beat,
            tokens_used=response.tokens_used,
            latency_ms=response.latency_ms,
            energy_before=response.energy_before,
            energy_after=response.energy_after,
            energy_cost=response.energy_cost,
            degraded=response.degraded,
            degraded_reason=response.degraded_reason,
            quality=response.quality,
            record=record,
        )

    def _write(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Append the record, turning a storage failure into a loud one."""
        try:
            record = self._ledger.append(payload, record_type=LLM_RECORD_TYPE)
        except TypeError:
            # A ledger that does not accept ``record_type`` still satisfies the
            # narrower contract; fall back rather than refusing to record.
            try:
                record = self._ledger.append(payload)
            except Exception as problem:
                raise LedgerLockedError(
                    "the model call completed but its record could not be "
                    "written: {}".format(problem),
                    ProxyResult(
                        text="",
                        mode=FAST,
                        beat="",
                        tokens_used=0,
                        latency_ms=0.0,
                        energy_before=0.0,
                        energy_after=0.0,
                        energy_cost=0.0,
                        degraded=False,
                        degraded_reason=None,
                        quality=0.0,
                    ),
                ) from problem
        except Exception as problem:  # a broken ledger must not look like silence
            raise LedgerLockedError(
                "the model call completed but its record could not be written: "
                "{}".format(problem),
                ProxyResult(
                    text="",
                    mode=FAST,
                    beat="",
                    tokens_used=0,
                    latency_ms=0.0,
                    energy_before=0.0,
                    energy_after=0.0,
                    energy_cost=0.0,
                    degraded=False,
                    degraded_reason=None,
                    quality=0.0,
                ),
            ) from problem

        if self._seal:
            sealer = getattr(self._ledger, "seal", None)
            if callable(sealer):
                try:
                    sealer()
                except Exception:  # pragma: no cover - the record itself stands
                    pass
        return record

    def __repr__(self) -> str:
        return "LLMProxy(calls={}, locked={}, charge={:.1f}%)".format(
            self._calls,
            self._ledger is not None,
            self._scheduler.account.ratio * 100.0,
        )
