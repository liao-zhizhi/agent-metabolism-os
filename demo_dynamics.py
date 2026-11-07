"""Numbers for the dynamics whitepaper, computed rather than asserted.

Run::

    python demo_dynamics.py

Every figure printed here is quoted in
``docs/AGENTIC_DYNAMIC_CONTROL_LAWS.md``. If this script and that document ever
disagree, the document is wrong -- which is the only direction that keeps a
whitepaper from becoming the thing it criticises.
"""

from __future__ import annotations

import math

from agent_metabolism.dynamics import (
    AdoptionGate,
    DormancyController,
    Ecosystem,
    GenomeAudit,
    State,
    Trait,
    adoption_horizon,
    audit_policy,
    classify,
    derivative,
    effective_witnesses,
    external_witness_fraction,
    liquidate,
    min_exit_ratio,
    net_assets,
    optimal_sample_convex,
    removal_for_containment,
    reproduction_number,
    solvency_margin,
    spillover,
)
from agent_metabolism.energy import (
    CONSERVE_THRESHOLD,
    DEFAULT_BUDGET,
    EXHAUSTED_THRESHOLD,
)
from agent_metabolism.penalty import (
    AMORTIZATION_RATE,
    CRITICAL_PENALTY,
    MAX_TRUST,
    PENALTY_BASE,
    TRUST_DROP_PER_PENALTY,
    TRUST_RECOVERY,
)
from agent_metabolism.scheduler import DEFAULT_BASE_COST, SLOW_ENERGY_MULTIPLIER

BAR = "=" * 74


def rule(title: str) -> None:
    print("\n" + BAR)
    print(title)
    print(BAR)


def d2_solvency() -> None:
    rule("D2  solvency: min(trust, 100*witness) - liability")
    print("  {:>6} {:>8} {:>10} {:>10} {:>9}".format("trust", "witness", "net", "margin", "verdict"))
    cases = [
        ("honest, one witness", 100.0, 1.0, 0.0),
        ("honest, half witness", 100.0, 0.5, 0.0),
        ("liar, no witness", 100.0, 0.0, 0.0),
        ("liar, one claim out", 100.0, 0.0, 1.0),
        ("liar, three claims out", 100.0, 0.0, 3.0),
        ("witnessed but fined", 60.0, 1.0, 20.0),
    ]
    for label, trust, witness, liability in cases:
        net = net_assets(trust, witness, liability)
        margin = solvency_margin(trust, witness, liability)
        verdict = "solvent" if net >= 0 else "INSOLVENT"
        print("  {:>6.0f} {:>8.2f} {:>10.2f} {:>10.2f} {:>9}  {}".format(
            trust, witness, net, margin, verdict, label))

    print("\n  The same four numbers under a balance-sheet model (assets = trust + 100*witness):")
    for label, trust, witness, liability in cases:
        summed = trust + (100.0 * witness) - liability
        actual = net_assets(trust, witness, liability)
        print("    {:<24} summed {:>8.2f}   min() {:>8.2f}   gap {:>8.2f}".format(
            label, summed, actual, summed - actual))


def d1_alias() -> None:
    rule("D1  aliasing: what a periodic statement cannot see")
    # A priced failure of weight 5 costs 50, and the state is CRITICAL while the
    # fine is at or above 40. Amortisation is 2% per verified pass.
    fine = 5.0 * PENALTY_BASE
    passes = 0
    value = fine
    while value >= CRITICAL_PENALTY:
        value *= 1.0 - AMORTIZATION_RATE
        passes += 1
    print("  false completion: fine {:.0f}, CRITICAL at fine >= {:.0f}".format(
        fine, CRITICAL_PENALTY))
    print("  clean passes for the fine to fall below CRITICAL: {}".format(passes))
    print("  -> a statement sampled less often than every {} passes never shows".format(passes))
    print("     that failure as CRITICAL. The lie is not missed, it is amortised.")

    print("\n  Why the latch exists: the same amortisation applied to a refutation")
    print("  would erase it in the same {} passes. R2 closes that door instead:".format(passes))
    print("    trust  -> 0 (latched, never recovers)")
    print("    fine   -> max(100, 10*10) = 100, and the state stays CRITICAL because")
    print("             state = CRITICAL if penalty >= 40 OR trust <= 50, and trust is 0.")


def d1_integration(steps: int = 240, dt: float = 0.1) -> None:
    rule("D1  aligned integration: where solvency is lost (dt=0.1, 24 cycles)")
    scenarios = {
        "honest":  dict(success_rate=1.0, violation_rate=0.0, severity=1.0,
                        claim_rate=0.2, spend_rate=1.0),
        "careless": dict(success_rate=0.6, violation_rate=0.02, severity=3.0,
                         claim_rate=0.8, spend_rate=1.0),
        "liar":    dict(success_rate=0.0, violation_rate=0.02, severity=5.0,
                        claim_rate=2.0, spend_rate=1.0),
    }
    print("  {:<10} {:>8} {:>8} {:>8} {:>10} {:>10}".format(
        "scenario", "trust", "witness", "liab", "net", "zero-at"))
    for name, flow in scenarios.items():
        s = State(witness=0.0)
        first_negative = None
        t = 0.0
        for i in range(steps):
            d = derivative(s, budget=DEFAULT_BUDGET, recovery_rate=0.05, **flow)
            s = State(
                energy=max(0.0, s.energy + d["energy"] * dt),
                trust=max(0.0, min(MAX_TRUST, s.trust + d["trust"] * dt)),
                witness=max(0.0, s.witness + d["witness"] * dt),
                hallucination=max(0.0, s.hallucination + d["hallucination"] * dt),
            )
            t += dt
            if first_negative is None and s.net() < 0.0:
                first_negative = t
        when = "never" if first_negative is None else "{:.1f}".format(first_negative)
        print("  {:<10} {:>8.2f} {:>8.2f} {:>8.2f} {:>10.2f} {:>10}".format(
            name, s.trust, s.witness, s.hallucination, s.net(), when))

    print("\n  Steady states, analytically (sigma/delta_w, claim/delta_repair):")
    for name, flow in scenarios.items():
        w_ss = flow["success_rate"] / 0.02
        h_ss = flow["claim_rate"] / 0.25
        print("    {:<10} witness_ss = {:>6.1f}   liability_ss = {:>5.1f}".format(
            name, w_ss, h_ss))


def d3_boundary() -> None:
    rule("D3  deadlock: dormancy versus death, and the chatter it prevents")
    print("  classify(energy, trust):")
    for energy, trust, rho, falsified, label in [
        (50.0, 100.0, 0.05, False, "rich"),
        (3.0, 100.0, 0.05, False, "poor, substrate present"),
        (3.0, 100.0, 0.00, False, "poor, substrate gone"),
        (100.0, 0.0, 0.05, True,  "rich but refuted"),
    ]:
        verdict = classify(energy, trust, recovery_rate=rho, falsified=falsified)
        print("    energy {:>5.1f}  trust {:>5.1f}  rho {:.2f}  -> {:<8} {}".format(
            energy, trust, rho, verdict, label))

    print("\n  hysteresis band must fund one cycle  (budget {:.0f}):".format(DEFAULT_BUDGET))
    for label, cost in [
        ("fast beat", DEFAULT_BASE_COST),
        ("slow beat", DEFAULT_BASE_COST * SLOW_ENERGY_MULTIPLIER),
    ]:
        need = min_exit_ratio(EXHAUSTED_THRESHOLD, cost)
        ok = "clears" if need <= CONSERVE_THRESHOLD else "DOES NOT CLEAR"
        print("    {:<10} cost {:>5.1f}  needs exit >= {:.3f}  shipped {:.2f}  -> {}".format(
            label, cost, need, CONSERVE_THRESHOLD, ok))
    print("    -> the first beat after waking must be FAST; that is a scheduler rule,")
    print("       not a suggestion, and it falls out of the band width.")

    controller = DormancyController()
    banded = controller.simulate(9.0, 400)

    budget, cost, recovery = DEFAULT_BUDGET, DEFAULT_BASE_COST, 0.05
    energy, dormant, flips = 9.0, False, 0
    for _ in range(400):
        ratio = energy / budget
        if dormant and ratio >= EXHAUSTED_THRESHOLD:
            dormant, flips = False, flips + 1
        elif not dormant and ratio < EXHAUSTED_THRESHOLD:
            dormant, flips = True, flips + 1
        energy = (min(budget, energy + recovery * (budget - energy)) if dormant
                  else max(0.0, energy - cost))

    print("\n  same 400 cycles, same budget, two controllers:")
    print("    shipped band 0.10/0.30 : {:>4.0f} transitions, {:>3.0f} slept, "
          "final ratio {:.3f}".format(
              banded["transitions"], banded["dormant_cycles"], banded["ratio"]))
    print("    bare comparator 0.10   : {:>4d} transitions, final ratio {:.3f}".format(
        flips, energy / budget))
    print("    ratio of transitions   : {:.1f}x more with no band".format(
        (flips / banded["transitions"]) if banded["transitions"] else float("inf")))

    print("\n  liquidation divides what survives:")
    result = liquidate(State(trust=0.0, witness=2.0, hallucination=5.0, falsified=True),
                      cause="refuted commitment")
    print("    witnesses to the ecosystem : {:.1f}".format(result.witness_survives))
    print("    liability written off      : {:.1f}".format(result.liability_written_off))
    print("    restitutable               : {}".format(result.restitutable))


def d3_wake_window() -> None:
    rule("D3.2  enforcement: first beat after waking is forced FAST")
    from agent_metabolism import (
        DEGRADED_WAKE,
        DualBeatScheduler,
        EnergyAccount,
    )

    def woken(restore_to: float):
        account = EnergyAccount(100.0)
        account.consume(account.current_energy - 3.0)
        account.restore(restore_to - account.current_energy)
        return account

    print("  {:>8} {:>10} {:>12} {:>24} {:>10}".format(
        "wake at", "slow cost", "after slow", "verdict", "mode run"))
    for wake_at in (11.0, 17.0, 40.0, 80.0):
        account = woken(wake_at)
        scheduler = DualBeatScheduler(account)
        after_slow = wake_at - scheduler.slow_cost
        if wake_at < scheduler.fast_cost + account.wake_reserve:
            verdict, mode = "refused: premature", "-"
        elif account.state != "GROW":
            # Below 30% the slow beat was never on offer, so the guard has
            # nothing to intercept and must not claim credit for the answer.
            response = scheduler.execute("prove the invariant", 1.0)
            verdict, mode = "conserve: fast only", response.mode
        else:
            response = scheduler.execute("prove the invariant", 1.0)
            mode = response.mode
            verdict = ("forced fast by wake guard" if response.degraded
                       else "slow allowed")
        print("  {:>8.0f} {:>10.1f} {:>12.1f} {:>24} {:>10}".format(
            wake_at, scheduler.slow_cost, after_slow, verdict, mode))

    print("\n  the same account, before and after the one forced beat (woken at 80%):")
    account = woken(80.0)
    scheduler = DualBeatScheduler(account)
    print("    woke at {:.0f}%, awaiting first beat = {}".format(
        account.current_energy, account.awaiting_first_beat))
    first = scheduler.execute("prove the invariant", 1.0)
    print("    beat 1: {:>5}  reason {:<22} charge {:.0f}".format(
        first.mode, str(first.degraded_reason), account.current_energy))
    second = scheduler.execute("prove the invariant", 1.0)
    print("    beat 2: {:>5}  reason {:<22} charge {:.0f}".format(
        second.mode, str(second.degraded_reason), account.current_energy))
    print("    -> {} is the only degradation that expires by itself".format(DEGRADED_WAKE))


def d4_genetics() -> None:
    rule("D4  heritability: fitness is a product, not a score")
    audit = GenomeAudit(min_external_fraction=0.5)
    genomes = {
        "earned (4 rungs)": [Trait("r%d" % i, True, True) for i in range(4)],
        "forged (20 rungs)": [Trait("f%d" % i, True, False) for i in range(20)],
        "mixed (10/10)": [Trait("m%d" % i, True, i % 2 == 0) for i in range(20)],
        "one broken witness": [Trait("b%d" % i, i != 3, True) for i in range(10)],
    }
    print("  {:<22} {:>6} {:>10} {:>8} {:>9}".format(
        "genome", "gate", "external", "gain", "fitness"))
    for label, traits in genomes.items():
        report = audit.report(traits)
        print("  {:<22} {:>6.1f} {:>10.2f} {:>8.1f} {:>9.1f}   {}".format(
            label, report["gate"], report["external_fraction"],
            report["gain"], report["fitness"],
            "" if report["fitness"] > 0 else "<- fast and worthless"))

    print("\n  adoption hysteresis (margin 10%, refutation latency 90s):")
    horizon = adoption_horizon(90.0, 30.0)
    gate = AdoptionGate(margin=0.10, horizon=horizon)
    trajectory = [3.0, 0.8, 3.2, 3.4, 3.5, 3.6, 3.7]
    print("    generation_time 30s -> horizon {} generations".format(horizon))
    print("    {:>10} {:>8} {:>10}".format("fitness", "streak", "verdict"))
    for value in trajectory:
        verdict = gate.observe(value)
        print("    {:>10.2f} {:>8d} {:>10}".format(value, gate.streak, verdict))
        if verdict == "ADOPT":
            break

    print("\n  how much to verify, under linear audit cost:")
    print("    {:>12} {:>10} {:>12} {:>12}".format("consequence", "base rate", "p*", "expected"))
    for consequence in (1.0, 10.0, 1000.0):
        policy = audit_policy(consequence=consequence, base_rate=0.2, sample_cost=1.0)
        print("    {:>12.0f} {:>10.2f} {:>12.2f} {:>12.2f}".format(
            consequence, 0.2, policy["p"], policy["expected_loss"]))
    print("    -> bang-bang: no interior optimum, so 'spot-check' silently means 0.")
    print("       A middle ground needs bounded consequence: qK/(2*kappa)")
    for kappa, q, K in ((1000.0, 0.5, 1.0), (1.0, 0.5, 1000.0)):
        print("       kappa {:>6.0f} q {:.2f} K {:>5.0f} -> p* = {:.5f}".format(
            kappa, q, K, optimal_sample_convex(kappa, q, K)))


def d5_collusion() -> None:
    rule("D5  independence: a quorum of clones is one witness")
    print("  n_eff = n / (1 + (n-1)rho)")
    print("  {:>5} " .format("n") + " ".join("{:>8}".format("rho=%.2f" % r)
                                            for r in (0.0, 0.25, 0.5, 0.75, 1.0)))
    for n in (2, 3, 6, 12, 100):
        row = "  {:>5} ".format(n)
        row += " ".join("{:>8.2f}".format(effective_witnesses(n, r))
                        for r in (0.0, 0.25, 0.5, 0.75, 1.0))
        print(row)

    eco = Ecosystem()
    names = ["clone%d" % i for i in range(6)]
    for name in names:
        eco.add(name, State())
        for other in names:
            if other != name:
                eco.lineage[(name, other)] = 1.0
    report = eco.collusion_report(names)
    print("\n  six clones claiming a quorum:")
    print("    headcount {:.0f}  rho {:.2f}  effective {:.2f}  overstatement {:.1f}x".format(
        report["headcount"], report["rho"], report["effective"], report["overstatement"]))

    mixed = Ecosystem()
    for i in range(6):
        mixed.add("m%d" % i, State())
    for i in range(6):
        for j in range(i + 1, 6):
            mixed.lineage[("m%d" % i, "m%d" % j)] = 0.25 if i // 3 == j // 3 else 0.0
    mixed_report = mixed.collusion_report(["m%d" % i for i in range(6)])
    print("  six agents in two lineages:")
    print("    rho {:.2f}  effective {:.2f}  overstatement {:.2f}x".format(
        mixed_report["rho"], mixed_report["effective"], mixed_report["overstatement"]))


def d6_contagion() -> None:
    rule("D6  contagion: spillover is bounded exactly up to R0 = 1")
    liability = 10.0
    print("  one inflamed unit carrying {:.0f} units of liability".format(liability))
    print("  {:>8} {:>12} {:>12} {:>10}".format("R0", "spillover", "bounded?", "response"))
    for r0 in (0.0, 0.3, 0.6, 0.8, 0.9, 0.99, 1.0, 1.5, 3.0):
        total = spillover(liability, r0)
        bounded = "yes" if math.isfinite(total) else "NO"
        response = "verify" if r0 < 1.0 else "ISOLATE"
        shown = "{:.2f}".format(total) if math.isfinite(total) else "inf"
        print("  {:>8.2f} {:>12} {:>12} {:>10}".format(r0, shown, bounded, response))

    print("\n  isolation rate needed to reach R0 = 0.9 (beta=0.5, <k>=3):")
    gamma = removal_for_containment(0.5, 3.0, target=0.9)
    print("    gamma = {:.4f}   check R0 = {:.4f}".format(
        gamma, reproduction_number(0.5, 3.0, gamma)))
    print("    vs the naive threshold gamma > beta*<k> = {:.2f} for R0 < 1".format(0.5 * 3.0))


def main() -> None:
    print("agent-metabolism-os -- dynamic control laws, computed figures")
    print("trust/collateral scale: {:.0f}   trust per pass: {:.0f}   "
          "trust per penalty unit: {:.2f}".format(
              MAX_TRUST, TRUST_RECOVERY, TRUST_DROP_PER_PENALTY))
    d2_solvency()
    d1_alias()
    d1_integration()
    d3_boundary()
    d3_wake_window()
    d4_genetics()
    d5_collusion()
    d6_contagion()
    print("\n" + BAR)
    print("Every number above is produced by agent_metabolism.dynamics;")
    print("the whitepaper quotes this output rather than the reverse.")
    print(BAR)


if __name__ == "__main__":
    main()
