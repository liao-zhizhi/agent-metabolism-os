"""D1--D6 -- dynamics for a streaming agent, derived from the layers below.

The layers in this package answer questions at the *event* scale: may this beat
run (L1), may these two rungs be compiled (interface engine), may this unit keep
its memory (L7). None of them answers the question an operator asks about an
agent running at 10^3 cycles/second: *what is this thing worth, and is it going
to die this afternoon?*

Accounting answers that question with a balance sheet. This module is the
argument that a balance sheet cannot answer it, and the replacement.

Why the replacement is not a metaphor
-------------------------------------
Four properties of the accounting identity fail here, and each failure is a
formula rather than an opinion:

1. **Conservation.** Double entry assumes every debit has a credit, so
   ``A = L + E``. Trust is *created* by a verified crossing and *destroyed* by a
   refutation; witnesses are minted and go stale. That is an open system with a
   source term, ``dx/dt = J_in - J_out + S``, not a closed ledger. A book that
   must balance cannot represent a source, so it books the source as an
   adjustment -- which is exactly where a false completion hides.
2. **Periodicity.** A statement samples the state. A claim and its refutation
   can both complete between two samples, in which case the sampled book shows
   no change and the ``0.98`` amortisation in :mod:`penalty` erases the fine
   before the next sample. The false completion is not merely missed, it is
   *laundered*. This is aliasing, and no reporting frequency fixes it if the
   event rate is higher.
3. **Fungibility.** Accounting values two units of the same asset equally. Two
   witnesses from the same lineage are one witness (D5): ``n_eff = n/(1+(n-1)rho)``
   collapses to 1 at ``rho = 1`` however large ``n`` is.
4. **Monetisability.** A financial liability can be settled by paying it.
   Hallucination cannot: R2 makes a refutation terminal (``trust = 0``, latched,
   never recovered -- see :class:`~agent_metabolism.penalty.PenaltyLedger`). The
   liability is an absorbing state, not a quantity.

The laws
--------
``D1`` supply      :meth:`derivative` -- the four state variables and their flows
``D2`` solvency    :func:`net_assets` -- trust and witness are complements, so
                   they combine by ``min``, never by sum
``D3`` deadlock    :func:`classify` -- dormancy versus death is the difference
                   between "no affordable move" and "no exit edge at all"
``D4`` heritability:class:`GenomeAudit` -- fitness is multiplicative in witness
                   validity, so speed cannot buy off provenance
``D5`` independence:func:`effective_witnesses` -- a quorum of clones is one
                   witness, and the collusion audit looks for exactly that
``D6`` contagion   :func:`spillover` -- one inflamed unit poisons the pool iff
                   ``R0 >= 1``, and the answer to that is a membrane, not more
                   verification

Honest limitation, in the same voice as the rest of the package: the rates here
are policy, not measurement. `SIGMA_MINT`, `DELTA_WITNESS` and the exchange
rates are where a real deployment substitutes its own numbers. What is *not*
policy is the shape of each law -- the ``min`` in D2, the absorbing state in D3,
the product in D4, the harmonic form in D5, the ``1/(1-R0)`` in D6 -- those
follow from the layers below and are stated as such.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .compartment import DEFAULT_ANOMALY_THRESHOLD
from .energy import (
    CONSERVE_THRESHOLD,
    DEFAULT_BUDGET,
    EXHAUSTED_THRESHOLD,
)
from .interface_engine import SOLIDIFIED_COST, VERIFY_COST
from .penalty import (
    MAX_TRUST,
    PENALTY_BASE,
    TRUST_DROP_PER_PENALTY,
    TRUST_RECOVERY,
)

__all__ = [
    "ACTIVE",
    "DEAD",
    "DORMANT",
    "DynamicsError",
    "DormancyController",
    "Ecosystem",
    "GenomeAudit",
    "Liquidation",
    "State",
    "Trait",
    "adoption_horizon",
    "audit_policy",
    "classify",
    "derivative",
    "effective_witnesses",
    "external_witness_fraction",
    "lineage_correlation",
    "liquidate",
    "min_exit_ratio",
    "net_assets",
    "reproduction_number",
    "required_independence",
    "spillover",
    "solvency_margin",
]

ACTIVE = "ACTIVE"
DORMANT = "DORMANT"
DEAD = "DEAD"

#: Trust units one *independent* witness can collateralise. One witness backing
#: one full-trust claim is the unit choice, not a discovery: it says the two are
#: complements at parity. Read ``net_assets`` for why this is a cap and not a
#: weight.
COLLATERAL_PER_WITNESS = MAX_TRUST

#: Witnesses minted per verified crossing. One, because a witness *is* the record
#: of one crossing; minting a fraction would make "how much was verified" a
#: question with a fractional answer.
SIGMA_MINT = 1.0

#: Fraction of the witness stock that goes stale per cycle. Witnesses are bound
#: to a difficulty and a digest scheme (``DIGEST_SCHEME``); both move, so a stock
#: decays even when nothing is refuted. Same role as ``STALE_SUBSTRATE`` in the
#: penalty table.
DELTA_WITNESS = 0.02

#: Fraction of outstanding hallucination liability discharged per cycle, by
#: repairing the claim. Never 1.0: a claim that was made is not unmade.
DELTA_REPAIR = 0.25

_EPS = 1e-12


class DynamicsError(ValueError):
    """A law was asked for inputs on which it is not defined."""


def _finite(value: object, label: str) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise DynamicsError("{} must be a number, got {!r}".format(label, value)) from None
    if not math.isfinite(number):
        raise DynamicsError("{} must be finite, got {!r}".format(label, value))
    return number


def _non_negative(value: object, label: str) -> float:
    number = _finite(value, label)
    if number < 0.0:
        raise DynamicsError("{} must be non-negative, got {!r}".format(label, value))
    return number


def _unit(value: object, label: str) -> float:
    number = _finite(value, label)
    if not 0.0 <= number <= 1.0:
        raise DynamicsError("{} must lie in [0, 1], got {!r}".format(label, value))
    return number


# -- D2: what a thing is worth ---------------------------------------------


def net_assets(
    trust: float,
    witness: float,
    hallucination: float = 0.0,
    *,
    collateral: float = COLLATERAL_PER_WITNESS,
    liability_rate: float = 1.0,
) -> float:
    """``min(trust, collateral * witness) - liability_rate * hallucination``.

    The ``min`` is the whole law, and it is a claim about the world rather than a
    tidy formula: trust and witness are **complements**, so they produce credit
    together or not at all. Summing them is the balance-sheet move, and it prices
    the one failure this system exists to catch:

    >>> net_assets(100.0, 0.0)          # maximum trust, nothing witnessed
    0.0
    >>> net_assets(100.0, 1.0)          # one independent witness
    100.0
    >>> net_assets(100.0, 0.0, 3.0)     # three unwitnessed claims
    -3.0

    A liar at ``trust = 100`` carrying even one unwitnessed claim is already
    insolvent, which is the statement a balance sheet cannot make: it would
    report 100 in assets and the claim as an afterthought. At ``H = 0`` the
    figure is 0 -- the boundary, not a surplus.
    """
    t = _non_negative(trust, "trust")
    w = _non_negative(witness, "witness")
    h = _non_negative(hallucination, "hallucination")
    cap = _non_negative(collateral, "collateral")
    rate = _non_negative(liability_rate, "liability_rate")
    return min(t, cap * w) - rate * h


def solvency_margin(
    trust: float,
    witness: float,
    hallucination: float = 0.0,
    *,
    collateral: float = COLLATERAL_PER_WITNESS,
) -> float:
    """Net assets as a fraction of the credit the trust alone would claim.

    ``1.0`` means fully collateralised; ``0.0`` means the credit is entirely
    unsecured; negative means the book is underwater. Reported as a ratio
    because "how much of your reputation is actually backed" is the question,
    and it is scale-free in a way the absolute figure is not.
    """
    claim = min(_non_negative(trust, "trust"), MAX_TRUST)
    if claim <= _EPS:
        return 0.0
    return net_assets(trust, witness, hallucination, collateral=collateral) / claim


def required_independence(
    trust: float,
    hallucination: float = 0.0,
    *,
    collateral: float = COLLATERAL_PER_WITNESS,
) -> float:
    """Independent witness mass needed for the book to be solvent.

    Solves ``min(T, cW) - L >= 0`` for ``W``, which gives ``W = L/c``. That is
    the solvency constraint stated as a requirement on *evidence*, which is the
    only form an operator can act on: "raise trust" is not a lever, "produce this
    many independent witnesses" is.

    Note the second condition the ``min`` hides: if ``T < L`` the book is
    insolvent at *any* witness mass, because the trust leg is already the binding
    term. :func:`solvency_margin` reports that case; this function reports the
    witness requirement and says nothing about it, which is why the caller is
    expected to read both.

    >>> required_independence(100.0)         # nothing owed
    0.0
    >>> round(required_independence(100.0, 20.0), 3)   # 20 units owed, 100/witness
    0.2
    """
    t = _non_negative(trust, "trust")
    h = _non_negative(hallucination, "hallucination")
    cap = _non_negative(collateral, "collateral")
    if cap <= _EPS:
        raise DynamicsError("collateral must be positive to convert witnesses to credit")
    if h <= _EPS:
        return 0.0
    if t + _EPS < h:
        raise DynamicsError(
            "book is insolvent at any witness mass: trust {:.2f} is below the "
            "liability {:.2f}, so the min() is bound by the trust leg".format(t, h)
        )
    return h / cap


# -- D1: the flows ---------------------------------------------------------


@dataclass(frozen=True)
class State:
    """The four variables a streaming agent is worth knowing about.

    ``witness`` is an *independent* mass, not a count. Feeding it a raw count is
    the collusion error of D5, and :func:`effective_witnesses` is how to convert.
    """

    energy: float = DEFAULT_BUDGET
    trust: float = MAX_TRUST
    witness: float = 1.0
    hallucination: float = 0.0
    falsified: bool = False

    def __post_init__(self) -> None:
        _non_negative(self.energy, "energy")
        _non_negative(self.trust, "trust")
        _non_negative(self.witness, "witness")
        _non_negative(self.hallucination, "hallucination")

    def net(self) -> float:
        return net_assets(self.trust, self.witness, self.hallucination)


def derivative(
    state: State,
    *,
    success_rate: float,
    violation_rate: float,
    severity: float = 1.0,
    claim_rate: float = 0.0,
    spend_rate: float = 0.0,
    budget: float = DEFAULT_BUDGET,
    recovery_rate: float = 0.0,
) -> Dict[str, float]:
    """The continuous form of the ledger recurrences, and of nothing else.

    Each term is the per-cycle update in :mod:`penalty` divided by a cycle, so
    the discrete book is the Euler discretisation of this flow. That is the
    claim being made: this is not a new model of trust, it is the existing one
    written at the rate an event-driven system actually operates.

    With ``sigma`` success rate, ``nu`` violation rate at severity ``w``, base
    ``B``, mint ``m`` and decay ``delta``::

        dT/dt = +recover*sigma - (drop*w*B)*nu
        dW/dt = +m*sigma      - delta_w*W
        dH/dt = +claim_rate   - delta_repair*H
        dE/dt = +recovery*(B-E) - spend_rate

    The trust flow is the paper's ``(eq:trust)`` and ``(eq:recover)`` with the
    step replaced by a rate; the witness flow is new, because the paper has no
    witness stock -- it has one witness per crossing and discards the count.
    """
    sigma = _non_negative(success_rate, "success_rate")
    nu = _non_negative(violation_rate, "violation_rate")
    w = _non_negative(severity, "severity")
    claims = _non_negative(claim_rate, "claim_rate")
    spend = _non_negative(spend_rate, "spend_rate")
    rho = _non_negative(recovery_rate, "recovery_rate")
    b = _finite(budget, "budget")

    d_trust = (
        TRUST_RECOVERY * sigma
        - TRUST_DROP_PER_PENALTY * PENALTY_BASE * w * nu
    )
    d_witness = SIGMA_MINT * sigma - DELTA_WITNESS * state.witness
    d_hallucination = claims - DELTA_REPAIR * state.hallucination
    d_energy = rho * (b - state.energy) - spend

    if state.falsified:
        # R2: a refuted book does not climb. The flow is not zero -- successes
        # still happen -- but none of it buys trust, which is the difference
        # between "stagnant" and "cannot recover".
        d_trust = min(0.0, d_trust)

    return {
        "trust": d_trust,
        "witness": d_witness,
        "hallucination": d_hallucination,
        "energy": d_energy,
        "net": d_trust + COLLATERAL_PER_WITNESS * d_witness - d_hallucination,
    }


# -- D3: dormancy is not death --------------------------------------------


def classify(
    energy: float,
    trust: float,
    *,
    budget: float = DEFAULT_BUDGET,
    min_cycle_cost: float = 7.0,
    recovery_rate: float = 0.05,
    falsified: bool = False,
) -> str:
    """``ACTIVE`` / ``DORMANT`` / ``DEAD`` -- a graph statement, not a threshold.

    The question is not "how low is the charge" but **does a transition out of
    this state exist**:

    * ``ACTIVE``  -- some admissible cycle is affordable right now.
    * ``DORMANT`` -- nothing is affordable, but waiting replenishes the charge,
      so an exit edge exists. The state is poor, not terminal.
    * ``DEAD``    -- no exit edge: either the substrate is gone
      (``recovery_rate = 0``) so waiting cannot help, or the book is refuted and
      R2 makes trust absorbing.

    Collapsing ``DORMANT`` into ``DEAD`` is the accounting habit -- an
    under-capitalised firm is treated as a failed one because the statement has
    no way to say "paused". Collapsing ``DEAD`` into ``DORMANT`` is the more
    expensive mistake: it keeps spending verification on a book that cannot be
    saved, which is what a liar wants.

    >>> classify(50.0, 100.0)
    'ACTIVE'
    >>> classify(3.0, 100.0)
    'DORMANT'
    >>> classify(3.0, 100.0, recovery_rate=0.0)
    'DEAD'
    >>> classify(50.0, 0.0, falsified=True)
    'DEAD'
    """
    e = _non_negative(energy, "energy")
    _non_negative(trust, "trust")
    cost = _non_negative(min_cycle_cost, "min_cycle_cost")
    rho = _non_negative(recovery_rate, "recovery_rate")
    b = _non_negative(budget, "budget")

    if falsified:
        return DEAD
    if e >= cost:
        return ACTIVE
    if rho > 0.0 and e < b:
        return DORMANT
    return DEAD


def min_exit_ratio(
    enter_ratio: float,
    cycle_cost: float,
    *,
    budget: float = DEFAULT_BUDGET,
) -> float:
    """The wake threshold that can actually fund one cycle.

    A hysteresis band narrower than one cycle's cost does not save energy, it
    manufactures a wake-up that immediately starves: the controller exits
    dormancy, cannot pay for the beat, and re-enters. The band must therefore
    exceed the *worst* cycle it will admit, and this returns that requirement as
    a ratio.

    The package's own numbers make the constraint visible. ``enter=0.10`` and a
    default fast beat of 7 on a budget of 100 need ``exit >= 0.17``; the shipped
    ``exit=0.30`` clears it. A *slow* beat multiplies cost by five
    (``SLOW_ENERGY_MULTIPLIER``), so admitting one on wake needs ``0.10 + 0.35 =
    0.45`` -- which the shipped band does not provide. The law's consequence is
    not "raise the threshold", it is "the first beat after waking must be fast",
    and that is a scheduler obligation, testable.

    >>> round(min_exit_ratio(0.10, 7.0), 3)
    0.17
    >>> round(min_exit_ratio(0.10, 35.0), 3)
    0.45
    """
    enter = _unit(enter_ratio, "enter_ratio")
    cost = _non_negative(cycle_cost, "cycle_cost")
    b = _finite(budget, "budget")
    if b <= 0.0:
        raise DynamicsError("budget must be positive, got {!r}".format(budget))
    return enter + cost / b


@dataclass
class DormancyController:
    """Schmitt trigger on the charge, with the band validated at construction.

    ``enter`` must be below ``exit``, and the band must fund one cycle. Both are
    refused at construction rather than discovered as chatter at 3 a.m., because
    a controller that flaps at exactly the boundary spends the energy it was
    built to conserve -- and it does so *silently*, which is the worse half.
    """

    enter_ratio: float = EXHAUSTED_THRESHOLD
    exit_ratio: float = CONSERVE_THRESHOLD
    budget: float = DEFAULT_BUDGET
    cycle_cost: float = 7.0
    recovery_rate: float = 0.05

    def __post_init__(self) -> None:
        enter = _unit(self.enter_ratio, "enter_ratio")
        exit_ = _unit(self.exit_ratio, "exit_ratio")
        cost = _non_negative(self.cycle_cost, "cycle_cost")
        b = _finite(self.budget, "budget")
        if b <= 0.0:
            raise DynamicsError("budget must be positive")
        if not enter < exit_:
            raise DynamicsError(
                "enter_ratio ({}) must be below exit_ratio ({}); equal thresholds "
                "are a bare comparator and will chatter on every cycle it "
                "crosses".format(enter, exit_)
            )
        needed = enter + cost / b
        if exit_ + _EPS < needed:
            raise DynamicsError(
                "band too narrow: exit_ratio {:.3f} cannot fund one cycle of "
                "{:.1f} on a budget of {:.1f} (needs >= {:.3f}). Widen the band "
                "or run a cheaper first beat.".format(exit_, cost, b, needed)
            )
        _non_negative(self.recovery_rate, "recovery_rate")

    def ratio(self, energy: float) -> float:
        return _finite(energy, "energy") / self.budget

    def step(self, energy: float, dormant: bool) -> float:
        """One cycle. Dormant cycles recharge, awake cycles pay.

        The recharge is first-order toward the budget (``rho * (B - E)``), which
        is why it flattens: a nearly empty account recovers fast and a nearly
        full one recovers slowly. A constant recharge rate would make dormancy
        a timer instead of a mechanism.
        """
        e = _non_negative(energy, "energy")
        if dormant:
            return min(self.budget, e + self.recovery_rate * (self.budget - e))
        return max(0.0, e - self.cycle_cost)

    def should_wake(self, energy: float) -> bool:
        return self.ratio(energy) >= self.exit_ratio

    def should_sleep(self, energy: float) -> bool:
        return self.ratio(energy) < self.enter_ratio

    def simulate(self, energy: float, cycles: int, dormant: bool = False) -> Dict[str, float]:
        """Run the trigger and report the chatter, which is the thing measured.

        ``transitions`` is what an energy budget actually pays for; a controller
        reaching the same mean charge with fewer transitions is strictly better
        at the same work, so this is the number to compare.
        """
        n = int(cycles)
        if n < 0:
            raise DynamicsError("cycles must be non-negative, got {!r}".format(cycles))
        e = _non_negative(energy, "energy")
        transitions = 0
        slept = 0
        spent = 0.0
        for _ in range(n):
            if dormant and self.should_wake(e):
                dormant = False
                transitions += 1
            elif not dormant and self.should_sleep(e):
                dormant = True
                transitions += 1
            if dormant:
                slept += 1
            else:
                spent += min(e, self.cycle_cost)
            e = self.step(e, dormant)
        return {
            "energy": e,
            "ratio": e / self.budget,
            "transitions": float(transitions),
            "dormant_cycles": float(slept),
            "spent": spent,
        }


@dataclass(frozen=True)
class Liquidation:
    """What survives a death, and what is written off.

    A cell's ATP is lost when it lyses; its information is not, and neither is
    the debt it owed, which no one can now collect. The same split is load
    bearing here: witnesses belong to the *ecosystem* (they were external checks,
    and their value does not depend on the agent that held them), while
    hallucination liability dies with the debtor and must be recorded as a
    write-off rather than quietly netted off.

    ``cause`` is not decoration. A death with no recorded cause is
    indistinguishable from an agent that simply stopped reporting -- which is the
    failure this whole document is about, arriving by the back door.
    """

    cause: str
    witness_survives: float
    liability_written_off: float
    trust_forfeited: float
    restitutable: bool


def liquidate(state: State, cause: str = "unspecified") -> Liquidation:
    """Close the book. Witnesses transfer; liability is written off, not paid.

    >>> liquidate(State(trust=0.0, witness=2.0, hallucination=5.0, falsified=True)).restitutable
    False
    """
    if not cause or not cause.strip():
        raise DynamicsError("a liquidation needs a cause; a silent death is not one")
    falsified = bool(state.falsified)
    return Liquidation(
        cause=cause,
        witness_survives=state.witness,
        liability_written_off=state.hallucination,
        trust_forfeited=state.trust,
        # A refuted book cannot pay anything back, because what it owes is not
        # money but the truth of its own record. Everything else can.
        restitutable=not falsified,
    )


# -- D4: auditing a genome -------------------------------------------------


@dataclass(frozen=True)
class Trait:
    """One heritable unit: a solidified interface, a rule, a prompt template.

    ``witness_valid`` is whether every witness this trait names still recomputes
    *and* still binds to this gap at this difficulty -- the two checks
    :meth:`FractalEvolver.solidify` already performs. ``externally_witnessed`` is
    the separate question of whether any of that evidence was produced by
    someone other than the agent, which the digest cannot answer.
    """

    name: str
    witness_valid: bool
    externally_witnessed: bool
    verify_cost: float = VERIFY_COST
    replay_cost: float = SOLIDIFIED_COST

    def __post_init__(self) -> None:
        _non_negative(self.verify_cost, "verify_cost")
        _non_negative(self.replay_cost, "replay_cost")
        if self.replay_cost > self.verify_cost + _EPS:
            raise DynamicsError(
                "trait {!r}: replay_cost {} exceeds verify_cost {}; compiling a "
                "rung cannot make it more expensive than checking it".format(
                    self.name, self.replay_cost, self.verify_cost
                )
            )


def external_witness_fraction(traits: Sequence[Trait]) -> float:
    """Share of traits whose evidence was produced by someone else.

    This is the only defence against a forged witness that the digest cannot
    give. ``verify_witness`` checks that a record recomputes from its own
    contents -- a forger who edits the artefact *and* the witness produces a
    self-consistent pair and passes. What a forger cannot manufacture is a check
    it did not run. So the audit does not ask "did this verify", it asks "who ran
    the check", and the fraction is what that question reduces to.

    >>> external_witness_fraction([Trait('a', True, True), Trait('b', True, False)])
    0.5
    """
    items = list(traits)
    if not items:
        return 0.0
    return sum(1 for t in items if t.externally_witnessed) / len(items)


@dataclass
class GenomeAudit:
    """Fitness as a product, so provenance cannot be traded for speed.

    ``cost`` and ``gain`` are the interface engine's own numbers: a compiled rung
    costs ``SOLIDIFIED_COST`` instead of ``VERIFY_COST``, so a genome of ``n``
    rungs is ``100x`` cheaper when compiled. That is the reward evolution is
    chasing, and it is also the reward for deleting the witnesses -- the gain is
    identical whether the compilation was earned or fabricated.

    Hence the multiplicative gate. If fitness were ``gain - penalty`` a mutant
    that dropped its witnesses would win whenever the penalty was small, which
    is the "one lucky PASS" this system refuses to accept. As a product, an
    invalid witness chain zeroes the whole score no matter how fast it is:

    >>> audit = GenomeAudit(min_external_fraction=0.5)
    >>> fast_lie = [Trait('cache', True, False) for _ in range(20)]
    >>> audit.fitness(fast_lie)
    0.0

    The gate is a ``min`` over two *necessary* conditions, the same complement
    structure as D2. That is not a coincidence: credit and provenance both need
    two inputs, and adding them is how a book learns to reward a lie.
    """

    min_external_fraction: float = 0.0

    def __post_init__(self) -> None:
        _unit(self.min_external_fraction, "min_external_fraction")

    def witness_gate(self, traits: Sequence[Trait]) -> float:
        """``1.0`` only if every witness verifies *and* enough are external."""
        items = list(traits)
        if not items:
            return 0.0
        if not all(t.witness_valid for t in items):
            return 0.0
        if external_witness_fraction(items) + _EPS < self.min_external_fraction:
            return 0.0
        return 1.0

    def gain(self, traits: Sequence[Trait]) -> float:
        """Compilation speed-up, with no validity check. Reported for contrast."""
        items = list(traits)
        if not items:
            return 1.0
        cost = sum(t.replay_cost for t in items)
        if cost <= _EPS:
            return float("inf")
        return sum(t.verify_cost for t in items) / cost

    def fitness(self, traits: Sequence[Trait]) -> float:
        return self.witness_gate(traits) * self.gain(traits)

    def report(self, traits: Sequence[Trait]) -> Dict[str, float]:
        return {
            "traits": float(len(traits)),
            "gate": self.witness_gate(traits),
            "external_fraction": external_witness_fraction(traits),
            "gain": self.gain(traits),
            "fitness": self.fitness(traits),
        }


def adoption_horizon(refutation_latency: float, generation_time: float) -> int:
    """Generations a mutant must survive before it may be adopted.

    This is the hysteresis band in *time*, and it is not a policy dial: a mutant
    may be adopted only after the maximum time a refutation takes to arrive,
    because until then "it passed" and "it has not been checked yet" are the same
    observation. If ``refutation_latency`` cannot be bounded, no finite horizon
    is safe and the correct reading is that the trait cannot be solidified at
    all -- see :func:`adoption_is_decidable`.

    >>> adoption_horizon(90.0, 30.0)     # 90s to refute, 30s per generation
    3
    """
    latency = _non_negative(refutation_latency, "refutation_latency")
    gen = _finite(generation_time, "generation_time")
    if gen <= 0.0:
        raise DynamicsError("generation_time must be positive, got {!r}".format(generation_time))
    return int(math.ceil(latency / gen))


def adoption_is_decidable(refutation_latency: float) -> bool:
    """Whether the refutation window is bounded at all.

    An unbounded window -- a claim that might be checked next year, or never --
    makes adoption undecidable rather than slow. Saying so is the difference
    between a law and a wish. ``inf`` is a legitimate answer here and not an
    error: it is exactly the case being described, so this one predicate accepts
    it rather than refusing it the way the arithmetic helpers do.
    """
    try:
        value = float(refutation_latency)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise DynamicsError(
            "refutation_latency must be a number, got {!r}".format(refutation_latency)
        ) from None
    if math.isnan(value):
        raise DynamicsError("refutation_latency must not be NaN")
    return math.isfinite(value) and value >= 0.0


@dataclass
class AdoptionGate:
    """Hold a superior mutant in probation until its refutation window closes.

    Two independent conditions, both necessary: the mutant must beat the
    incumbent by ``margin`` (hysteresis, so noise cannot flip the population),
    and it must do so for ``horizon`` consecutive generations (so a refutation
    has time to arrive). The margin exists because a single lucky PASS is
    exactly a false completion, and evolution run at full speed on one sample is
    the most efficient lie generator there is.
    """

    margin: float = 0.10
    horizon: int = 1
    incumbent: float = 1.0
    streak: int = 0
    rejected: bool = False

    def __post_init__(self) -> None:
        _non_negative(self.margin, "margin")
        if int(self.horizon) < 1:
            raise DynamicsError("horizon must be at least 1 generation")
        self.horizon = int(self.horizon)
        _non_negative(self.incumbent, "incumbent")

    def observe(self, fitness: float) -> str:
        """Return ``ADOPT`` / ``PROBATION`` / ``REJECT`` for one generation."""
        f = _non_negative(fitness, "fitness")
        if self.rejected:
            return "REJECT"
        if f >= self.incumbent * (1.0 + self.margin):
            self.streak += 1
            if self.streak >= self.horizon:
                self.incumbent = f
                self.streak = 0
                return "ADOPT"
            return "PROBATION"
        self.streak = 0
        return "PROBATION"

    def refute(self) -> None:
        """A physical check contradicted a probationary trait: latch the refusal.

        Latched, not scored, for the same reason R2 latches trust: a mutant
        caught forging a witness must not be able to accumulate its way back
        during probation, or the penalty is a delay rather than a cost.
        """
        self.rejected = True
        self.streak = 0


def audit_policy(
    consequence: float,
    base_rate: float,
    sample_cost: float,
    *,
    grid: int = 1001,
) -> Dict[str, float]:
    """How much of a trait population to verify, under linear audit cost.

    Minimise ``p*c + (1-p)*q*K`` over ``p in [0, 1]``, with ``p`` the verified
    share, ``c`` the per-item cost, ``q`` the base rate of forgery and ``K`` the
    loss when one forged trait passes. The objective is linear in ``p``, so there
    is **no interior optimum**: either nothing is verified or everything is. The
    result is uncomfortable and is meant to be:

    >>> audit_policy(consequence=1.0, base_rate=0.5, sample_cost=1.0)["p"]
    0.0
    >>> audit_policy(consequence=1000.0, base_rate=0.5, sample_cost=1.0)["p"]
    1.0

    So "we will spot-check the mutants" is not a policy but a way of choosing the
    left branch without saying so. Sampling only becomes rational when audit cost
    is *convex* -- see :func:`optimal_sample_convex` -- which is to say when
    verification capacity is finite. An operator has to pick which of those two
    worlds they are in, and the law's only demand is that they pick deliberately.
    """
    k = _non_negative(consequence, "consequence")
    q = _unit(base_rate, "base_rate")
    c = _non_negative(sample_cost, "sample_cost")
    n = int(grid)
    if n < 2:
        raise DynamicsError("grid must be at least 2")
    best_p, best_loss = 0.0, float("inf")
    for i in range(n):
        p = i / (n - 1)
        loss = p * c + (1.0 - p) * q * k
        if loss < best_loss - _EPS:
            best_p, best_loss = p, loss
    return {"p": best_p, "expected_loss": best_loss, "rational": best_p > 0.0}


def optimal_sample_convex(
    convexity: float,
    base_rate: float,
    consequence: float,
) -> float:
    """The interior optimum, which exists only when consequence is bounded.

    With convex cost ``c(p) = kappa*p^2``, minimising gives ``p* = qK/(2*kappa)``
    clamped to 1. Interior, therefore, exactly when ``qK < 2*kappa``: a
    consequence small enough that partial verification is worth its crowding.
    For a catastrophic trait the clamp binds and the answer is full
    verification -- the same conclusion as the linear case, reached without
    pretending to a middle ground that does not exist.

    >>> optimal_sample_convex(1000.0, 0.5, 1.0)     # cheap consequence
    0.00025
    >>> optimal_sample_convex(1.0, 0.5, 1000.0)     # catastrophic
    1.0
    """
    kappa = _finite(convexity, "convexity")
    if kappa <= 0.0:
        raise DynamicsError("convexity must be positive, got {!r}".format(convexity))
    q = _unit(base_rate, "base_rate")
    k = _non_negative(consequence, "consequence")
    return max(0.0, min(1.0, q * k / (2.0 * kappa)))


# -- D5: a quorum of clones is one witness ---------------------------------


def effective_witnesses(n: int, rho: float) -> float:
    """``n / (1 + (n-1) * rho)`` -- the design effect, applied to witnesses.

    ``rho`` is the pairwise correlation between witnesses (equicorrelation
    assumed). Two limits carry the law:

    * ``rho = 0``  -> ``n``: independent checks are worth their count.
    * ``rho = 1``  -> ``1``: identical clones are worth *one*, however many.

    That second limit is internal collusion stated exactly. Six agents sharing a
    prompt, a model and a context are not a quorum; they are one witness
    repeated, and a system that counts them at par has a six-fold overstatement
    of its own solvency. This is the same harmonic form as the design effect in
    survey sampling, which is not a coincidence -- both ask how much independent
    information ``n`` correlated measurements carry.

    >>> round(effective_witnesses(6, 0.0), 3)
    6.0
    >>> round(effective_witnesses(6, 1.0), 3)
    1.0
    >>> round(effective_witnesses(6, 0.5), 3)
    1.714
    """
    count = int(n)
    if count < 0:
        raise DynamicsError("n must be non-negative, got {!r}".format(n))
    r = _unit(rho, "rho")
    if count == 0:
        return 0.0
    return count / (1.0 + (count - 1) * r)


def lineage_correlation(shared: float, total: float) -> float:
    """Estimate ``rho`` from overlap: Jaccard over lineage tokens.

    A proxy, and labelled as one. Two agents whose contexts share every token are
    clones (``rho = 1``); disjoint ones are independent (``rho = 0``). Real
    correlation would need the failure modes, which are only observable after a
    failure, so the audit uses the cheap structural proxy and says so -- the
    alternative is a number with a decimal point that pretends to precision it
    does not have.

    >>> lineage_correlation(10, 10)
    1.0
    >>> lineage_correlation(0, 10)
    0.0
    """
    s = _non_negative(shared, "shared")
    t = _finite(total, "total")
    if t <= 0.0:
        raise DynamicsError("total must be positive, got {!r}".format(total))
    return max(0.0, min(1.0, s / t))


@dataclass
class Ecosystem:
    """A membrane graph, and the solvency of a quorum inside it.

    ``agents`` maps a name to its state; ``lineage`` maps a pair to shared
    lineage mass. Independence is computed from the lineage, never assumed from
    the headcount, which is the entire content of D5.
    """

    agents: Dict[str, State] = field(default_factory=dict)
    lineage: Dict[Tuple[str, str], float] = field(default_factory=dict)

    def add(self, name: str, state: State) -> None:
        if not name or not name.strip():
            raise DynamicsError("an agent needs a name")
        self.agents[name] = state

    def mean_correlation(self, members: Optional[Sequence[str]] = None) -> float:
        """Average pairwise lineage correlation among ``members``.

        A quorum with no recorded relation is treated as fully correlated by
        default, which is the conservative direction: assuming independence is
        what turns six clones into a quorum.
        """
        names = list(members if members is not None else self.agents)
        if len(names) < 2:
            return 0.0
        pairs = 0
        total = 0.0
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                total += self.lineage.get((a, b), self.lineage.get((b, a), 1.0))
                pairs += 1
        return total / pairs if pairs else 0.0

    def quorum_witness(self, members: Sequence[str]) -> float:
        """Independent witness mass a quorum can actually claim."""
        return effective_witnesses(len(members), self.mean_correlation(members))

    def collusion_report(self, members: Sequence[str]) -> Dict[str, float]:
        rho = self.mean_correlation(members)
        return {
            "headcount": float(len(members)),
            "rho": rho,
            "effective": self.quorum_witness(members),
            "overstatement": (len(members) / self.quorum_witness(members))
            if members and self.quorum_witness(members) > 0
            else float("inf"),
        }


# -- D6: contagion ---------------------------------------------------------


def reproduction_number(
    transmission: float,
    mean_degree: float,
    removal: float,
) -> float:
    """``R0 = beta * <k> / gamma`` on the membrane graph.

    ``beta`` is the rate at which a false claim is *adopted as a premise* by a
    neighbour, ``<k>`` the mean number of admissible channels per compartment,
    ``gamma`` the rate at which an inflamed unit is removed (isolated or lysed).
    The threshold is 1: below it a single bad unit's influence dies out, above it
    it becomes self-sustaining and no amount of downstream verification stops it,
    because the verifiers are now checking claims derived from it.

    >>> round(reproduction_number(0.1, 3.0, 1.0), 3)
    0.3
    >>> reproduction_number(0.5, 3.0, 1.0)
    1.5
    """
    b = _non_negative(transmission, "transmission")
    k = _non_negative(mean_degree, "mean_degree")
    g = _finite(removal, "removal")
    if g <= 0.0:
        raise DynamicsError("removal must be positive, got {!r}".format(removal))
    return b * k / g


def spillover(liability: float, r0: float) -> float:
    """Total ecosystem liability from one inflamed unit, ``H / (1 - R0)``.

    The expected total progeny of a subcritical branching process. Every claim
    the unit made can be adopted downstream, and each adoption can be adopted
    again; the geometric sum adds ``1/(1-R0)`` to the direct liability. At
    ``R0 -> 1`` the denominator vanishes, which is not an artefact -- it is the
    statement that a critical contagion's total reach has no finite bound.

    >>> spillover(10.0, 0.5)
    20.0
    >>> spillover(10.0, 1.0)
    inf
    """
    h = _non_negative(liability, "liability")
    r = _non_negative(r0, "r0")
    if r >= 1.0:
        return float("inf")
    return h / (1.0 - r)


def containment_required(r0: float) -> bool:
    """Whether the only correct response is a membrane rather than more checking.

    Above threshold, verification is not merely insufficient, it is *unsafe*: it
    spends the ecosystem's remaining credibility confirming claims that inherit
    the infection. The one action that still works is to cut the graph, which
    lowers ``<k>`` and therefore ``R0`` -- the lysed compartment of L7, chosen for
    a reason this law can state.

    >>> containment_required(0.9)
    False
    >>> containment_required(1.2)
    True
    """
    r = _non_negative(r0, "r0")
    return r >= 1.0


def removal_for_containment(transmission: float, mean_degree: float, *, target: float = 0.9) -> float:
    """The isolation rate that brings ``R0`` to ``target``.

    Solves ``beta*<k>/gamma = target``. Returned because "isolate harder" is not
    an instruction an operator can execute, and this is.

    >>> round(removal_for_containment(0.5, 3.0, target=0.9), 4)
    1.6667
    """
    b = _non_negative(transmission, "transmission")
    k = _non_negative(mean_degree, "mean_degree")
    t = _finite(target, "target")
    if not 0.0 < t < 1.0:
        raise DynamicsError("target must lie strictly inside (0, 1); R0 = 1 is the critical point, not a target")
    return b * k / t


#: Consecutive anomalies that lyse a compartment, re-exported so the two
#: thresholds cannot drift apart.
LYSIS_THRESHOLD = DEFAULT_ANOMALY_THRESHOLD
