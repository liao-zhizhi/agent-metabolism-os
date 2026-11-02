"""The penalty ledger -- pain as a currency.

Every other layer in this system measures *supply*: charge left, agents allowed,
context still readable. None of them measures *damage*. A system can sit at 100%
charge with a perfect error window and still have shipped three false
completions, and nothing in L1, L2 or L3 would notice, because none of them is
about what the system has already broken.

This ledger is that memory. It answers one question -- *how much has this system
cost us so far* -- and it answers it in two units that refuse to move together:

``total_penalty``  accumulated cost, in abstract units. Amortises slowly.
``trust_score``    how much benefit of the doubt we still extend. Starts at 100
                   and only a run of verified successes buys it back.

Splitting the two is the point. A single number would have to decide whether a
catastrophe is "paid off" once its fine is amortised, and that is exactly the
wrong question: the fine can be paid, the trust cannot be paid back by
bookkeeping. So damage decays geometrically while trust recovers linearly, and
the asymmetry is deliberate -- a false completion should not be erased by three
good afternoons.

Why the boundaries lean the other way
-------------------------------------
L1's thresholds put the boundary value on the *safe* side (exactly 30% is still
``GROW``), because charge is a resource being consumed and being at the line is
not yet a problem. Here every boundary belongs to the *worse* side: a penalty of
exactly 40.0 is already ``CRITICAL``. Damage is not a resource, and rounding a
damage threshold in the system's own favour is how a ledger stops being a
ledger. The direction is stated in the tests so the difference from L1 is
clearly intentional and not a copy-paste slip.

Honest limitation: the weights are policy, not measurement. Nothing here
computes what a false completion actually costs; it records that one happened
and charges a price the operator chose. That is the same trade as L1's energy
figures, and the same caveat applies -- the numbers are where a real deployment
would put its own.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

__all__ = [
    "AMORTIZATION_RATE",
    "CRITICAL",
    "CRITICAL_PENALTY",
    "CRITICAL_TRUST",
    "DEFAULT_WEIGHTS",
    "HEALTHY",
    "MAX_TRUST",
    "PENALTY_BASE",
    "PenaltyError",
    "PenaltyLedger",
    "TRUST_DROP_PER_PENALTY",
    "TRUST_RECOVERY",
    "UnknownViolationType",
    "Violation",
    "WARNING",
    "WARNING_PENALTY",
    "WARNING_TRUST",
    "state_for",
]

HEALTHY = "HEALTHY"
WARNING = "WARNING"
CRITICAL = "CRITICAL"

STATES: Tuple[str, ...] = (HEALTHY, WARNING, CRITICAL)

# One unit of weight costs this many units of penalty.
PENALTY_BASE = 10.0

# Trust falls with the fine, but not one-for-one: a fine of 50 costs 25 trust.
# Half-weight is deliberate, so that trust is *also* its own signal rather than
# a second readout of the same number.
TRUST_DROP_PER_PENALTY = 0.5

MAX_TRUST = 100.0

# Recovery is slow in both directions on purpose. Two points of trust per
# verified pass, and a fine that decays only 2% per pass, means one false
# completion (fine 50) keeps the system out of CRITICAL for about a dozen
# verified passes and out of WARNING for about sixty. Anything faster and
# "trust bankruptcy" would be a figure of speech: five clean afternoons would
# buy back a shipped lie.
TRUST_RECOVERY = 2.0
AMORTIZATION_RATE = 0.02

# Below this, the tail of an amortising fine is noise; snap it to zero so a
# ledger does not carry 1e-9 of blame forever.
PENALTY_FLOOR = 0.01

# Boundaries belong to the worse side -- see the module docstring.
CRITICAL_PENALTY = 40.0
CRITICAL_TRUST = 50.0
WARNING_PENALTY = 15.0
WARNING_TRUST = 80.0

# Default severity per violation type, so the common cases cannot be billed at
# a guessed price. An unknown type must state its own weight: silently charging
# a default is how a novel failure gets under-reported.
DEFAULT_WEIGHTS: Dict[str, float] = {
    "FALSE_COMPLETION": 5.0,
    "ISOLATION_BREACH": 4.0,
    "UNAUTHORIZED_ACTION": 4.0,
    "UNWITNESSED_WRITE": 3.0,
    "BUDGET_OVERRUN": 2.0,
    "PROTOCOL_VIOLATION": 1.5,
    "STALE_SUBSTRATE": 1.0,
}


class PenaltyError(ValueError):
    """A charge could not be computed from the inputs given."""


class UnknownViolationType(PenaltyError):
    """The type has no default weight, so its severity must be stated."""


@dataclass(frozen=True)
class Violation:
    """One charged offence, kept so the total can be explained later."""

    seq: int
    violation_type: str
    weight: float
    penalty: float
    trust_before: float
    trust_after: float
    state_after: str


def state_for(total_penalty: float, trust_score: float) -> str:
    """Classify a ledger position. Pure, so the boundary is testable on its own.

    >>> state_for(0.0, 100.0)
    'HEALTHY'
    >>> state_for(40.0, 100.0)
    'CRITICAL'
    >>> state_for(20.0, 100.0)
    'WARNING'
    """
    penalty = _finite(total_penalty, "total_penalty")
    trust = _finite(trust_score, "trust_score")
    if penalty < 0.0:
        raise PenaltyError(
            "total_penalty cannot be negative, got {!r}".format(total_penalty)
        )
    if not 0.0 <= trust <= MAX_TRUST:
        raise PenaltyError(
            "trust_score must lie in [0, {!r}], got {!r}".format(MAX_TRUST, trust_score)
        )
    if penalty >= CRITICAL_PENALTY or trust <= CRITICAL_TRUST:
        return CRITICAL
    if penalty >= WARNING_PENALTY or trust <= WARNING_TRUST:
        return WARNING
    return HEALTHY


class PenaltyLedger:
    """Accumulated cost and remaining trust, with a slow, asymmetric recovery.

    >>> ledger = PenaltyLedger()
    >>> ledger.get_state()
    'HEALTHY'
    >>> ledger.record_violation("FALSE_COMPLETION").penalty   # uses weight 5.0
    50.0
    >>> ledger.get_state()
    'CRITICAL'
    >>> ledger.record_success() > 0
    True
    """

    def __init__(
        self,
        *,
        penalty_base: float = PENALTY_BASE,
        initial_trust: float = MAX_TRUST,
    ) -> None:
        base = _positive(penalty_base, "penalty_base")
        trust = _finite(initial_trust, "initial_trust")
        if not 0.0 <= trust <= MAX_TRUST:
            raise PenaltyError(
                "initial_trust must lie in [0, {!r}], got {!r}".format(
                    MAX_TRUST, initial_trust
                )
            )
        self._base = base
        self._trust = trust
        self._penalty = 0.0
        self._violations: List[Violation] = []
        self._successes = 0

    # -- read-only view ---------------------------------------------------

    @property
    def total_penalty(self) -> float:
        return self._penalty

    @property
    def trust_score(self) -> float:
        return self._trust

    @property
    def state(self) -> str:
        return self.get_state()

    @property
    def penalty_base(self) -> float:
        return self._base

    @property
    def violations(self) -> Tuple[Violation, ...]:
        return tuple(self._violations)

    @property
    def success_count(self) -> int:
        return self._successes

    def get_state(self) -> str:
        """``HEALTHY`` / ``WARNING`` / ``CRITICAL`` for the current position."""
        return state_for(self._penalty, self._trust)

    def is_critical(self) -> bool:
        return self.get_state() == CRITICAL

    # -- charging ---------------------------------------------------------

    def resolve_weight(
        self, violation_type: str, weight: Optional[float] = None
    ) -> float:
        """The weight a charge would use, without charging it."""
        if not isinstance(violation_type, str) or not violation_type.strip():
            raise PenaltyError("violation_type must be a non-empty str")
        if weight is None:
            try:
                return DEFAULT_WEIGHTS[violation_type]
            except KeyError:
                raise UnknownViolationType(
                    "no default weight for {!r}; state one explicitly, because "
                    "guessing the severity of a novel failure under-reports it. "
                    "Known types: {}".format(
                        violation_type, ", ".join(sorted(DEFAULT_WEIGHTS))
                    )
                ) from None
        return _positive(weight, "weight")

    def record_violation(self, violation_type: str, weight: Optional[float] = None) -> Violation:
        """Charge one offence and return the record of what it cost.

        ``weight`` defaults to the table entry for a known type; an unknown type
        must supply it, so severity is never silently assumed.
        """
        resolved = self.resolve_weight(violation_type, weight)
        penalty = resolved * self._base
        before = self._trust
        self._penalty += penalty
        self._trust = max(0.0, self._trust - penalty * TRUST_DROP_PER_PENALTY)
        record = Violation(
            seq=len(self._violations) + 1,
            violation_type=violation_type,
            weight=resolved,
            penalty=penalty,
            trust_before=before,
            trust_after=self._trust,
            state_after=self.get_state(),
        )
        self._violations.append(record)
        return record

    # -- recovery ---------------------------------------------------------

    def record_success(self) -> float:
        """A verified pass: buy back a little trust, amortise a little fine.

        Returns the trust after recovery. Deliberately small -- see the module
        docstring for why one good run must not clear a false completion. Note
        the two effects are unequal: trust moves by a fixed step while the fine
        decays by a fraction, so a large fine keeps hurting after trust has
        started to recover.
        """
        self._successes += 1
        self._trust = min(MAX_TRUST, self._trust + TRUST_RECOVERY)
        if self._penalty:
            self._penalty *= 1.0 - AMORTIZATION_RATE
            if self._penalty < PENALTY_FLOOR:
                self._penalty = 0.0
        return self._trust

    # -- reporting --------------------------------------------------------

    def next_charges_until(self, target_state: str) -> int:
        """How many clean passes it would take to reach ``target_state``.

        A projection, not a promise: it assumes no further offences. Provided
        because "how far down am I" is the question an operator actually asks,
        and deriving it by hand from a geometric series is how people misjudge
        the cost of recovery.

        Raises:
            PenaltyError: if ``target_state`` is worse than the current one,
                since successes only ever improve the position.
        """
        if target_state not in STATES:
            raise PenaltyError(
                "target_state must be one of {}, got {!r}".format(STATES, target_state)
            )
        current = self.get_state()
        if STATES.index(target_state) > STATES.index(current):
            raise PenaltyError(
                "recovery cannot reach {!r} from {!r}: successes only improve "
                "the state".format(target_state, current)
            )

        penalty = self._penalty
        trust = self._trust
        needed = 0
        # The horizon is bounded in practice (a geometric decay plus a linear
        # climb), but the loop must not depend on that being true.
        while state_for(penalty, trust) != target_state:
            trust = min(MAX_TRUST, trust + TRUST_RECOVERY)
            if penalty:
                penalty *= 1.0 - AMORTIZATION_RATE
                if penalty < PENALTY_FLOOR:
                    penalty = 0.0
            needed += 1
            if needed > 10000:  # pragma: no cover - unreachable given the above
                raise PenaltyError(
                    "could not reach {!r} within 10000 clean passes".format(target_state)
                )
        return needed

    def snapshot(self) -> Dict[str, object]:
        return {
            "total_penalty": self._penalty,
            "trust_score": self._trust,
            "state": self.get_state(),
            "violations": len(self._violations),
            "successes": self._successes,
            "penalty_base": self._base,
        }

    def __repr__(self) -> str:
        return "PenaltyLedger(penalty={:.2f}, trust={:.1f}, state={})".format(
            self._penalty, self._trust, self.get_state()
        )


def _finite(value: object, label: str) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise PenaltyError(
            "{} must be a number, got {!r}".format(label, value)
        ) from None
    if not math.isfinite(number):
        raise PenaltyError("{} must be finite, got {!r}".format(label, value))
    return number


def _positive(value: object, label: str) -> float:
    number = _finite(value, label)
    if number <= 0.0:
        raise PenaltyError(
            "{} must be positive, got {!r}".format(label, value)
        )
    return number
