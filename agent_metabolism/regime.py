"""L3 -- the satiety regime switch.

L1 asks one question: may *this call* afford the expensive beat? L3 asks a
different one: how much of the organism should be running at all? They use
different thresholds on purpose, and the gap is not an oversight.

    L1  < 30% -> CONSERVE, < 10% -> EXHAUSTED     one call, one beat
    L3  > 70% -> GROW, < 30% -> EMERGENCY         concurrency, proliferation

A cell with ample ATP for a single reaction can still be under systemic stress
that makes dividing a bad idea. So the 70% line governs *how many* agents
explore in parallel, while the 30% line governs *whether a single agent* may
think slowly. An account at 65% is legitimately `GROW` for L1 and `CONSERVE`
for L3: the call is affordable, the expansion is not.

Two inputs, not one. Budget alone would keep the system confident while it is
failing: an agent with a full charge and a 60% error rate should contract, not
explore. So the regime is a function of charge *and* recent error feedback, and
the worse of the two decides -- which is why :meth:`evaluate_regime` is a pure
function of its arguments rather than a reading of the account.

A third input, from outside. Both of the above have a bounded memory: the error
window holds eight outcomes and a success resets the critical run, which is the
right behaviour for "is the system steady right now" and the wrong one for "what
has this system cost us". A failure that scrolls out of the window should still
hurt, so the regime also reads a :class:`~agent_metabolism.penalty.PenaltyLedger`
when one is attached. It can only tighten the answer, never soften it: damage
outranks supply, because an account can be refilled and a broken claim cannot.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Deque, Dict, Optional, Tuple
from collections import deque

from .energy import EnergyAccount
from .penalty import (
    CRITICAL as PENALTY_CRITICAL,
    STATES as PENALTY_STATES,
    WARNING as PENALTY_WARNING,
    PenaltyLedger,
)

__all__ = [
    "CONSERVE",
    "CRITICAL_ERROR_LIMIT",
    "EMERGENCY",
    "ERROR_RATE_CONSERVE",
    "GROW",
    "GROW_MIN_RATIO",
    "EMERGENCY_MAX_RATIO",
    "MAX_CONCURRENT_AGENTS",
    "RegimeError",
    "RegimePlan",
    "RegimeSwitch",
]

GROW = "GROW"
CONSERVE = "CONSERVE"
EMERGENCY = "EMERGENCY"

REGIMES: Tuple[str, ...] = (GROW, CONSERVE, EMERGENCY)

# Boundary semantics mirror L1: GROW needs *more than* 70%, EMERGENCY needs
# *less than* 30%. An account sitting exactly on a line belongs to the safer
# side, so the exact value never gets the more expensive treatment.
GROW_MIN_RATIO = 0.70
EMERGENCY_MAX_RATIO = 0.30

# Above this error rate, exploration stops even with a full charge.
ERROR_RATE_CONSERVE = 0.30

# Consecutive *major* failures are their own trigger: two of them are enough to
# declare an emergency regardless of budget, because a system repeating a
# catastrophic mistake is not going to be saved by having joules left.
CRITICAL_ERROR_LIMIT = 2

MAX_CONCURRENT_AGENTS = 5
DEFAULT_ERROR_WINDOW = 20


class RegimeError(ValueError):
    """A regime decision was asked for with inputs that cannot describe one."""


@dataclass(frozen=True)
class RegimePlan:
    """What the chosen regime permits.

    Kept as data, not as ``if regime == ...`` branches scattered through the
    caller: the permissions of a regime should be reviewable in one place.
    """

    regime: str
    max_concurrent_agents: int
    allow_divergent_thinking: bool
    interrupt_long_tasks: bool
    reason: str
    allows_new_writes: bool = True
    penalty_state: Optional[str] = None

    @property
    def is_emergency(self) -> bool:
        return self.regime == EMERGENCY

    def allows(self, requested_agents: int) -> int:
        """Clamp a requested concurrency down to what the regime permits."""
        if isinstance(requested_agents, bool) or not isinstance(requested_agents, int):
            raise TypeError("requested_agents must be an int")
        if requested_agents < 1:
            raise ValueError("requested_agents must be at least 1")
        return min(requested_agents, self.max_concurrent_agents)


def evaluate_regime(
    budget_ratio: float,
    recent_error_rate: float = 0.0,
    critical_errors: int = 0,
    critical_error_limit: int = CRITICAL_ERROR_LIMIT,
    penalty_state: Optional[str] = None,
) -> str:
    """Decide the regime from charge, error feedback and accumulated damage.

    Pure function. Precedence is worst-wins, and the penalty can only *tighten*
    the answer -- it never softens one the energy or error inputs already
    produced:

    1. charge below 30%, or enough consecutive major failures -> ``EMERGENCY``
    2. penalty ``CRITICAL``                                   -> ``EMERGENCY``
    3. charge above 70% with a clean error rate                -> ``GROW``
    4. penalty ``WARNING``                                    -> at best ``CONSERVE``
    5. otherwise                                              -> ``CONSERVE``

    The ordering encodes the thesis: damage outranks supply. A full account
    cannot buy back trust, so a ``CRITICAL`` ledger produces an emergency at
    100% charge. The reverse is never true -- no amount of trust makes a spent
    account richer.

    >>> evaluate_regime(1.0, 0.0, penalty_state="CRITICAL")
    'EMERGENCY'
    >>> evaluate_regime(1.0, 0.0, penalty_state="WARNING")
    'CONSERVE'
    >>> evaluate_regime(0.9, 0.05)
    'GROW'
    """
    ratio = _unit(budget_ratio, "budget_ratio")
    error_rate = _unit(recent_error_rate, "recent_error_rate")
    critical = _count(critical_errors, "critical_errors")
    limit = _count(critical_error_limit, "critical_error_limit")
    if limit < 1:
        raise RegimeError("critical_error_limit must be at least 1")
    if penalty_state is not None and penalty_state not in PENALTY_STATES:
        raise RegimeError(
            "penalty_state must be one of {} or None, got {!r}".format(
                PENALTY_STATES, penalty_state
            )
        )

    # Supply and failure history first.
    regime = CONSERVE
    if ratio < EMERGENCY_MAX_RATIO:
        regime = EMERGENCY
    elif critical >= limit:
        regime = EMERGENCY
    elif ratio > GROW_MIN_RATIO and error_rate <= ERROR_RATE_CONSERVE:
        regime = GROW

    # Damage can only make it worse. Trust bankruptcy stops the system even
    # with a full account, because a system that cannot be believed should not
    # be spending.
    if penalty_state == PENALTY_CRITICAL:
        return EMERGENCY
    if penalty_state == PENALTY_WARNING and regime == GROW:
        return CONSERVE
    return regime


class RegimeSwitch:
    """Tracks error feedback and turns it into a regime, with its permissions.

    The error window is short-term memory: bounded, and reset by success. When a
    ``penalty_ledger`` is supplied it adds long-term memory -- a charge survives
    scrolling out of the window, so an old false completion can still hold the
    system at ``EMERGENCY`` tomorrow morning.

    >>> from agent_metabolism import EnergyAccount
    >>> switch = RegimeSwitch(EnergyAccount(100.0))
    >>> switch.evaluate()
    'GROW'
    >>> switch.feedback(success=False, critical=True)
    >>> switch.feedback(success=False, critical=True)
    >>> switch.evaluate()
    'EMERGENCY'
    """

    def __init__(
        self,
        account: EnergyAccount,
        *,
        error_window: int = DEFAULT_ERROR_WINDOW,
        critical_error_limit: int = CRITICAL_ERROR_LIMIT,
        penalty_ledger: Optional[PenaltyLedger] = None,
    ) -> None:
        if not isinstance(account, EnergyAccount):
            raise TypeError("account must be an EnergyAccount")
        if isinstance(error_window, bool) or not isinstance(error_window, int):
            raise TypeError("error_window must be an int")
        if error_window < 1:
            raise ValueError("error_window must be at least 1")
        if isinstance(critical_error_limit, bool) or not isinstance(critical_error_limit, int):
            raise TypeError("critical_error_limit must be an int")
        if critical_error_limit < 1:
            raise ValueError("critical_error_limit must be at least 1")
        if penalty_ledger is not None and not isinstance(penalty_ledger, PenaltyLedger):
            raise TypeError("penalty_ledger must be a PenaltyLedger")

        self._account = account
        self._window = error_window
        self._critical_limit = critical_error_limit
        self._penalty = penalty_ledger
        self._outcomes: Deque[bool] = deque(maxlen=error_window)
        self._critical_run = 0
        # Last regime we reported, so callers can detect a transition rather
        # than re-reading a value they may already have acted on.
        self._last_regime: Optional[str] = None

    # -- feedback ---------------------------------------------------------

    @property
    def account(self) -> EnergyAccount:
        return self._account

    @property
    def window(self) -> int:
        return self._window

    @property
    def recent_error_rate(self) -> float:
        """Error rate over the last ``window`` outcomes; 0.0 when untested.

        Zero samples means "no evidence of failure", not "perfect health". The
        two are different, and a caller that needs to know should look at
        :attr:`sample_count` rather than treat 0.0 as a guarantee.
        """
        if not self._outcomes:
            return 0.0
        failures = sum(1 for ok in self._outcomes if not ok)
        return failures / len(self._outcomes)

    @property
    def sample_count(self) -> int:
        return len(self._outcomes)

    @property
    def consecutive_critical_errors(self) -> int:
        return self._critical_run

    def feedback(self, success: bool, critical: bool = False) -> None:
        """Record one outcome.

        ``critical`` marks a *major* failure -- wrong output shipped, a
        destructive action, a protocol violation. Any success resets the
        critical run: the counter is for *consecutive* catastrophes, and a
        system that recovers should not be executed for its history.
        """
        if not isinstance(success, bool):
            raise TypeError("success must be a bool")
        if not isinstance(critical, bool):
            raise TypeError("critical must be a bool")
        self._outcomes.append(success)
        if success:
            self._critical_run = 0
        elif critical:
            self._critical_run += 1

    def reset_feedback(self) -> None:
        self._outcomes.clear()
        self._critical_run = 0

    # -- decisions --------------------------------------------------------

    def _compute(
        self,
        budget_ratio: Optional[float] = None,
        recent_error_rate: Optional[float] = None,
        critical_errors: Optional[int] = None,
    ) -> str:
        """Resolve the inputs and evaluate. Never mutates the regime memory."""
        ratio = self._account.ratio if budget_ratio is None else budget_ratio
        error_rate = (
            self.recent_error_rate if recent_error_rate is None else recent_error_rate
        )
        critical = (
            self._critical_run if critical_errors is None else critical_errors
        )
        return evaluate_regime(
            ratio,
            error_rate,
            critical,
            critical_error_limit=self._critical_limit,
            penalty_state=self.penalty_state,
        )

    @property
    def penalty_ledger(self) -> Optional[PenaltyLedger]:
        return self._penalty

    @property
    def penalty_state(self) -> Optional[str]:
        """The ledger's verdict, or ``None`` when no ledger is attached."""
        return None if self._penalty is None else self._penalty.get_state()

    def evaluate(
        self,
        *,
        budget_ratio: Optional[float] = None,
        recent_error_rate: Optional[float] = None,
        critical_errors: Optional[int] = None,
    ) -> str:
        """The current regime, and a note of it as the last observed one.

        Every input can be overridden, which makes a "what if" query possible
        without mutating state -- and without polluting the transition memory,
        because only the parameterless reading is a real observation.
        """
        regime = self._compute(budget_ratio, recent_error_rate, critical_errors)
        self._last_regime = regime
        return regime

    def plan(self, **overrides) -> RegimePlan:
        """The regime plus everything it permits."""
        regime = self.evaluate(**overrides)
        return self._plan_for(regime)

    def _plan_for(self, regime: str) -> RegimePlan:
        penalty_state = self.penalty_state
        if regime == GROW:
            return RegimePlan(
                regime=regime,
                max_concurrent_agents=MAX_CONCURRENT_AGENTS,
                allow_divergent_thinking=True,
                interrupt_long_tasks=False,
                reason="charge {:.0f}%, error rate {:.0f}%: expansion funded".format(
                    self._account.ratio * 100.0, self.recent_error_rate * 100.0
                ),
                allows_new_writes=True,
                penalty_state=penalty_state,
            )
        if regime == CONSERVE:
            if penalty_state == PENALTY_WARNING:
                # Named first: when damage is what closed the door, saying
                # "charge 100%: expansion is not affordable" would be a lie.
                reason = (
                    "penalty ledger is {} (fine {:.1f}, trust {:.0f}): expansion "
                    "refused despite charge {:.0f}%".format(
                        penalty_state,
                        self._penalty.total_penalty,
                        self._penalty.trust_score,
                        self._account.ratio * 100.0,
                    )
                )
            elif self._critical_run:
                reason = "{} consecutive major failure(s)".format(self._critical_run)
            elif self.recent_error_rate > ERROR_RATE_CONSERVE:
                reason = "error rate {:.0f}% over the last {} outcome(s)".format(
                    self.recent_error_rate * 100.0, self.sample_count
                )
            else:
                reason = "charge {:.0f}%: expansion is not affordable".format(
                    self._account.ratio * 100.0
                )
            return RegimePlan(
                regime=regime,
                max_concurrent_agents=1,
                allow_divergent_thinking=False,
                interrupt_long_tasks=False,
                reason=reason,
                allows_new_writes=True,
                penalty_state=penalty_state,
            )

        # EMERGENCY. Trust bankruptcy is reported ahead of a low charge,
        # because a full account that is out of trust is the case worth
        # understanding -- and the one the ledger exists to produce.
        if penalty_state == PENALTY_CRITICAL:
            reason = (
                "penalty ledger is CRITICAL (fine {:.1f}, trust {:.0f}): trust "
                "bankruptcy stops the system with charge at {:.0f}%".format(
                    self._penalty.total_penalty,
                    self._penalty.trust_score,
                    self._account.ratio * 100.0,
                )
            )
        elif self._account.ratio < EMERGENCY_MAX_RATIO:
            reason = "charge {:.0f}% is below the emergency floor".format(
                self._account.ratio * 100.0
            )
        else:
            reason = "{} consecutive major failure(s)".format(self._critical_run)
        return RegimePlan(
            regime=regime,
            max_concurrent_agents=1,
            allow_divergent_thinking=False,
            interrupt_long_tasks=True,
            reason=reason,
            allows_new_writes=False,
            penalty_state=penalty_state,
        )

    def transitioned(self) -> bool:
        """Whether the current regime differs from the last *evaluated* one.

        Computed without updating the memory, so asking the question does not
        answer it. ``evaluate()`` is what commits an observation.
        """
        if self._last_regime is None:
            return False
        return self._compute() != self._last_regime

    def snapshot(self) -> Dict[str, object]:
        plan = self.plan()
        payload: Dict[str, object] = {
            "regime": plan.regime,
            "budget_ratio": self._account.ratio,
            "recent_error_rate": self.recent_error_rate,
            "samples": self.sample_count,
            "consecutive_critical_errors": self._critical_run,
            "max_concurrent_agents": plan.max_concurrent_agents,
            "allow_divergent_thinking": plan.allow_divergent_thinking,
            "interrupt_long_tasks": plan.interrupt_long_tasks,
            "allows_new_writes": plan.allows_new_writes,
            "reason": plan.reason,
        }
        if self._penalty is not None:
            payload["penalty"] = self._penalty.snapshot()
        return payload

    def __repr__(self) -> str:
        return "RegimeSwitch({}, ratio={:.2f}, errors={:.2f})".format(
            self.evaluate(), self._account.ratio, self.recent_error_rate
        )


def _unit(value: object, label: str) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise RegimeError("{} must be a number, got {!r}".format(label, value)) from None
    if not 0.0 <= number <= 1.0:
        raise RegimeError(
            "{} must lie in [0, 1], got {!r}".format(label, value)
        )
    return number


def _count(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RegimeError("{} must be an int, got {!r}".format(label, value))
    if value < 0:
        raise RegimeError("{} must not be negative, got {!r}".format(label, value))
    return value
