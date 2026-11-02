"""L1 -- the energy-charge account.

A cell does not decide to have ATP. It has a budget, the budget is spent, and
the price it can still pay determines what it is allowed to do next. An LLM call
has no such thing: asked for a five-thousand-token chain of thought on its last
joule, it will produce one. So the account has to live *outside* the model.

That is the difference between modelling energy and counting tokens. A token
counter is a receipt -- it tells you what was spent after the work is done. An
account is a constraint -- it decides, before the work, whether the expensive
beat may run at all.

States
------
``GROW``       charge above the conserve threshold. Every beat is available.
``CONSERVE``   charge below 30% of budget. High-cost CoT is forbidden; the
               scheduler must fall back to the fast beat.
``EXHAUSTED``  charge below 10% of budget. The account no longer funds work, so
               every spend raises. Recovery is explicit, via ``restore``.

The thresholds are *strict* (``ratio < threshold``), so an account sitting
exactly on 30% is still in ``GROW``. That is deliberate: the boundary is a
description of the charge, not a rounding rule, and a demo can show it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

__all__ = [
    "CONSERVE",
    "CONSERVE_THRESHOLD",
    "DEFAULT_BUDGET",
    "EXHAUSTED",
    "EXHAUSTED_THRESHOLD",
    "GROW",
    "EnergyAccount",
    "EnergyError",
    "EnergyExhausted",
    "InsufficientEnergy",
    "LedgerEntry",
]

GROW = "GROW"
CONSERVE = "CONSERVE"
EXHAUSTED = "EXHAUSTED"

DEFAULT_BUDGET = 100.0
CONSERVE_THRESHOLD = 0.30
EXHAUSTED_THRESHOLD = 0.10

# Floats do not subtract exactly; without a tolerance, "spend exactly what is
# left" would fail on values that differ in the last bit.
_EPSILON = 1e-9


class EnergyError(Exception):
    """Base class for every way the account can refuse a spend."""


class InsufficientEnergy(EnergyError):
    """The cost is larger than the remaining charge. Retry cheaper, or refill."""


class EnergyExhausted(EnergyError):
    """The account is below the exhausted threshold, so it funds nothing.

    Distinct from :class:`InsufficientEnergy` on purpose: that one is a pricing
    problem ("this costs more than I have"), this one is a policy floor ("below
    10% I stop spending altogether, even if a little is left").
    """


@dataclass(frozen=True)
class LedgerEntry:
    """One accepted spend, kept so a run can be audited after the fact."""

    cost: float
    before: float
    after: float
    state: str  # the state the account was in *after* the spend


def _finite(value: object, label: str) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ValueError("{} must be a number, got {!r}".format(label, value)) from None
    if not math.isfinite(number):
        raise ValueError("{} must be finite, got {!r}".format(label, value))
    return number


def _ratio(value: object, label: str) -> float:
    number = _finite(value, label)
    if not 0.0 <= number <= 1.0:
        raise ValueError("{} must lie in [0, 1], got {!r}".format(label, value))
    return number


class EnergyAccount:
    """A finite charge that must be paid before the scheduler may think.

    >>> account = EnergyAccount(100.0)
    >>> account.consume(75.0)
    25.0
    >>> account.state
    'CONSERVE'
    """

    def __init__(
        self,
        budget: float = DEFAULT_BUDGET,
        initial: Optional[float] = None,
        *,
        conserve_threshold: float = CONSERVE_THRESHOLD,
        exhausted_threshold: float = EXHAUSTED_THRESHOLD,
    ) -> None:
        total = _finite(budget, "budget")
        if total <= 0.0:
            raise ValueError("budget must be positive, got {!r}".format(budget))
        start = total if initial is None else _finite(initial, "initial")
        if not 0.0 <= start <= total:
            raise ValueError(
                "initial energy must lie within [0, {!r}], got {!r}".format(total, initial)
            )
        exhausted = _ratio(exhausted_threshold, "exhausted_threshold")
        conserve = _ratio(conserve_threshold, "conserve_threshold")
        if not 0.0 <= exhausted < conserve <= 1.0:
            raise ValueError(
                "thresholds must satisfy 0 <= exhausted < conserve <= 1, "
                "got exhausted={!r} conserve={!r}".format(exhausted, conserve)
            )

        self._budget = total
        self._energy = start
        self._exhausted_threshold = exhausted
        self._conserve_threshold = conserve
        self._ledger: List[LedgerEntry] = []

    # -- read-only view ---------------------------------------------------

    @property
    def budget(self) -> float:
        return self._budget

    @property
    def current_energy(self) -> float:
        return self._energy

    @property
    def ratio(self) -> float:
        """Charge as a fraction of budget, in ``[0, 1]``."""
        return self._energy / self._budget

    @property
    def state(self) -> str:
        """Derived, never stored: no second copy to fall out of sync."""
        ratio = self.ratio
        if ratio < self._exhausted_threshold:
            return EXHAUSTED
        if ratio < self._conserve_threshold:
            return CONSERVE
        return GROW

    @property
    def ledger(self) -> Tuple[LedgerEntry, ...]:
        return tuple(self._ledger)

    @property
    def total_spent(self) -> float:
        return sum(entry.cost for entry in self._ledger)

    def can_afford(self, cost: float) -> bool:
        """Whether ``cost`` may be spent *right now*, floor included."""
        amount = _finite(cost, "cost")
        if amount < 0.0:
            raise ValueError("cost must not be negative, got {!r}".format(cost))
        if amount == 0.0:
            return True
        if self.state == EXHAUSTED:
            return False
        return amount <= self._energy + _EPSILON

    # -- mutation ---------------------------------------------------------

    def consume(self, cost: float) -> float:
        """Spend ``cost`` and return the remaining charge.

        Raises:
            ValueError: negative or non-finite cost.
            EnergyExhausted: the account is already below the floor. Spending
                is refused even when the arithmetic would allow it -- the floor
                is the point.
            InsufficientEnergy: this specific cost is larger than the charge.
        """
        amount = _finite(cost, "cost")
        if amount < 0.0:
            raise ValueError("cost must not be negative, got {!r}".format(cost))
        if amount == 0.0:
            return self._energy

        if self.state == EXHAUSTED:
            raise EnergyExhausted(
                "account is EXHAUSTED at {:.1f}% of budget; refusing to fund "
                "{:.2f} more. Degrade the task or restore energy.".format(
                    self.ratio * 100.0, amount
                )
            )
        if amount > self._energy + _EPSILON:
            raise InsufficientEnergy(
                "cannot pay {:.2f}; {:.2f} remains ({:.1f}% of budget)".format(
                    amount, self._energy, self.ratio * 100.0
                )
            )

        before = self._energy
        self._energy = max(0.0, self._energy - amount)
        self._ledger.append(
            LedgerEntry(cost=amount, before=before, after=self._energy, state=self.state)
        )
        return self._energy

    def restore(self, amount: float) -> float:
        """Add charge, capped at the budget. Returns the new charge."""
        value = _finite(amount, "amount")
        if value < 0.0:
            raise ValueError("amount must not be negative, got {!r}".format(amount))
        self._energy = min(self._budget, self._energy + value)
        return self._energy

    # -- reporting --------------------------------------------------------

    def snapshot(self) -> dict:
        return {
            "budget": self._budget,
            "energy": self._energy,
            "ratio": self.ratio,
            "state": self.state,
            "spent": self.total_spent,
        }

    def __repr__(self) -> str:
        return "EnergyAccount({:.1f}/{:.1f}, state={})".format(
            self._energy, self._budget, self.state
        )
