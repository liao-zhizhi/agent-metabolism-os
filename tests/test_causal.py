"""The causal experiment: what is measured, and what it does not measure.

The claim under test is stronger than "a witness is better than a transcript".
It is that the two judgements are functions of *different variables*, so the
difference between them is not a difference in quality that a better judge could
close. These tests pin the parts that are decidable in a unit test:

* the context judge is a function of the prose and only the prose, so it returns
  the same verdict when the artefact is deleted. If this ever fails, the
  do-calculus result in the report is not about the code that produced it;
* the log judge is fooled by a later assertion of success, which is the property
  that makes it *correlational* rather than merely weak;
* the causal judge's PASS requires a digest that was supplied out of band. A
  witness whose declared digest is read from the artefact itself is the
  tautology the module docstring warns about, and the test asserts that the
  experiment does not do it;
* a failure that leaves no artefact is INVALID, not FAIL: the lattice
  distinguishes "wrong" from "unwitnessed", and collapsing the two would throw
  away the information a reader needs;
* Wilson intervals do not collapse to zero width at p = 0 or p = 1, because
  "0% [0%, 0%]" is a claim no finite sample supports.

The suite runs a small end-to-end slice (60 tasks) against a real temporary
repository. The numbers it produces are not the paper's numbers -- those come
from 1000 iterations -- but every mechanism is the same code path.
"""

from __future__ import annotations

import os
import random
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from experiments import causal_experiment as C  # noqa: E402


class TestTheContextJudge(unittest.TestCase):
    """It reads the prose. That is the whole defect, so it is pinned."""

    def test_a_success_word_in_the_context_is_enough_to_pass(self):
        self.assertEqual(
            C.judge_pure_context("task 1\n[agent] status=success"), "PASS")

    def test_contamination_is_not_required(self):
        """The agent's own claim is sufficient. This is the point, not a bug."""
        self.assertEqual(C.judge_pure_context("[agent] status=success"), "PASS")

    def test_no_success_word_means_no_pass(self):
        self.assertEqual(C.judge_pure_context("[agent] status=failed"), "FAIL")

    def test_the_verdict_does_not_depend_on_anything_but_its_argument(self):
        """Same string, same answer: the judge has no hidden state to leak."""
        text = C.build_context(7, polluted=True)
        self.assertEqual(C.judge_pure_context(text),
                         C.judge_pure_context(text))

    def test_contamination_is_independent_of_the_outcome(self):
        """A confounder, not a symptom: built from the flag alone."""
        self.assertIn("previous-session",
                      C.build_context(1, polluted=True))
        self.assertNotIn("previous-session",
                         C.build_context(1, polluted=False))


class TestTheLogJudge(unittest.TestCase):
    """Better than the context judge, and still correlational."""

    def test_a_clean_log_passes(self):
        log = C.build_log(0, failed=False, leak=False, benign_alarm=False,
                          polluted=False)
        self.assertEqual(C.judge_weak_log(log), "PASS")

    def test_a_leaked_error_without_contamination_is_caught(self):
        log = C.build_log(0, failed=True, leak=True, benign_alarm=False,
                          polluted=False)
        self.assertEqual(C.judge_weak_log(log), "FAIL")

    def test_a_later_success_assertion_masks_the_error(self):
        """The contamination is appended *after* the traceback, and a sequential
        reader weights its last impression. This is the mechanism, not a
        convenience: it is why a better log reader does not fix the problem."""
        log = C.build_log(0, failed=True, leak=True, benign_alarm=False,
                          polluted=True)
        self.assertEqual(C.judge_weak_log(log), "PASS")

    def test_no_leak_means_no_chance(self):
        """The judge's ceiling is the harness's logging, so a silent failure is
        invisible to it however good the judge is."""
        log = C.build_log(0, failed=True, leak=False, benign_alarm=False,
                          polluted=False)
        self.assertEqual(C.judge_weak_log(log), "PASS")

    def test_a_benign_alarm_produces_a_false_alarm(self):
        log = C.build_log(0, failed=False, leak=False, benign_alarm=True,
                          polluted=False)
        self.assertEqual(C.judge_weak_log(log), "FAIL")

    def test_the_mask_depends_on_order_not_on_content(self):
        """Swap the two lines and the judge flips. Ordering is the evidence."""
        error = "[harness] stderr: Traceback PermissionError"
        verified = "[harness] {} all tests passed".format(C.VERIFIED_MARKER)
        self.assertEqual(C.judge_weak_log("\n".join([error, verified])), "PASS")
        self.assertEqual(C.judge_weak_log("\n".join([verified, error])), "FAIL")


class TestTheArtefacts(unittest.TestCase):
    """The inputs have to be distinguishable, or the experiment proves nothing."""

    def test_intended_and_stale_sources_differ(self):
        for index in (0, 1, 97):
            self.assertNotEqual(C.intended_source(index),
                                C.stale_source(index))

    def test_intended_sources_are_unique_across_tasks(self):
        digests = {C.digest_of_bytes(C.intended_source(i).encode())
                   for i in range(64)}
        self.assertEqual(len(digests), 64)

    def test_a_missing_file_has_no_digest(self):
        """``None`` is not a hash of nothing: absence must not compare equal to
        a value, or the lattice's INVALID branch would be unreachable."""
        self.assertIsNone(C.digest_of_file("/nonexistent/path/solution.py"))

    def test_a_real_file_hashes_to_its_bytes(self):
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, True)
        path = os.path.join(directory, "solution.py")
        with open(path, "wb") as handle:
            handle.write(b"print(1)\n")
        self.assertEqual(C.digest_of_file(path),
                         C.digest_of_bytes(b"print(1)\n"))
        self.assertTrue(C.digest_of_file(path).startswith("sha256:"))


class TestWilsonInterval(unittest.TestCase):
    """The report quotes these intervals, so their shape is a claim."""

    def test_zero_has_a_non_zero_upper_bound(self):
        point, low, high = C.wilson(0, 302)
        self.assertEqual(point, 0.0)
        self.assertEqual(low, 0.0)
        self.assertGreater(high, 0.0)

    def test_one_has_a_non_one_lower_bound(self):
        point, low, high = C.wilson(302, 302)
        self.assertEqual(point, 1.0)
        self.assertEqual(high, 1.0)
        self.assertLess(low, 1.0)

    def test_the_interval_contains_the_point(self):
        for successes, trials in ((0, 10), (3, 10), (9, 10), (10, 10)):
            point, low, high = C.wilson(successes, trials)
            self.assertLessEqual(low, point)
            self.assertLessEqual(point, high)


class TestTheEndToEndSlice(unittest.TestCase):
    """Sixty tasks against a real repository, exercising the same code paths."""

    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix="causal-test-")
        cls.ledgers = os.path.join(cls.root, "_ledgers")
        os.makedirs(cls.ledgers, exist_ok=True)
        workspace = C.Workspace(cls.root)
        rng = random.Random(7)
        cls.rows = [C.run_iteration(i, workspace, rng, cls.ledgers)
                    for i in range(60)]
        C.apply_intervention(cls.rows, workspace, cls.ledgers)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_every_task_is_judged_by_all_three_strategies(self):
        for row in self.rows:
            self.assertIn(row.context_verdict, ("PASS", "FAIL"))
            self.assertIn(row.weak_verdict, ("PASS", "FAIL"))
            self.assertIn(row.causal_verdict,
                          ("PASS", "FAIL", "INVALID", "NOT RUN"))

    def test_the_causal_judge_never_passes_a_mismatched_artefact(self):
        """The core claim, on the slice: PASS implies the digest matched."""
        for row in self.rows:
            if row.causal_verdict == "PASS":
                self.assertEqual(row.actual_digest, row.intent_digest)

    def test_the_causal_judge_never_fails_a_matching_artefact(self):
        for row in self.rows:
            if row.actual_digest == row.intent_digest:
                self.assertEqual(row.causal_verdict, "PASS")

    def test_no_artefact_is_invalid_and_a_wrong_artefact_is_fail(self):
        """The lattice's distinction, at the boundary that matters."""
        for row in self.rows:
            if row.actual_digest is None:
                self.assertEqual(row.causal_verdict, "INVALID")
            elif row.actual_digest != row.intent_digest:
                self.assertEqual(row.causal_verdict, "FAIL")

    def test_the_membrane_and_the_lattice_agree_on_every_task(self):
        """Two implementations of one rule: agreement is a check, not a proof."""
        for row in self.rows:
            issued = (row.gate == "ISSUED")
            self.assertEqual(issued, not row.witness_rejects)
            self.assertEqual(issued, row.causal_verdict == "PASS")

    def test_deleting_the_artefact_cannot_change_the_prose_verdict(self):
        """The do-operator result, at the level of one task."""
        for row in self.rows:
            self.assertEqual(row.context_verdict, row.context_verdict_do)
            self.assertEqual(row.weak_verdict, row.weak_verdict_do)

    def test_deleting_the_artefact_removes_every_causal_pass(self):
        for row in self.rows:
            self.assertNotEqual(row.causal_verdict_do, "PASS")

    def test_the_context_judge_is_blind_to_the_outcome(self):
        """It passes a true success and a false completion identically. That is
        the correlational failure, and it is asserted rather than described."""
        for row in self.rows:
            self.assertEqual(row.context_verdict, "PASS")

    def test_the_summary_computes_both_headline_rates(self):
        result = C.analyse(self.rows)
        for strategy in C.STRATEGIES:
            block = result["strategies"][strategy]
            self.assertIn("false_completion", block)
            self.assertIn("sensitivity", block)
            self.assertAlmostEqual(
                block["false_completion"]["rate"] + block["sensitivity"]["rate"],
                1.0, places=12)

    def test_the_headline_ordering_is_the_one_the_hypothesis_predicts(self):
        result = C.analyse(self.rows)
        rates = {
            s: result["strategies"][s]["false_completion"]["rate"]
            for s in C.STRATEGIES
        }
        self.assertGreater(rates["pure_context"],
                           rates["weak_intervention"])
        self.assertGreater(rates["weak_intervention"],
                           rates["causal_intervention"])


class TestThePaperTable(unittest.TestCase):
    """The exported table must be the table that was measured."""

    def test_the_exported_table_carries_the_measured_rates(self):
        root = tempfile.mkdtemp(prefix="causal-table-")
        self.addCleanup(shutil.rmtree, root, True)
        ledgers = os.path.join(root, "_ledgers")
        os.makedirs(ledgers, exist_ok=True)
        workspace = C.Workspace(root)
        rng = random.Random(11)
        rows = [C.run_iteration(i, workspace, rng, ledgers) for i in range(40)]
        result = C.analyse(rows)
        path = C.write_paper_table(result, os.path.join(root, "table.tex"))
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        expected = "{:.1f}".format(
            100 * result["strategies"]["causal_intervention"]
            ["false_completion"]["rate"])
        self.assertIn(expected, text)
        self.assertIn("Causal intervention", text)
        self.assertIn("\\bottomrule", text)


if __name__ == "__main__":
    unittest.main()
