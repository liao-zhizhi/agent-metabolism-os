"""L2 -- the dual-beat scheduler.

The model is a CPU: give it a prompt and it computes, with no notion of how much
charge remains. Two beats are available, and they are not interchangeable:

``OXPHOS`` (fast)   one pass, cheap, good enough for most work.
``SLP``    (slow)   five times the energy, twice the wall clock, better output.

Which beat runs is decided *before* the call, from three inputs: how complex the
task is, what the account can afford, and whether the system is still trusted.
A rich account facing a hard problem may think slowly. A poor account may not,
however hard the problem. And an account that has been caught lying may not
either, even at 100% charge -- that is the penalty ledger's grip on this layer,
and it is the reason "how much do I have" and "how much am I owed" are different
questions.

Latency is *modelled*, never slept, so tests stay deterministic and fast. The
returned ``latency_ms`` is what the beat would cost, not what it took.

The punishment makes recovery harder
------------------------------------
Banning the slow beat when trust is broken means the system does its worst work
at the exact moment it most needs to prove itself, which is a genuine trap and
not an accident. It is the point: trust is earned by cheap, checkable work, not
by an expensive essay nobody can verify. Worth knowing that the weights in
``penalty.py`` decide how long the trap lasts, so a severity that keeps the
system in ``CRITICAL`` for a hundred passes is also a decision to keep it
shallow for a hundred passes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from .energy import EXHAUSTED, GROW, EnergyAccount, EnergyExhausted
from .penalty import CRITICAL as PENALTY_CRITICAL
from .penalty import PenaltyLedger

__all__ = [
    "DEGRADED_ENERGY",
    "DEGRADED_TRUST",
    "FAST",
    "FAST_QUALITY",
    "LLMResponse",
    "SLOW",
    "SLOW_COMPLEXITY_THRESHOLD",
    "SLOW_ENERGY_MULTIPLIER",
    "SLOW_LATENCY_MULTIPLIER",
    "SLOW_QUALITY",
    "DualBeatScheduler",
]

FAST = "fast"
SLOW = "slow"

OXPHOS = "OXPHOS"
SLP = "SLP"

# Why a task that wanted the slow beat ran fast. Two distinct causes, kept
# distinct for the same reason EnergyExhausted and InsufficientEnergy are
# different exceptions: "I ran out of joules" and "I am not trusted" call for
# different responses, so collapsing them into one bool forces every caller to
# re-derive the cause from context.
DEGRADED_ENERGY = "insufficient_energy"
DEGRADED_TRUST = "penalty_critical"

DEFAULT_BASE_COST = 7.0
SLOW_COMPLEXITY_THRESHOLD = 0.7
SLOW_ENERGY_MULTIPLIER = 5.0
SLOW_LATENCY_MULTIPLIER = 2.0

# Simulated quality, not a measurement. It exists so the demo can say what the
# extra joules bought, and it is labelled as simulated wherever it is printed.
FAST_QUALITY = 0.72
SLOW_QUALITY = 0.94


@dataclass(frozen=True)
class LLMResponse:
    """What one beat produced, and what it cost to get there."""

    prompt: str
    mode: str          # "fast" or "slow"
    beat: str          # "OXPHOS" or "SLP"
    tokens_used: int
    latency_ms: float
    energy_cost: float
    energy_before: float
    energy_after: float
    state_after: str
    degraded: bool     # wanted SLP, could not have it, ran OXPHOS instead
    quality: float     # simulated
    text: str
    degraded_reason: Optional[str] = None   # DEGRADED_ENERGY / DEGRADED_TRUST

    @property
    def thinking_seconds(self) -> float:
        """Modelled wall clock, in seconds. Nothing actually slept."""
        return self.latency_ms / 1000.0

    @property
    def trust_blocked(self) -> bool:
        """True when the slow beat was withheld because of the ledger."""
        return self.degraded_reason == DEGRADED_TRUST

    @property
    def energy_blocked(self) -> bool:
        """True when the slow beat was withheld because of the account."""
        return self.degraded_reason == DEGRADED_ENERGY


class DualBeatScheduler:
    """Spends an :class:`EnergyAccount` to choose between two beats."""

    def __init__(
        self,
        account: EnergyAccount,
        base_cost: float = DEFAULT_BASE_COST,
        *,
        slow_complexity_threshold: float = SLOW_COMPLEXITY_THRESHOLD,
        slow_energy_multiplier: float = SLOW_ENERGY_MULTIPLIER,
        slow_latency_multiplier: float = SLOW_LATENCY_MULTIPLIER,
        penalty_ledger: Optional[PenaltyLedger] = None,
    ) -> None:
        if not isinstance(account, EnergyAccount):
            raise TypeError("account must be an EnergyAccount")
        cost = _positive(base_cost, "base_cost")
        threshold = _positive(
            slow_complexity_threshold, "slow_complexity_threshold", allow_zero=True
        )
        if threshold > 1.0:
            raise ValueError("slow_complexity_threshold must lie in [0, 1]")
        if penalty_ledger is not None and not isinstance(penalty_ledger, PenaltyLedger):
            raise TypeError("penalty_ledger must be a PenaltyLedger")
        self._account = account
        self._base_cost = cost
        self._slow_threshold = threshold
        self._slow_energy_multiplier = _positive(
            slow_energy_multiplier, "slow_energy_multiplier"
        )
        self._slow_latency_multiplier = _positive(
            slow_latency_multiplier, "slow_latency_multiplier"
        )
        self._penalty = penalty_ledger

    # -- parameters -------------------------------------------------------

    @property
    def account(self) -> EnergyAccount:
        return self._account

    @property
    def base_cost(self) -> float:
        return self._base_cost

    @property
    def slow_threshold(self) -> float:
        return self._slow_threshold

    @property
    def slow_cost(self) -> float:
        return self._base_cost * self._slow_energy_multiplier

    @property
    def fast_cost(self) -> float:
        return self._base_cost

    @property
    def penalty_ledger(self) -> Optional[PenaltyLedger]:
        return self._penalty

    @property
    def penalty_state(self) -> Optional[str]:
        """The ledger's verdict, or ``None`` when no ledger is attached."""
        return None if self._penalty is None else self._penalty.get_state()

    @property
    def slow_thinking_allowed(self) -> bool:
        """False once trust is bankrupt, whatever the charge says.

        Deliberately binary and deliberately one-directional: a clean ledger
        does not *entitle* the slow beat (the charge and the complexity still
        have to agree), but a ``CRITICAL`` one removes it. Trust cannot buy
        thinking time it never had.
        """
        return self.penalty_state != PENALTY_CRITICAL

    # -- execution --------------------------------------------------------

    def execute(self, prompt: str, complexity_score: float) -> LLMResponse:
        """Run one task, choosing the beat and paying for it.

        Raises:
            ValueError: empty prompt, or complexity outside ``[0, 1]``.
            EnergyExhausted: the account is below the floor, or even the fast
                beat cannot be paid for.
        """
        if not isinstance(prompt, str):
            raise TypeError("prompt must be a str, got {!r}".format(type(prompt).__name__))
        if not prompt.strip():
            raise ValueError("prompt must not be empty")
        complexity = _finite(complexity_score, "complexity_score")
        if not 0.0 <= complexity <= 1.0:
            raise ValueError(
                "complexity_score must lie in [0, 1], got {!r}".format(complexity_score)
            )

        state = self._account.state
        if state == EXHAUSTED:
            raise EnergyExhausted(
                "refusing to schedule: account is EXHAUSTED at {:.1f}% of budget. "
                "Restore energy before asking for more work.".format(
                    self._account.ratio * 100.0
                )
            )

        before = self._account.current_energy
        wants_slow = complexity > self._slow_threshold and state == GROW
        # Why the slow beat is unavailable, if it is. Checked before the
        # account so that a system which is both poor and distrusted is told
        # the truth about which one is holding it back -- the charge is easy to
        # fix, and naming it wrongly would send the operator to the wrong lever.
        blocked_by = None
        if wants_slow:
            if not self.slow_thinking_allowed:
                blocked_by = DEGRADED_TRUST
            elif not self._account.can_afford(self.slow_cost):
                blocked_by = DEGRADED_ENERGY

        degraded = False
        degraded_reason = None
        if wants_slow and blocked_by is None:
            mode = SLOW
            cost = self.slow_cost
            self._account.consume(cost)
        else:
            # Three ways to arrive here, and they mean different things:
            #   wants_slow is False  -> the task did not deserve the slow beat,
            #                           or the account is conserving charge.
            #   blocked_by ENERGY    -> it deserved one and could not pay, so
            #                           the scheduler degrades instead of
            #                           overdrawing. That is recorded.
            #   blocked_by TRUST     -> it deserved one and is not trusted with
            #                           it. Also recorded, and also not a debt.
            degraded = wants_slow
            degraded_reason = blocked_by
            cost = self.fast_cost
            if not self._account.can_afford(cost):
                raise EnergyExhausted(
                    "cannot fund even the fast beat: {:.2f} needed, {:.2f} "
                    "remains ({:.1f}% of budget)".format(
                        cost, self._account.current_energy, self._account.ratio * 100.0
                    )
                )
            mode = FAST
            self._account.consume(cost)

        tokens = self._estimate_tokens(prompt, mode)
        latency = self._estimate_latency(self._estimate_tokens(prompt, FAST), mode)
        return LLMResponse(
            prompt=prompt,
            mode=mode,
            beat=SLP if mode == SLOW else OXPHOS,
            tokens_used=tokens,
            latency_ms=latency,
            energy_cost=cost,
            energy_before=before,
            energy_after=self._account.current_energy,
            state_after=self._account.state,
            degraded=degraded,
            quality=SLOW_QUALITY if mode == SLOW else FAST_QUALITY,
            text=self._text(prompt, mode, tokens),
            degraded_reason=degraded_reason,
        )

    # -- modelling --------------------------------------------------------

    def _estimate_tokens(self, prompt: str, mode: str) -> int:
        # Deterministic on purpose: a random simulation would make the demo
        # unreproducible and the tests flaky.
        base = max(8, min(4096, len(prompt) // 4 + 8))
        if mode == SLOW:
            # The slow beat writes out intermediate reasoning, so it emits
            # roughly three times the answer plus a fixed reflection overhead.
            return base * 3 + 16
        return base

    def _estimate_latency(self, fast_tokens: int, mode: str) -> float:
        # Fixed overhead plus per-token time, computed from the fast-beat token
        # count so the slow beat is *exactly* the configured multiple: SLP costs
        # twice the wall clock of the same task on OXPHOS. Deriving it from the
        # slow token count instead would make "doubles" mean nothing.
        fast_latency = 90.0 + 3.0 * fast_tokens
        if mode == SLOW:
            return fast_latency * self._slow_latency_multiplier
        return fast_latency

    def _text(self, prompt: str, mode: str, tokens: int) -> str:
        gist = prompt.strip().replace("\n", " ")
        if len(gist) > 48:
            gist = gist[:45] + "..."
        if mode == SLOW:
            return "SLP ({} tokens of reflection): {}".format(tokens, gist)
        return "OXPHOS: {}".format(gist)

    def __repr__(self) -> str:
        base = "DualBeatScheduler(fast={:.1f}, slow={:.1f}, threshold={:.2f}".format(
            self.fast_cost, self.slow_cost, self._slow_threshold
        )
        if self._penalty is not None:
            base += ", penalty={}".format(self.penalty_state)
        return base + ")"


def _finite(value: object, label: str) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ValueError("{} must be a number, got {!r}".format(label, value)) from None
    if not math.isfinite(number):
        raise ValueError("{} must be finite, got {!r}".format(label, value))
    return number


def _positive(value: object, label: str, allow_zero: bool = False) -> float:
    number = _finite(value, label)
    if allow_zero:
        if number < 0.0:
            raise ValueError("{} must not be negative, got {!r}".format(label, value))
    elif number <= 0.0:
        raise ValueError("{} must be positive, got {!r}".format(label, value))
    return number
