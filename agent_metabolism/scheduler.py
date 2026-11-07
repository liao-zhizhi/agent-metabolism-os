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

The first beat after waking is fast, by force
--------------------------------------------
A woken account is not the same machine as one that never slept, even at the same
charge: it has no work in flight, and an expensive first beat spends the reserve
that keeps it awake. With the shipped numbers -- wake at 40, slow beat 35, sleep
floor 10 -- the slow beat is *affordable* and still must not run, because it lands
the account on 5. That is the chatter a hysteresis band exists to prevent, so the
scheduler refuses it here rather than reporting it afterwards.

The fact comes from the account (``EnergyAccount.awaiting_first_beat``), the
policy lives here, and the two are deliberately different causes with different
reason codes: ``DEGRADED_TRUST`` is absorbing, ``DEGRADED_ENERGY`` needs a
restore, and ``DEGRADED_WAKE`` expires on its own after one beat. Collapsing them
would tell an operator to go fix something that fixes itself.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from .energy import EXHAUSTED, GROW, EnergyAccount, EnergyExhausted
from .penalty import CRITICAL as PENALTY_CRITICAL
from .penalty import PenaltyLedger

__all__ = [
    "ADAPTIVE_SURCHARGE",
    "DEGRADED_ENERGY",
    "DEGRADED_TRUST",
    "DEGRADED_WAKE",
    "FAST",
    "FAST_QUALITY",
    "LLMResponse",
    "SLOW",
    "SLOW_COMPLEXITY_THRESHOLD",
    "SLOW_ENERGY_MULTIPLIER",
    "SLOW_LATENCY_MULTIPLIER",
    "SLOW_QUALITY",
    "TIER_ADAPTIVE",
    "TIER_INNATE",
    "TIERS",
    "DualBeatScheduler",
    "WakeWindowError",
]

FAST = "fast"
SLOW = "slow"

OXPHOS = "OXPHOS"
SLP = "SLP"

#: Witness class for a call. Two tiers, because a cheap existence check and a
#: full change-set diff do not cost the same, and a scheduler that billed them
#: identically would make escalation free -- which is to say, would make it a
#: policy nobody has to budget for.
#:
#: The strings match ``audit_sidecar.immune_state.TIER_INNATE`` /
#: ``TIER_ADAPTIVE`` by *contract*, not by import: this package keeps
#: ``dependencies = []`` and never imports the auditor. The agreement is pinned
#: by a test rather than assumed, because two modules independently deciding that
#: "tier2" means the expensive one is exactly the kind of coincidence that
#: silently stops holding.
TIER_INNATE = "tier1"
TIER_ADAPTIVE = "tier2"
TIERS = (TIER_INNATE, TIER_ADAPTIVE)

#: Extra charge for the adaptive check: a ``git diff`` plus a content read.
#: Policies, not measurements -- the same caveat as ``DEFAULT_BASE_COST``.
ADAPTIVE_SURCHARGE = 10.0

# Why a task that wanted the slow beat ran fast. Two distinct causes, kept
# distinct for the same reason EnergyExhausted and InsufficientEnergy are
# different exceptions: "I ran out of joules" and "I am not trusted" call for
# different responses, so collapsing them into one bool forces every caller to
# re-derive the cause from context.
DEGRADED_ENERGY = "insufficient_energy"
DEGRADED_TRUST = "penalty_critical"

#: Third cause, and the one that clears itself: the account had just climbed out
#: of ``EXHAUSTED`` and had not yet paid for a beat. Distinct from the other two
#: because the fix is *nothing* -- wait one beat -- and because it is the only
#: degradation that expires without an operator doing anything. Keeping it
#: separate is what lets an audit tell "degraded for a one-shot reason" from
#: "degraded because the book is poor".
DEGRADED_WAKE = "first_beat_after_wake"

DEFAULT_BASE_COST = 7.0
SLOW_COMPLEXITY_THRESHOLD = 0.7
SLOW_ENERGY_MULTIPLIER = 5.0
SLOW_LATENCY_MULTIPLIER = 2.0

# Simulated quality, not a measurement. It exists so the demo can say what the
# extra joules bought, and it is labelled as simulated wherever it is printed.
FAST_QUALITY = 0.72
SLOW_QUALITY = 0.94


class WakeWindowError(Exception):
    """A slow beat was demanded inside the first-beat window after waking.

    Raised only in ``strict_first_beat`` mode. The default is to degrade and
    record, because a scheduler that throws away a completed task to make a point
    is worse than one that runs the cheap beat and says so. A caller that cannot
    tolerate a fast answer -- a proof search, say -- asks for strict mode and gets
    a failure instead of a quietly inferior result.
    """


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
    #: Witness class this call was billed at, and the part of ``energy_cost``
    #: that bought the adaptive check rather than the answer. Reported
    #: separately so "what did the escalation cost" is answerable from the
    #: record instead of inferred by subtracting two numbers.
    tier: str = TIER_INNATE
    tier_surcharge: float = 0.0

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
        strict_first_beat: bool = False,
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
        self._strict_first_beat = bool(strict_first_beat)

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

    def surcharge_for(self, tier: str) -> float:
        """Extra charge a witness class costs on top of the beat.

        Raises:
            ValueError: ``tier`` is not one of :data:`TIERS`. An unknown tier is
                rejected rather than billed as cheap: a typo must not be able to
                buy the expensive check for free, and it must not silently skip
                a charge the caller believes it is paying.
        """
        if tier not in TIERS:
            raise ValueError(
                "tier must be one of {}, got {!r}".format(TIERS, tier)
            )
        return ADAPTIVE_SURCHARGE if tier == TIER_ADAPTIVE else 0.0

    @property
    def slow_thinking_allowed(self) -> bool:
        """False once trust is bankrupt, whatever the charge says.

        Deliberately binary and deliberately one-directional: a clean ledger
        does not *entitle* the slow beat (the charge and the complexity still
        have to agree), but a ``CRITICAL`` one removes it. Trust cannot buy
        thinking time it never had.
        """
        return self.penalty_state != PENALTY_CRITICAL

    @property
    def strict_first_beat(self) -> bool:
        """Whether a demanded slow beat after waking raises instead of degrading."""
        return self._strict_first_beat

    @property
    def awaiting_first_beat(self) -> bool:
        """D3.2 in one predicate: this account woke and has not yet paid a beat."""
        return self._account.awaiting_first_beat

    # -- execution --------------------------------------------------------

    def execute(
        self,
        prompt: str,
        complexity_score: float,
        *,
        tier: str = TIER_INNATE,
    ) -> LLMResponse:
        """Run one task, choosing the beat and paying for it.

        Args:
            tier: the witness class the caller needs. ``TIER_ADAPTIVE`` adds
                :data:`ADAPTIVE_SURCHARGE` on top of whichever beat runs,
                because the cost of proving what happened is part of what the
                call costs -- not a bookkeeping detail kept outside the account
                that is supposed to be enforcing scarcity.

        Raises:
            ValueError: empty prompt, complexity outside ``[0, 1]``, or an
                unknown tier.
            EnergyExhausted: the account is below the floor, or the chosen beat
                plus its surcharge cannot be paid for.
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
        surcharge = self.surcharge_for(tier)

        state = self._account.state
        if state == EXHAUSTED:
            raise EnergyExhausted(
                "refusing to schedule: account is EXHAUSTED at {:.1f}% of budget. "
                "Restore energy before asking for more work.".format(
                    self._account.ratio * 100.0
                )
            )

        before = self._account.current_energy
        slow_price = self.slow_cost + surcharge
        fast_price = self.fast_cost + surcharge
        wants_slow = complexity > self._slow_threshold and state == GROW
        # Why the slow beat is unavailable, if it is. Checked before the
        # account so that a system which is both poor and distrusted is told
        # the truth about which one is holding it back -- the charge is easy to
        # fix, and naming it wrongly would send the operator to the wrong lever.
        #
        # Order: trust, then the wake window, then the charge. Trust is
        # absorbing, so it is reported first even when a transient condition is
        # also present -- otherwise a woken-up bankrupt system would say
        # "come back next beat" and the bankruptcy would never be named. The
        # wake window sits in the middle because it is real but self-clearing,
        # while a charge shortfall needs a restore.
        blocked_by = None
        if wants_slow:
            if not self.slow_thinking_allowed:
                blocked_by = DEGRADED_TRUST
            elif self._account.awaiting_first_beat:
                blocked_by = DEGRADED_WAKE
            elif not self._account.can_afford(slow_price):
                blocked_by = DEGRADED_ENERGY

        if blocked_by == DEGRADED_WAKE and self._strict_first_beat:
            # Nothing has been spent. A strict caller gets a failure rather than
            # a cheap answer it did not ask for, and the account is untouched so
            # the next call can still run fast.
            raise WakeWindowError(
                "refusing a slow beat on the first beat after waking: the "
                "account left EXHAUSTED at {:.1f}% and has not paid for a beat "
                "yet. The first beat must be fast (D3.2: a band narrower than "
                "one slow cycle would wake, overspend, and re-sleep). Retry "
                "with a task under the slow threshold, or wait one beat."
                .format(self._account.ratio * 100.0)
            )

        # The band must fund the beat it admits. A wake that cannot pay for even
        # the fast beat and stay above the sleep floor is not a wake, it is half
        # of a chatter cycle: run it and the account is EXHAUSTED again on the
        # very next cycle, having bought one answer with the charge it needed to
        # keep running. Refused here rather than after the spend, so nothing is
        # billed for a beat the band cannot hold.
        if self._account.awaiting_first_beat and not self._account.first_beat_clears_band(fast_price):
            raise EnergyExhausted(
                "premature wake: {:.2f} of charge cannot fund a {:.2f} beat and "
                "still clear the {}% sleep floor. A first beat must leave at "
                "least {:.2f} behind; restore to at least {:.2f} first."
                .format(
                    self._account.current_energy,
                    fast_price,
                    self._account.wake_reserve / self._account.budget * 100.0,
                    self._account.wake_reserve,
                    fast_price + self._account.wake_reserve,
                )
            )

        degraded = False
        degraded_reason = None
        if wants_slow and blocked_by is None:
            mode = SLOW
            cost = slow_price
            self._account.consume(cost)
        else:
            # Four ways to arrive here, and they mean different things:
            #   wants_slow is False  -> the task did not deserve the slow beat,
            #                           or the account is conserving charge.
            #   blocked_by TRUST     -> it deserved one and is not trusted with
            #                           it. Also recorded, and also not a debt.
            #   blocked_by WAKE      -> it deserved one on the first beat after
            #                           waking. Recorded, and it expires on its
            #                           own: the next beat may run slow.
            #   blocked_by ENERGY    -> it deserved one and could not pay, so
            #                           the scheduler degrades instead of
            #                           overdrawing. That is recorded.
            degraded = wants_slow
            degraded_reason = blocked_by
            cost = fast_price
            if not self._account.can_afford(cost):
                raise EnergyExhausted(
                    "cannot fund even the fast beat{}: {:.2f} needed, {:.2f} "
                    "remains ({:.1f}% of budget)".format(
                        " plus the adaptive check" if surcharge else "",
                        cost,
                        self._account.current_energy,
                        self._account.ratio * 100.0,
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
            tier=tier,
            tier_surcharge=surcharge,
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
