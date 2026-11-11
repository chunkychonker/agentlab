"""Milestone 4: the cycle value types refuse illegal states; the pure helpers behave as specified.

Covers the ``CycleResult`` sum type, ``CycleReport``/``NightResult``,
``RunId``, the recovery branch name, the prompt templates, and B10's
``tag_line`` (every prior metrics field unchanged).
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from nightloop.cycle import (
    Clean,
    CycleReport,
    NightResult,
    NotShipped,
    PhaseFailed,
    ReviewRefused,
    RunId,
    Shipped,
    Snapshotted,
    UsageLimited,
)
from nightloop.cycle.cycle import PHASE_ORDER
from nightloop.cycle.metrics_tag import tag_line
from nightloop.cycle.naming import recovery_branch
from nightloop.cycle.prompts import phase_prompt
from nightloop.gates import Verdict
from nightloop.model import PHASE_POSTCONDITIONS
from nightloop.phase import PhaseRecord, PhaseStatus
from nightloop.phase.metrics import to_json_line
from nightloop.phase.result import parse_cli_json
from nightloop.phase.fake_runner import cli_stdout

RUN = RunId("2026-11-11_020001")


class CycleResultTest(unittest.TestCase):
    def test_valid_cases_build(self) -> None:
        Shipped(pr=7)
        NotShipped("gh is not authenticated")
        PhaseFailed("research", PhaseStatus.GATE_FAILED)
        PhaseFailed("review", PhaseStatus.TIMED_OUT)
        ReviewRefused(Verdict.MISSING)
        UsageLimited("build", "4:10pm (America/Chicago)")

    def test_illegal_cases_raise(self) -> None:
        bad = [
            lambda: Shipped(pr=0),
            lambda: Shipped(pr=True),
            lambda: NotShipped(""),
            lambda: NotShipped("two\nlines"),
            lambda: PhaseFailed("build", PhaseStatus.GATED_PASS),
            lambda: PhaseFailed("review", PhaseStatus.GATE_FAILED),  # that is ReviewRefused
            lambda: PhaseFailed("maintain", PhaseStatus.CLI_ERROR),
            lambda: ReviewRefused(Verdict.PASS),
            lambda: UsageLimited("build", ""),
            lambda: UsageLimited("deploy", "9pm"),
        ]
        for index, make in enumerate(bad):
            with self.subTest(case=index), self.assertRaises((TypeError, ValueError)):
                make()

    def test_snapshotted_needs_a_branch_and_a_bool(self) -> None:
        with self.assertRaises(ValueError):
            Snapshotted(branch="", pushed=True)
        with self.assertRaises(TypeError):
            Snapshotted(branch="cycle/x", pushed=1)


class ReportTest(unittest.TestCase):
    def report(self, cycle: int, result) -> CycleReport:
        return CycleReport(cycle=cycle, result=result, recovery=Clean())

    def test_a_usage_limit_can_only_end_the_night(self) -> None:
        limited = self.report(1, UsageLimited("research", "9pm"))
        night = NightResult((limited,))
        self.assertTrue(night.usage_limited)
        with self.assertRaises(ValueError):
            NightResult((limited, self.report(2, Shipped(1))))

    def test_cycles_must_be_numbered_in_order(self) -> None:
        with self.assertRaises(ValueError):
            NightResult((self.report(2, Shipped(1)),))
        with self.assertRaises(ValueError):
            NightResult(())

    def test_report_rejects_values_outside_the_sum_types(self) -> None:
        with self.assertRaises(TypeError):
            CycleReport(cycle=1, result="shipped", recovery=Clean())
        with self.assertRaises(TypeError):
            CycleReport(cycle=1, result=Shipped(1), recovery=None)
        with self.assertRaises(ValueError):
            CycleReport(cycle=0, result=Shipped(1), recovery=Clean())


class RunIdAndNamingTest(unittest.TestCase):
    def test_run_id_format(self) -> None:
        self.assertEqual(RUN.date, "2026-11-11")
        for bad in ("2026-11-11", "2026-13-01_000000", "2026-11-11_250000", "2026-11-11_020001 ", "../x"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                RunId(bad)
        with self.assertRaises(TypeError):
            RunId(20261111)

    def test_branch_name_matches_bash_shape(self) -> None:
        self.assertEqual(recovery_branch(RUN, 2, "021340"), "cycle/2026-11-11-unshipped-021340-2")

    def test_two_cycles_in_the_same_second_get_different_names(self) -> None:  # B5, invariant 5
        self.assertNotEqual(recovery_branch(RUN, 1, "021340"), recovery_branch(RUN, 2, "021340"))

    def test_branch_name_rejects_bad_parts(self) -> None:
        for cycle, hhmmss in ((0, "021340"), (True, "021340"), (1, "2:13:40"), (1, "246000")):
            with self.subTest(cycle=cycle, hhmmss=hhmmss), self.assertRaises(ValueError):
                recovery_branch(RUN, cycle, hhmmss)


class PromptsTest(unittest.TestCase):
    def test_phase_order_is_the_config_phases(self) -> None:
        self.assertEqual(PHASE_ORDER, tuple(PHASE_POSTCONDITIONS))

    def test_every_phase_has_a_prompt_naming_the_repo(self) -> None:
        for name in PHASE_ORDER:
            text = phase_prompt(name, repo=Path("/work/repo"), verdict_path=Path("logs/last-review.md"))
            with self.subTest(phase=name):
                self.assertIn("/work/repo", text)
                self.assertFalse(text.startswith("-"))

    def test_review_prompt_names_the_file_the_gate_reads(self) -> None:
        text = phase_prompt("review", repo=Path("/r"), verdict_path=Path("out/verdict.md"))
        self.assertIn("out/verdict.md", text)

    def test_unknown_phase_raises(self) -> None:
        with self.assertRaises(KeyError):
            phase_prompt("maintain", repo=Path("/r"), verdict_path=Path("v"))


class TagLineTest(unittest.TestCase):
    """B10 at the unit level: two keys first, every m3 field unchanged and in order."""

    def m3_line(self) -> str:
        cli = parse_cli_json(cli_stdout("ok · done", duration_ms=12, total_cost_usd=0.1, num_turns=3))
        record = PhaseRecord(phase="build", model="opus", rc=0, outcome=PhaseStatus.GATED_PASS,
                             postcondition_state="FRESH", cli=cli)
        return to_json_line(record)

    def test_tags_go_first_and_nothing_else_changes(self) -> None:
        original = self.m3_line()
        tagged = tag_line(original, RUN, 3)
        doc = json.loads(tagged)
        self.assertEqual(list(doc)[:2], ["run_ts", "cycle"])
        self.assertEqual((doc["run_ts"], doc["cycle"]), ("2026-11-11_020001", 3))
        rest = {k: v for k, v in doc.items() if k not in ("run_ts", "cycle")}
        self.assertEqual(list(rest), list(json.loads(original)))
        self.assertEqual(rest, json.loads(original))
        self.assertNotIn("\n", tagged)

    def test_a_line_already_tagged_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            tag_line(tag_line(self.m3_line(), RUN, 1), RUN, 1)

    def test_a_non_object_line_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            tag_line("[1, 2]", RUN, 1)


if __name__ == "__main__":
    unittest.main()
