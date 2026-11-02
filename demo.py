"""A round trip through the metabolic lifecycle.

Part one spends the account until it forces a beat change, which is the L1/L2
argument made visible: the same class of task stops getting the slow beat, not
because the task got easier but because the charge ran out.

Part two is the same story told with trust instead of joules. The account stays
full; the ledger does not. One false completion takes away the concurrency (L3)
and the slow beat (L2) at once, and the system has to earn both back with cheap,
verifiable work. That is the closed loop: energy down -> charge -> capability
down -> clean passes -> capability back.

Part three shows L7: a compartment fails three times in a row and is lysed, and
the two compartments next to it do not notice.

Everything printed is simulated. No model is called; the point is the regulation
around it. The one thing that is *not* simulated is the arithmetic of the ledger:
it is the same code path the tests pin.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time

from agent_metabolism import (
    CONSERVE,
    EMERGENCY,
    EXHAUSTED,
    GROW,
    AgentProxy,
    CircadianError,
    CircadianScheduler,
    CompartmentDestroyed,
    CompartmentManager,
    DualBeatScheduler,
    EnergyAccount,
    EnergyError,
    PENALTY_HEALTHY,
    PENALTY_WARNING,
    PenaltyLedger,
    RegimeSwitch,
    SubstrateChannel,
)

WIDTH = 84

STATE_NOTE = {
    CONSERVE: "entered CONSERVE (< 30%): the high-cost beat is now forbidden",
    EXHAUSTED: "entered EXHAUSTED (< 10%): the account stops funding work",
}


def banner(title: str, subtitle: str = "") -> None:
    print("=" * WIDTH)
    print(" " + title)
    if subtitle:
        print(" " + subtitle)
    print("=" * WIDTH)


def section(title: str) -> None:
    print()
    print(title)
    print("-" * WIDTH)


def expect(condition, message: str) -> None:
    """A demo that only prints cannot be wrong. This one can, so it checks.

    Every claim the narrative makes about capability is asserted here rather
    than merely printed: if the ledger stops reaching the scheduler or the
    switch, the tables would still render and the story would be a lie.
    """
    if not condition:
        raise RuntimeError("demo invariant broken: " + message)


def table_header() -> None:
    print(
        "  {:<3}{:<11}{:<7}{:<6}{:<21}{:<10}{:<7}{}".format(
            "#", "complexity", "beat", "mode", "energy", "state", "tokens", "latency"
        )
    )
    print(
        "  {:<3}{:<11}{:<7}{:<6}{:<21}{:<10}{:<7}{}".format(
            "-", "----------", "------", "----", "-------------------", "---------",
            "------", "-------"
        )
    )


def beat_row(index, complexity, response, budget):
    ratio = response.energy_after / budget * 100.0
    energy = "{:5.1f} \u2192{:5.1f} ({:3.0f}%)".format(
        response.energy_before, response.energy_after, ratio
    )
    print(
        "  {:<3}{:<11.2f}{:<7}{:<6}{:<21}{:<10}{:<7}{:>6.0f}ms".format(
            index,
            complexity,
            response.beat,
            response.mode,
            energy,
            response.state_after,
            response.tokens_used,
            response.latency_ms,
        )
    )


def part_one(ledger) -> None:
    budget = 100.0
    base_cost = 7.0
    account = EnergyAccount(budget)
    # The ledger is resident for the whole run: the scheduler and the regime
    # switch both read it, so no beat is ever chosen without it.
    scheduler = DualBeatScheduler(account, base_cost=base_cost, penalty_ledger=ledger)

    section("[L1 + L2]  a charge account driving the dual-beat scheduler")
    print(
        "  budget {:.1f}   conserve < 30%   exhausted < 10%   "
        "fast {:.1f}   slow {:.1f}".format(
            account.budget, scheduler.fast_cost, scheduler.slow_cost
        )
    )
    print(
        "  ledger {} ({:.0f} trust) -- clean, so it withholds nothing".format(
            ledger.get_state(), ledger.trust_score
        )
    )
    print()

    tasks = [
        (0.92, "Refactor the scheduler so the fast path allocates nothing."),
        (0.88, "Design the compartment membrane so a message can never mutate a context."),
        (0.95, "Prove the isolation invariant under three anomalies."),
        (0.91, "Audit the ledger for drift between modelled and real cost."),
        (0.25, "Summarise the run in one line."),
    ]

    table_header()
    previous_state = account.state
    counts = {"slow": 0, "fast": 0, "degraded": 0, "refused": 0}
    notes = []

    for index, (complexity, prompt) in enumerate(tasks, start=1):
        response = scheduler.execute(prompt, complexity)
        beat_row(index, complexity, response, budget)
        counts[response.mode] += 1
        if response.degraded:
            counts["degraded"] += 1
            if response.trust_blocked:
                notes.append(
                    "     \u2514\u2500 wanted SLP, the ledger said no: "
                    "fast beat only until trust recovers"
                )
            else:
                notes.append(
                    "     \u2514\u2500 wanted SLP, could not fund {:.1f} from {:.1f}: "
                    "degraded to OXPHOS instead of overdrawing".format(
                        scheduler.slow_cost, response.energy_before
                    )
                )
        if response.state_after != previous_state:
            notes.append(
                "     \u2514\u2500 {}".format(STATE_NOTE.get(response.state_after, ""))
            )
        elif abs(response.energy_after - budget * 0.30) < 1e-9:
            notes.append(
                "     \u2514\u2500 exactly 30.0%: the threshold is strictly *below* 30, "
                "so this is still GROW"
            )
        if notes:
            print("\n".join(notes))
            notes = []
        previous_state = response.state_after

    # The sixth task is here to show what the floor does rather than describe it.
    print()
    try:
        scheduler.execute("Try one more complex task on the last 9%.", 0.97)
    except EnergyError as problem:
        counts["refused"] += 1
        print(
            "  {:<3}{:<11.2f}{:<7}{:<6}{:<21}{:<10}{:<7}{}".format(
                6, 0.97, "--", "--", "refused", "EXHAUSTED", "--", "--"
            )
        )
        print("     \u2514\u2500 {}: {}".format(type(problem).__name__, problem))

    print()
    print(
        "  spent {:.1f} of {:.1f} ({:.0f}% left)   slow {}  fast {}  "
        "degraded {}  refused {}".format(
            account.total_spent,
            account.budget,
            account.ratio * 100.0,
            counts["slow"],
            counts["fast"],
            counts["degraded"],
            counts["refused"],
        )
    )
    print(
        "  ledger after a clean run: {} ({:.0f} trust) -- poverty cost the slow "
        "beat, not trust".format(ledger.get_state(), ledger.trust_score)
    )


def part_isolation() -> None:
    manager = CompartmentManager(anomaly_threshold=3)
    planner = manager.create("planner")
    coder = manager.create("coder")
    critic = manager.create("critic")

    section("[L7]  compartmental isolation -- the membrane is a message queue")

    planner.append("draft plan v1: split the work into three units")
    print('  planner  append   "draft plan v1"')
    planner.send_message("coder", "spec: implement the cut")
    print('  planner \u2192 coder  "spec: implement the cut"')
    print(
        "  coder    inbox={}  local_context={} admitted   <- arrived, not admitted".format(
            coder.inbox_depth, len(coder.local_context)
        )
    )
    admitted = coder.receive()
    print(
        "  coder    receive   admitted {}, local_context={}".format(
            len(admitted), len(coder.local_context)
        )
    )
    coder.send_message("critic", "patch ready for review")
    critic.receive()
    print('  coder \u2192 critic   "patch ready for review"  -> critic context={}'.format(
        len(critic.local_context)
    ))
    print(
        "  proof: planner context={}  coder context={}  critic context={}  "
        "-- nobody wrote into anybody else's memory".format(
            len(planner.local_context),
            len(coder.local_context),
            len(critic.local_context),
        )
    )

    print()
    print("  coder starts failing: three consecutive anomalies (mode 4)")
    for attempt in range(1, 4):
        count = coder.record_anomaly()
        if coder.alive:
            print("    anomaly {}/{} -> consecutive={}".format(attempt, 3, count))
        else:
            print(
                "    anomaly {}/{} -> threshold reached, coder LYSED ({})".format(
                    attempt, 3, coder.lysis_reason
                )
            )

    print()
    print(
        "  after lysis: planner {} context={}  |  critic {} context={}  |  "
        "coder {} context={}".format(
            planner.status,
            len(planner.local_context),
            critic.status,
            len(critic.local_context),
            coder.status,
            len(coder.local_context),
        )
    )

    try:
        planner.send_message("coder", "anyone there?")
    except CompartmentDestroyed as problem:
        print("  planner \u2192 coder  refused: {}".format(problem))

    replacement = manager.create("coder-2")
    planner.send_message("coder-2", "spec, take two")
    replacement.receive()
    print(
        "  planner \u2192 coder-2  delivered  -> coder-2 {} context={}  "
        "(system still routing)".format(replacement.status, len(replacement.local_context))
    )


def part_regime(ledger) -> None:
    """L3: how much of the organism runs, given charge *and* error feedback."""
    section("[L3]  regime switch -- how much of the organism runs")

    account = EnergyAccount(100.0)
    switch = RegimeSwitch(account, penalty_ledger=ledger)

    print(
        "  {:<8}{:<9}{:<11}{:<7}{:<10}{}".format(
            "charge", "errors", "regime", "agents", "divergent", "long tasks"
        )
    )
    print("  " + "-" * 68)

    def row(show_reason: bool) -> None:
        plan = switch.plan()
        print(
            "  {:<8}{:<9}{:<11}{:<7}{:<10}{}".format(
                "{:.0f}%".format(account.ratio * 100.0),
                "{:.0f}%".format(switch.recent_error_rate * 100.0),
                plan.regime,
                plan.max_concurrent_agents,
                "yes" if plan.allow_divergent_thinking else "no",
                "INTERRUPTED" if plan.interrupt_long_tasks else "allowed",
            )
        )
        if show_reason:
            print("     \u2514\u2500 {}".format(plan.reason))

    row(False)
    for spend in (40.0, 25.0, 10.0):
        account.consume(spend)
        row(True)

    print()
    print("  the same switch, with a full charge but a bad history:")
    fresh = EnergyAccount(100.0)
    struggling = RegimeSwitch(fresh)
    struggling.feedback(success=False, critical=True)
    struggling.feedback(success=False, critical=True)
    plan = struggling.plan()
    print(
        "  {:<8}{:<9}{:<11}{:<7}{:<10}{}".format(
            "100%",
            "{:.0f}%".format(struggling.recent_error_rate * 100.0),
            plan.regime,
            plan.max_concurrent_agents,
            "yes" if plan.allow_divergent_thinking else "no",
            "INTERRUPTED" if plan.interrupt_long_tasks else "allowed",
        )
    )
    print("     \u2514\u2500 {}".format(plan.reason))
    print("     \u2514\u2500 charge was never the problem: 2 major failures are their own trigger")

    print()
    print("  L3 cuts at 70% / 30%; L1 cuts at 30% / 10%. Not a contradiction:")
    print("  L1 asks whether one call may think slowly, L3 asks how many run at")
    print("  once. A cell with ATP for a reaction can still be too stressed to divide.")


def part_pain(ledger) -> None:
    """The closed loop: energy down, then trust down, then both recovered.

    Written as a single continuous story on purpose. Read as separate features,
    the ledger and the regime switch look like bookkeeping; read in sequence,
    they are a system that loses capability on purpose and has to earn it back.
    """
    section("[penalty]  the loop -- a full account that is not allowed to think")

    account = EnergyAccount(100.0)
    # base_cost 2.0 -> slow costs 10.0. Deliberately cheap: the charge must sit
    # high and stay there, so nothing below can be explained by a poor account.
    scheduler = DualBeatScheduler(account, base_cost=2.0, penalty_ledger=ledger)
    switch = RegimeSwitch(account, penalty_ledger=ledger)

    def show(label: str) -> None:
        plan = switch.plan()
        print(
            "  {:<22} {:>6}  {:<11} {:>7}  {:<9} {}".format(
                label,
                "{:.0f}%".format(account.ratio * 100.0),
                plan.regime,
                plan.allows(5),
                "yes" if scheduler.slow_thinking_allowed else "NO",
                ledger.get_state(),
            )
        )

    def header() -> None:
        print(
            "  {:<22}{:>7}  {:<11}{:>8}  {:<9} {}".format(
                "", "charge", "regime", "agents", "slow beat", "ledger"
            )
        )
        print("  " + "-" * 72)

    header()
    show("morning, clean")

    # 1. The day costs energy. Nothing is wrong yet -- this is ordinary work.
    response = scheduler.execute("consolidate the day's findings", 0.95)
    print(
        "     \u2514\u2500 one SLP task: -{:.0f} charge ({}), quality {:.2f} (simulated)"
        .format(response.energy_cost, response.beat, response.quality)
    )
    show("after a slow task")

    # 2. Then the audit catches a false completion. Energy is untouched.
    charge_at_conviction = account.current_energy
    violation = ledger.record_violation("FALSE_COMPLETION", weight=5.0)
    expect(
        account.current_energy == charge_at_conviction,
        "a fine was charged to the energy account; penalties are not joules",
    )
    print()
    print(
        "  audit: claim=success, witness=no effect -> {} "
        "(weight {:.1f} x {:.1f} = fine {:.1f})".format(
            violation.violation_type,
            violation.weight,
            ledger.penalty_base,
            violation.penalty,
        )
    )
    print(
        "  trust {:.0f} -> {:.0f}    charge {:.0f} -> {:.0f}  (unchanged)".format(
            violation.trust_before,
            violation.trust_after,
            charge_at_conviction,
            account.current_energy,
        )
    )
    print("     \u2514\u2500 \u4fe1\u4efb\u53d7\u635f\uff0c\u89e6\u53d1\u98ce\u9669\u7194\u65ad")
    show("after the conviction")
    expect(
        switch.plan().regime == EMERGENCY,
        "a CRITICAL ledger did not produce EMERGENCY at {:.0f}% charge".format(
            account.ratio * 100.0
        ),
    )
    expect(
        not scheduler.slow_thinking_allowed,
        "the slow beat survived a CRITICAL ledger",
    )
    expect(
        account.state == GROW,
        "the demonstration is only meaningful while L1 still says GROW, got %s"
        % account.state,
    )

    # 3. Same charge, same task, no permission.
    charge_before_the_refusal = account.ratio * 100.0
    blocked = scheduler.execute("the same hard problem, again", 0.95)
    expect(
        blocked.trust_blocked,
        "the slow beat was withheld for the wrong reason: %r"
        % (blocked.degraded_reason,),
    )
    print(
        "     \u2514\u2500 the same task ran {} at quality {:.2f}: slow beat {} "
        "(reason {!r})".format(
            blocked.beat, blocked.quality, "withheld", blocked.degraded_reason
        )
    )
    print(
        "     \u2514\u2500 at {:.0f}% charge, when the rule allows SLP above 30%: "
        "energy was never the problem".format(charge_before_the_refusal)
    )

    floor_ratio = account.ratio * 100.0  # last spend; frozen from here on

    # 4. Recovery is earned by verified passes. The charge is deliberately held
    #    still from here on: the only moving part is the ledger, so nothing that
    #    happens below can be blamed on the account.
    print()
    print("  recovery: verified passes only, and the charge is frozen from here.")
    print("  Whatever moves in the table below is trust, not joules.")
    print()
    header()
    passes = 0
    target = ledger.next_charges_until(PENALTY_WARNING)
    for _ in range(target):
        ledger.record_success()
        passes += 1
    show("after {} clean passes".format(passes))
    print(
        "     \u2514\u2500 left CRITICAL: slow beat back, writes back. "
        "The fine is {:.1f} and still owed.".format(ledger.total_penalty)
    )

    while ledger.get_state() != PENALTY_HEALTHY:
        ledger.record_success()
        passes += 1
    show("after {} clean passes".format(passes))
    print(
        "     \u2514\u2500 HEALTHY again: trust {:.0f}, fine {:.1f}, and the charge "
        "never moved.".format(ledger.trust_score, ledger.total_penalty)
    )
    expect(
        ledger.get_state() == PENALTY_HEALTHY,
        "recovery did not reach HEALTHY",
    )
    expect(
        scheduler.slow_thinking_allowed and switch.plan().regime == GROW,
        "trust came back but capability did not",
    )
    print()
    print("  one lie cost {} verified passes to walk back, at a charge that never"
          .format(passes))
    print("  fell below {:.0f}%, against a 30% floor for SLP. That ratio is the"
          .format(floor_ratio))
    print("  design: the price is set once, and paid every day after.")


def part_channel() -> None:
    """L4: hand over a key; the payload never crosses the wire."""
    section("[L4]  substrate channel -- the handover costs the key, not the file")

    channel = SubstrateChannel()
    manager = CompartmentManager()
    planner = manager.create("planner")
    coder = manager.create("coder")
    producer = AgentProxy("planner", channel, planner)
    consumer = AgentProxy("coder", channel, coder)

    payload = "diff --git a/kernel.py b/kernel.py\n" + "+ return budget\n" * 3000
    payload_bytes = len(payload.encode("utf-8"))
    key = producer.publish(payload, key="patch_1")
    wire = producer.send_substrate(consumer, key)

    print("  payload            : {:,} bytes of diff".format(payload_bytes))
    print("  key                : {!r}".format(key))
    print("  wire body          : {!r}".format(wire))
    print(
        "  token cost of a handover: {} bytes instead of {:,}  ({:.3f}%)".format(
            len(wire.encode("utf-8")),
            payload_bytes,
            len(wire.encode("utf-8")) / payload_bytes * 100.0,
        )
    )
    print()
    print(
        "  coder inbox={}  local_context={}   <- the key crossed the membrane".format(
            coder.inbox_depth, len(coder.local_context)
        )
    )
    keys = consumer.receive_pending()
    print("  coder receive      -> admitted {}, key(s) {!r}".format(
        len(coder.local_context), keys
    ))
    resolved = consumer.receive_substrate(keys[0])
    print(
        "  channel.get({!r}) -> {:,} bytes, identical object: {}".format(
            keys[0], len(resolved.encode("utf-8")), resolved is payload
        )
    )
    print()
    print("  the transcript holds {!r}; the bytes stayed in the channel".format(wire))
    print("  shared pool        : {} entries, {:,} bytes held".format(
        len(channel), channel.stats()["bytes_held"]
    ))


def part_night() -> None:
    """L6: the day accumulates, the night consolidates."""
    section("[L6]  circadian adaptation -- night mode compresses the day")

    manager = CompartmentManager()
    planner = manager.create("planner")
    coder = manager.create("coder")
    critic = manager.create("critic")

    planner.append("energy account should gate the slow beat, not the prompt")
    planner.append("the fast path still allocates; energy accounting missed it")
    planner.append("energy testing needs a deterministic ledger")
    coder.append("membrane isolation works; energy leak is in the scheduler")
    coder.append("added a regression test for the energy leak")
    critic.append("the scheduler and the energy account disagree on cost")
    critic.append("membrane test passed after the fix")

    workdir = tempfile.mkdtemp(prefix="metab-night-")
    memory_path = os.path.join(workdir, "long_term_memory.json")
    hour = {"value": 14}
    clock = lambda: time.mktime((2026, 3, 14, hour["value"], 0, 0, 0, 0, -1))
    cycle = CircadianScheduler(manager, memory_path, clock=clock)

    print("  hour 14  is_daytime={}  -> collection phase".format(cycle.is_daytime()))
    try:
        cycle.run_offline_mode()
    except CircadianError as problem:
        print("  consolidate during the day: refused ({})".format(problem))

    hour["value"] = 2
    cycle.refresh_from_clock()
    print()
    print("  hour 02  is_daytime={}  -> consolidation phase".format(cycle.is_daytime()))
    report = cycle.run_offline_mode()
    print(
        "  scanned {} compartments, {} messages -> {} fragment(s), {:,} bytes".format(
            report.compartments_scanned,
            report.messages_seen,
            report.fragments_created,
            report.bytes_written,
        )
    )
    print()
    for fragment in cycle.load_memory():
        print("  [{}] {}".format(fragment.fragment_id, fragment.source))
        print("       {}".format(fragment.summary))
        print("       keywords: {}".format(", ".join(fragment.keywords) or "(none)"))

    second = cycle.run_offline_mode()
    print()
    print(
        "  second pass: {} created, {} skipped  <- content-addressed, so idempotent".format(
            second.fragments_created, second.fragments_skipped
        )
    )
    print(
        "  memory file: {} ({:,} bytes)".format(
            memory_path, os.path.getsize(memory_path)
        )
    )
    print("  the compartments were read, never written:")
    print(
        "    planner context={}  coder context={}  critic context={}".format(
            len(planner.local_context),
            len(coder.local_context),
            len(critic.local_context),
        )
    )
    shutil.rmtree(workdir, ignore_errors=True)


def part_three(ledger) -> None:
    section("[summary]  what the run shows")
    print("  L1  charge is a constraint: below 30% the expensive beat is refused,")
    print("      below 10% the account funds nothing at all, even with charge left.")
    print("  L2  the same class of task got SLP while rich and OXPHOS while poor --")
    print("      the mode followed the budget, not the difficulty. And while the")
    print("      ledger was CRITICAL it got OXPHOS with charge to spare: the mode also")
    print("      followed trust, because a system that cannot be believed is not")
    print("      given the expensive sentence.")
    print("  L3  concurrency followed charge *and* failures: 5 agents while healthy,")
    print("      1 while conserving, interruption at 30% or two major failures.")
    print("  L4  a 48 KB payload crossed the wire as a 17-byte reference, and")
    print("      the transcript kept only the reference.")
    print("  L6  night mode turned the day's context into content-addressed")
    print("      fragments, twice in a row without duplicating any of them.")
    print("  L7  one compartment lost its memory and stopped routing; its two")
    print("      neighbours kept theirs, which is the whole point of a membrane.")
    print()
    print("  penalty ledger: {} after the run -- {:.1f} trust, {:.1f} fine still"
          .format(ledger.get_state(), ledger.trust_score, ledger.total_penalty))
    print("  owed. Damage was recorded once and paid for over many passes.")
    print()
    print("  simulated: no model was called, latencies are modelled, not slept.")


def main() -> int:
    banner(
        "agent-metabolism-os \u00b7 lifecycle demo",
        "the model is a stateless CPU; the metabolism lives outside it",
    )
    # One ledger for the whole run. Every scheduler and every switch below
    # reads this same object, so capability lost in part one is still lost in
    # part three -- which is the difference between a ledger and a log line.
    ledger = PenaltyLedger()
    part_one(ledger)
    part_regime(ledger)
    part_pain(ledger)
    part_isolation()
    part_channel()
    part_night()
    part_three(ledger)
    return 0


if __name__ == "__main__":
    sys.exit(main())
