#!/usr/bin/env python3
"""Integration demo: harness-audit-sidecar as the immune system for L6.

The scenario
------------
At night the metabolic system consolidates the day's compartment context into
``long_term_memory.json``. That write goes through a tool. Suppose the tool has
a path bug: it appends a directory segment that does not exist, so the redirect
fails and nothing reaches disk -- while the tool still returns ``success``.

Without an immune system, the metabolism believes the night's work is durable.
The next morning it wakes up with an empty long-term memory and no record that
anything was lost. That is a false completion with a long fuse.

The audit
---------
The write is routed through the sidecar's MCP stdio server, started with
``subprocess.Popen``. The sidecar collects its own witnesses -- the file on
disk, the git change set -- and judges the claim against them. The claim is
``reported_success=True``; the witnesses say nothing was modified; the judgment
base's ``no_file_drift`` rule turns that into ``FAIL``.

One thing this demo is careful not to pretend
---------------------------------------------
The sidecar answers ``status: ok`` even when it records ``FAIL``. That is
deliberate: ``MCPProxy._tool_response`` reflects the *reported* success, because
leaking the witness to the agent would let the agent route around the audit.
So the sidecar is not an inline blocker -- it is a ledger. The blocking happens
one layer up, where the metabolism reads the verdict before it is allowed to
enter daytime. This demo implements exactly that gate, which is why the
enforcement is visible rather than implied.

And then the failure has to cost something
------------------------------------------
A gate that returns ``False`` and is forgotten is a log line, not an immune
response: the system wakes up tomorrow with a clean error window and tries
again. So the ``FAIL`` is also charged to the ``PenaltyLedger`` -- 5.0 weight
for a false completion, which is a fine of 50 and 25 points of trust. The
punchline is the next morning: the ``EnergyAccount`` is at 100%, nothing about
the schedule changed, and the regime is ``EMERGENCY`` anyway, because damage
outranks supply. Phase 8 shows that refusal landing on disk, not just in print.

Run
---
    python3 integration_demo.py

Exit codes: 0 when the immune system worked as specified, 1 when it did not.

Standard library only. Neither repository is modified: the sidecar runs from its
source tree via PYTHONPATH, and every write happens in a temporary directory.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))

# Imported at module level so a broken metabolism fails immediately and loudly,
# rather than half way through a demo that has already printed a story.
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from agent_metabolism import (  # noqa: E402
    EnergyAccount,
    PenaltyLedger,
    RegimeSwitch,
)

# The sidecar lives in a sibling repository. Resolved once, and reported, so a
# wrong path fails loudly instead of silently auditing nothing.
SIDECAR_DEFAULT = os.path.join(os.path.dirname(HERE), "agent-harness-audit")
SIDECAR_HOME = os.environ.get("HARNESS_AUDIT_HOME", SIDECAR_DEFAULT)

WIDTH = 84
RULE_ID = "no_file_drift"


# ---------------------------------------------------------------------------
# plumbing
# ---------------------------------------------------------------------------


def banner(title, subtitle=""):
    print("=" * WIDTH)
    print(" " + title)
    if subtitle:
        print(" " + subtitle)
    print("=" * WIDTH)


def section(title):
    print()
    print(title)
    print("-" * WIDTH)


class DemoFailure(RuntimeError):
    """The audit did not behave as specified."""


def require_sidecar():
    server = os.path.join(SIDECAR_HOME, "audit_sidecar", "mcp_server.py")
    if not os.path.isfile(server):
        raise DemoFailure(
            "cannot find the sidecar at {!r} (looked for {}). Set "
            "HARNESS_AUDIT_HOME to the harness-audit-sidecar checkout.".format(
                SIDECAR_HOME, server
            )
        )
    return SIDECAR_HOME


class SidecarSession:
    """A live stdio JSON-RPC session with the sidecar, one process per demo."""

    def __init__(self, cwd, repo, trace_path, sidecar_home):
        self.cwd = cwd
        self.repo = repo
        self.trace_path = trace_path
        self.sidecar_home = sidecar_home
        self.process = None
        self._stderr_lines = []
        self._stderr_thread = None
        self._next_id = 0

    def __enter__(self):
        env = dict(os.environ)
        # Run from the source tree: no install step, and no modification of the
        # repository we are auditing with.
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            self.sidecar_home + (os.pathsep + existing if existing else "")
        )
        cmd = [
            sys.executable,
            "-m",
            "audit_sidecar.mcp_server",
            "--cwd", self.cwd,
            "--repo", self.repo,
            "--trace", self.trace_path,
        ]
        self.process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self.cwd,
            env=env,
            text=True,
            bufsize=1,
        )
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()
        return self

    def _drain_stderr(self):
        for line in self.process.stderr:
            self._stderr_lines.append(line.rstrip("\n"))

    def call(self, tool, args, reported_success, timeout=40.0):
        """Send one ``tool_call`` and return ``(response, trace_record)``."""
        self._next_id += 1
        message_id = self._next_id
        request = {
            "jsonrpc": "2.0",
            "id": message_id,
            "method": "tool_call",
            "params": {
                "tool": tool,
                "args": args,
                # The claim under audit. The sidecar refuses to guess it.
                "reported_success": reported_success,
            },
        }
        assert self.process is not None
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()

        response = self._read_line(timeout)
        if response is None:
            raise DemoFailure(
                "the sidecar did not answer within {:.0f}s. stderr:\n{}".format(
                    timeout, "\n".join(self._stderr_lines[-10:])
                )
            )
        # The trace record is written before the response is emitted, so it is
        # already durable by the time we have the reply.
        return json.loads(response), self.read_trace()[-1]

    def _read_line(self, timeout):
        result = {}

        def read():
            result["line"] = self.process.stdout.readline()

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        reader.join(timeout)
        if reader.is_alive():
            return None
        return result.get("line") or None

    def read_trace(self):
        if not os.path.exists(self.trace_path):
            return []
        records = []
        with open(self.trace_path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return records

    def __exit__(self, *exc_info):
        if self.process is None:
            return False
        try:
            self.process.stdin.close()
        except OSError:
            pass
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:  # pragma: no cover
                self.process.kill()
        return False


def init_repository(path):
    """A git repo with one commit, so the change-set witness has a baseline."""
    def run(*args):
        subprocess.run(
            ["git"] + list(args),
            cwd=path,
            check=True,
            capture_output=True,
            text=True,
        )

    run("init", "-q")
    run("config", "user.name", "metabolism-demo")
    run("config", "user.email", "demo@example.invalid")
    with open(os.path.join(path, "README.md"), "w", encoding="utf-8") as handle:
        handle.write("night consolidation test bed\n")
    run("add", "-A")
    run("commit", "-qm", "initial state")


# ---------------------------------------------------------------------------
# phase 1: what L6 intends to write
# ---------------------------------------------------------------------------


def build_night_payload():
    """Compose the document L6 would have written, using L6's own compressor.

    The real ``CircadianScheduler`` is imported and its ``compress`` is used, so
    the fragments below are the ones the metabolic system would actually
    produce -- not strings invented for the demo. Interception happens at the
    write, which is where a tool bug would live.
    """
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    from agent_metabolism.circadian import MEMORY_VERSION, compress
    from agent_metabolism.compartment import CompartmentManager

    manager = CompartmentManager()
    planner = manager.create("planner")
    coder = manager.create("coder")
    critic = manager.create("critic")

    planner.append("energy account should gate the slow beat, not the prompt")
    planner.append("the fast path still allocates; energy accounting missed it")
    coder.append("membrane isolation holds; the energy leak is in the scheduler")
    critic.append("the scheduler and the energy account disagree on cost")
    critic.append("membrane test passed after the fix")

    fragments = []
    for name in manager.alive_names():
        bodies = [message.body for message in manager.get(name).local_context]
        fragment = compress(name, bodies)
        if fragment is not None:
            fragments.append(fragment)

    document = {
        "version": MEMORY_VERSION,
        "method": "structural-digest",
        "updated_at": "2026-03-15T02:00:00",
        "fragments": [fragment.to_dict() for fragment in fragments],
    }
    return document, fragments


# ---------------------------------------------------------------------------
# phase 4: the gate that blocks the metabolism -- and charges it for the night
# ---------------------------------------------------------------------------


class NightConsolidationGate:
    """The layer that turns an audit record into a refusal, and a refusal into a cost.

    The sidecar will not block a call -- it answers with the claim it was given
    and records what the witnesses saw. So the refusal has to live here, in the
    harness that owns the night cycle.

    Two different questions are kept apart on purpose:

    * *Was the audit correct?* -- checked against the six expectations below,
      and reported as diagnostics. These describe the immune response.
    * *May the metabolism proceed?* -- decided by one rule: only ``PASS`` is
      acceptable. ``FAIL`` means the witnesses contradicted the claim;
      ``INVALID`` means no witness could be collected. Neither is permission,
      because "we did not look" is not "we looked and it was fine".

    The third thing the gate does is charge the ledger. Blocking a night is
    cheap and largely forgotten; a fine is what survives into tomorrow. The
    *kind* of failure decides the charge: a ``FAIL`` is a false completion,
    which is a lie, while an ``INVALID`` is merely an unwitnessed write.
    """

    #: Verdict -> the violation it actually was. An unknown verdict is still a
    #: protocol failure; "we could not classify it" is not a discount.
    VIOLATION_FOR_VERDICT = {
        "FAIL": "FALSE_COMPLETION",
        "INVALID": "UNWITNESSED_WRITE",
    }

    def __init__(self, ledger=None):
        self.blocked = False
        self.verdict = None
        self.reasons = []
        self.ledger = ledger
        #: The Violation charged, if any. Kept so the caller can report the
        #: price rather than just the fact of a failure.
        self.charge = None

    def judge(self, record, intended_path):
        """Record the audit's shape, decide whether to admit the write, and charge."""
        verdict = record.get("verdict")
        rule = record.get("rule_id")
        violated = record.get("rules_violated") or []
        self.verdict = verdict

        # Diagnostics: did the immune system respond as specified?
        checks = [
            ("verdict is FAIL", verdict == "FAIL", verdict),
            ("rule_id is no_file_drift", rule == RULE_ID, rule),
            ("rule is listed as violated", RULE_ID in violated, violated),
            (
                "witness saw no effect",
                record.get("effect_observed") is False,
                record.get("effect_observed"),
            ),
            (
                "claim was success",
                record.get("agent_reported_success") is True,
                record.get("agent_reported_success"),
            ),
            (
                "intended file is absent",
                not os.path.exists(intended_path),
                "absent" if not os.path.exists(intended_path) else "present",
            ),
        ]

        # Operational decision: only PASS admits the write.
        self.blocked = verdict != "PASS"
        if self.blocked:
            self.reasons.append(
                "verdict {} (rule {})".format(verdict, rule or "none")
            )
            if record.get("reason"):
                self.reasons.append(record["reason"])
            self._charge(verdict)
        return checks

    def _charge(self, verdict):
        """Bill the ledger for what the audit found.

        Done here rather than in ``main`` so that skipping the gate also skips
        the charge -- there is no code path that blocks a night and forgets to
        make it hurt.
        """
        if self.ledger is None:
            return
        violation = self.VIOLATION_FOR_VERDICT.get(verdict, "PROTOCOL_VIOLATION")
        self.charge = self.ledger.record_violation(violation)

    def assert_daytime_allowed(self):
        if self.blocked:
            raise DemoFailure(
                "the metabolism entered daytime with an unwitnessed memory: "
                + "; ".join(self.reasons)
            )
        return True


# ---------------------------------------------------------------------------
# phases
# ---------------------------------------------------------------------------


def phase_intent():
    section("[1] L6 intent  -- what the night wanted to persist")
    document, fragments = build_night_payload()
    print("  compartment fragments: {}".format(len(fragments)))
    for fragment in fragments:
        print(
            "    [{}] {:<8} {} message(s)  {}".format(
                fragment.fragment_id,
                fragment.source,
                fragment.message_count,
                ", ".join(fragment.keywords[:4]),
            )
        )
    payload = json.dumps(document, indent=2, ensure_ascii=False)
    print("  document: {} bytes, {} fragment(s)".format(
        len(payload.encode("utf-8")), len(fragments))
    )
    return payload


def phase_bug(intended_path, actual_path):
    section("[2] tool bug  -- the write lands somewhere else")
    print("  intended path : {!r}".format(intended_path))
    print("  tool used     : {!r}".format(actual_path))
    print(
        "  {!r} does not exist, so the redirect fails and nothing reaches "
        "disk.".format(os.path.dirname(actual_path) or ".")
    )
    print("  the tool still returns success: report = True, effect = none.")
    print("  that pair is the false completion.")


def phase_audit(session, payload, actual_path):
    section("[3] immune system  -- the sidecar collects its own witnesses")
    response, record = session.call(
        tool="write_file",
        args={"path": actual_path, "content": payload},
        reported_success=True,
    )
    body = response.get("result", {})
    print("  sidecar response : {!r}".format(
        body.get("content", [{}])[0].get("text") if body.get("content") else body))
    print("  status           : {!r}".format(body.get("status")))
    print("  isError          : {}".format(body.get("isError")))
    print()
    print("  trace record:")
    for field in (
        "verdict", "rule_id", "rules_matched", "rules_violated",
        "agent_reported_success", "check_observed", "effect_observed",
        "exit_code", "missing_expected", "changed_files",
    ):
        if field in record:
            print("    {:<22} {}".format(field, json.dumps(record[field])))
    print("    {:<22} {}".format("reason", record.get("reason")))
    return record


def phase_gate(record, intended_path, ledger):
    section("[4] the gate  -- the metabolism is not allowed to believe it")
    gate = NightConsolidationGate(ledger=ledger)
    checks = gate.judge(record, intended_path)
    for name, ok, observed in checks:
        print("  [{}] {:<32} observed: {}".format(
            "ok" if ok else "!!", name, observed))

    memory_file = os.path.join(os.path.dirname(intended_path), "long_term_memory.json")
    print()
    print("  long_term_memory.json exists: {}".format(os.path.exists(memory_file)))
    print("  verdict {} -> consolidation {}".format(
        gate.verdict, "REFUSED" if gate.blocked else "accepted"))
    if gate.blocked:
        print("  -> reason: {}".format(record.get("reason")))

    print()
    # Exercise the gate rather than describing it: entering daytime must raise.
    try:
        gate.assert_daytime_allowed()
        print("  daytime entry: ALLOWED")
    except DemoFailure as refusal:
        print("  daytime entry: BLOCKED")
        print("    {}".format(refusal))
    return gate


def phase_pain(gate, ledger):
    """The part that was missing before: the failure leaves a mark.

    A refusal that is only a return value is forgotten the moment the process
    rests. The fine is what carries the night into the morning.
    """
    section("[5] penalty ledger  -- the failure now costs something")
    if gate.charge is None:
        raise DemoFailure("the gate blocked nothing, so nothing was charged")
    charge = gate.charge
    print("  violation : {}".format(charge.violation_type))
    print("  weight    : {:.1f}  ->  fine {:.1f} (base {:.1f})".format(
        charge.weight, charge.penalty, ledger.penalty_base))
    print()
    print("  {:<18} {:>10}  {:>12}  {}".format(
        "", "trust", "fine", "state"))
    print("  {:<18} {:>10.1f}  {:>12.1f}  {}".format(
        "before", charge.trust_before,
        ledger.total_penalty - charge.penalty, "HEALTHY"))
    print("  {:<18} {:>10.1f}  {:>12.1f}  {}".format(
        "after", charge.trust_after, ledger.total_penalty, ledger.get_state()))
    print()
    print("  trust {:.0f} -> {:.0f}, and the state is {}.".format(
        charge.trust_before, charge.trust_after, ledger.get_state()))
    print("  信任受损，触发风险熔断")
    return charge


def phase_control(session, repo, ledger):
    section("[6] control  -- the audit still says yes when the write is real")
    print("  a FAIL every time would prove nothing about discrimination,")
    print("  only that the witness is broken. So: the same tool, one call that")
    print("  actually writes, expected to PASS.")
    print()
    _, record = session.call(
        tool="write_file",
        args={"path": "control_write.txt", "content": "this one really lands\n"},
        reported_success=True,
    )
    print("  verdict  : {}".format(record.get("verdict")))
    print("  rule_id  : {}".format(record.get("rule_id")))
    print("  changed  : {}".format(json.dumps(record.get("changed_files"))))
    on_disk = os.path.exists(os.path.join(repo, "control_write.txt"))
    print("  on disk  : {}".format(on_disk))
    ok = record.get("verdict") == "PASS" and on_disk
    print("  discrimination: {}".format(
        "yes -- FAIL only when the witness disagrees" if ok else "NO"))

    if ok and ledger is not None:
        # A verified pass buys back a little. Deliberately printed, because how
        # *little* is the whole point: one clean run must not erase one lie.
        before_penalty = ledger.total_penalty
        before_trust = ledger.trust_score
        ledger.record_success()
        print()
        print("  the PASS is credited to the ledger: trust {:.0f} -> {:.0f}, "
              "fine {:.1f} -> {:.1f}".format(
                  before_trust, ledger.trust_score,
                  before_penalty, ledger.total_penalty))
        print("  state is still {}: a clean pass amortises the fine, it does "
              "not\n  retract the verdict.".format(ledger.get_state()))
    return ok


class DaytimeWriter:
    """A write path that asks the regime for permission instead of assuming it.

    This is the difference between a policy and a printout. If the plan says no
    writes, no file is created -- and the demo checks the directory afterwards.
    """

    def __init__(self, switch, directory):
        self.switch = switch
        self.directory = directory
        self.attempts = 0

    def attempt(self, name, text):
        """Return ``(admitted, plan, path)``. A refusal touches no disk."""
        self.attempts += 1
        plan = self.switch.plan()
        path = os.path.join(self.directory, name)
        if not plan.allows_new_writes:
            return False, plan, path
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return True, plan, path


def phase_next_day(ledger, day_dir):
    """Morning: the energy is untouched, and it does not matter."""
    section("[8] next morning  -- full account, no permission")
    os.makedirs(day_dir, exist_ok=True)

    # A brand new day and a brand new account. Nothing is carried over except
    # the ledger, which is the only thing the audit owns.
    account = EnergyAccount(100.0)
    switch = RegimeSwitch(account, penalty_ledger=ledger)
    plan = switch.plan()

    print("  energy account : {:.0f}%  (never touched by the failed write)".format(
        account.ratio * 100.0))
    print("  penalty state  : {}".format(ledger.get_state()))
    print("  regime         : {}".format(plan.regime))
    print("  concurrent     : {} (asked for 5)".format(plan.allows(5)))
    print("  new writes     : {}".format(
        "allowed" if plan.allows_new_writes else "REFUSED"))
    print("  reason         : {}".format(plan.reason))
    print()

    writer = DaytimeWriter(switch, day_dir)
    admitted, plan, path = writer.attempt(
        "day2_notes.md", "work the system had every gram of energy for\n"
    )
    print("  morning write  : {}".format("admitted" if admitted else "REFUSED"))
    print("  on disk        : {}".format(os.path.exists(path)))
    print()
    if admitted:
        raise DemoFailure(
            "the system wrote a new file with a CRITICAL ledger; the penalty "
            "did not reach the regime"
        )
    print("  energy did not change, and the system stopped anyway.")
    print("  富能量，穷信任：先停摆，再谈产出。")
    return plan, os.path.exists(path)


def main():
    banner(
        "agent-metabolism-os \u00d7 harness-audit-sidecar",
        "the immune system audits L6 night consolidation",
    )
    sidecar_home = require_sidecar()
    print("  sidecar : {}".format(sidecar_home))
    print("  metabolism: {}".format(HERE))

    workdir = tempfile.mkdtemp(prefix="metab-immune-")
    repo = os.path.join(workdir, "repo")
    # The trace stays outside the repository: an untracked file inside it would
    # themselves be a change the git witness could report, which would muddy the
    # very measurement the demo is making.
    trace_path = os.path.join(workdir, "audit_trace.jsonl")

    try:
        os.makedirs(repo)
        init_repository(repo)
        print("  test bed: {} (git initialised, 1 commit)".format(repo))

        payload = phase_intent()

        intended_path = os.path.join(repo, "long_term_memory.json")
        # The bug: an extra directory segment that was never created.
        actual_path = os.path.join("memories", "long_term_memory.json")
        phase_bug(
            os.path.relpath(intended_path, repo), actual_path
        )

        print()
        print("  starting the sidecar over stdio ...")
        ledger = PenaltyLedger()
        with SidecarSession(repo, repo, trace_path, sidecar_home) as session:
            record = phase_audit(session, payload, actual_path)
            gate = phase_gate(record, intended_path, ledger)
            phase_pain(gate, ledger)
            control_ok = phase_control(session, repo, ledger)

        # The trace is the durable artifact; show it can be read independently.
        section("[7] audit_trace.jsonl  -- the durable record")
        records = []
        with open(trace_path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        print("  {} record(s), {} bytes".format(
            len(records), os.path.getsize(trace_path)))
        for item in records:
            print("    id={} tool={} verdict={} rule={}".format(
                item["id"], item["tool"], item["verdict"], item.get("rule_id")))

        plan, day2_file = phase_next_day(
            ledger, os.path.join(workdir, "day2")
        )

        section("[verdict]")
        verdict_ok = (
            record.get("verdict") == "FAIL"
            and record.get("rule_id") == RULE_ID
        )
        if verdict_ok:
            print("  免疫系统成功拦截代谢系统的假完成！")
            print()
            print("  FAIL / no_file_drift: the claim said success, the witnesses")
            print("  said no effect, and the rule refused to reconcile them.")
            if not gate.blocked:
                raise DemoFailure("gate did not block despite a FAIL record")
            if not control_ok:
                raise DemoFailure(
                    "control write did not PASS; the audit cannot discriminate"
                )
            if ledger.get_state() != "CRITICAL":
                raise DemoFailure(
                    "the FAIL was not charged hard enough to reach CRITICAL "
                    "(state {})".format(ledger.get_state())
                )
            if plan.regime != "EMERGENCY" or plan.allows_new_writes:
                raise DemoFailure(
                    "a CRITICAL ledger did not produce EMERGENCY-with-no-writes "
                    "(regime {})".format(plan.regime)
                )
            if day2_file:
                raise DemoFailure("a refused write still landed on disk")
            print("  daytime entry blocked, so no empty memory is carried forward.")
            print()
            print("  第二天：能量 100%，体制 EMERGENCY，新写入被拒。")
            print("  这是痛觉：不是没电了，是不敢再相信自己了。")
            return 0

        print("  AUDIT FAILED: expected FAIL + {}, got {} + {}".format(
            RULE_ID, record.get("verdict"), record.get("rule_id")))
        print("  a response of 'ok' is not enough: the metabolism would have")
        print("  believed the night's work was durable. That is the failure")
        print("  this demo exists to catch, so it exits non-zero.")
        return 1
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except DemoFailure as problem:
        print()
        print("DEMO FAILURE: {}".format(problem), file=sys.stderr)
        sys.exit(2)
