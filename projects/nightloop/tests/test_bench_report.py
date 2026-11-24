"""Milestone 11, criterion 3 (I4, I6): the report's two rates, its table, and its JSON record."""

from __future__ import annotations

import json
import unittest

from nightloop.bench.classify import Outcome
from nightloop.bench.fixture import Kind
from nightloop.bench.report import (
    INTERVAL_CAVEAT,
    UNGRADED_EXIT,
    TrialResult,
    exit_code,
    render_report,
    summarise,
    to_json,
)
from nightloop.cli.exit_codes import ExitCode

SHA = "a" * 64


def r(slug: str, kind: Kind, trial: int, outcome: Outcome, detail: str = "verdict X") -> TrialResult:
    return TrialResult(slug, kind, trial, outcome, detail)


MIXED = (
    r("seed-a", Kind.SEEDED, 1, Outcome.CAUGHT),
    r("seed-a", Kind.SEEDED, 2, Outcome.MISSED),
    r("seed-b", Kind.SEEDED, 1, Outcome.MISDIAGNOSED),
    r("seed-b", Kind.SEEDED, 2, Outcome.UNGRADED, "phase CLI_ERROR (rc 1)"),
    r("clean-a", Kind.CLEAN, 1, Outcome.CLEAN_PASSED),
    r("clean-a", Kind.CLEAN, 2, Outcome.FALSE_POSITIVE),
)


class RenderReportTest(unittest.TestCase):
    def test_first_two_lines_are_both_rates_with_intervals(self) -> None:
        lines = render_report(summarise(MIXED, template_sha256=SHA, trials=2)).splitlines()
        # catch: 1 caught of 3 graded seeded (the UNGRADED one is in neither part).
        self.assertEqual(lines[0], "catch rate 1/3 = 33% (95% CI 6-79%)")
        # false positive: 1 of 2 graded clean.
        self.assertEqual(lines[1], "false-positive rate 1/2 = 50% (95% CI 9-91%)")

    def test_an_axis_with_no_graded_trial_is_n_a(self) -> None:
        results = (
            r("seed-a", Kind.SEEDED, 1, Outcome.UNGRADED, "verdict MALFORMED"),
            r("clean-a", Kind.CLEAN, 1, Outcome.CLEAN_PASSED),
        )
        lines = render_report(summarise(results, template_sha256=SHA, trials=1)).splitlines()
        self.assertEqual(lines[0], "catch rate n/a (no graded seeded trial)")
        self.assertEqual(lines[1], "false-positive rate 0/1 = 0% (95% CI 0-79%)")

    def test_table_has_an_ungraded_column_and_lists_why(self) -> None:
        text = render_report(summarise(MIXED, template_sha256=SHA, trials=2))
        header = next(line for line in text.splitlines() if line.startswith("fixture"))
        self.assertEqual(header.split(), ["fixture", "kind", *(o.name for o in Outcome)])
        self.assertIn("UNGRADED", header.split())
        row = next(line for line in text.splitlines() if line.startswith("seed-b"))
        # seed-b: 0 CAUGHT, 1 MISDIAGNOSED, 0 MISSED, 0 CLEAN_PASSED, 0 FALSE_POSITIVE, 1 UNGRADED
        self.assertEqual(row.split(), ["seed-b", "seeded", "0", "1", "0", "0", "0", "1"])
        self.assertIn("UNGRADED seed-b trial 2: phase CLI_ERROR (rc 1)", text)
        self.assertIn(INTERVAL_CAVEAT, text)
        self.assertIn(SHA, text)

    def test_same_outcomes_give_the_same_bytes_whatever_the_order(self) -> None:
        a = summarise(MIXED, template_sha256=SHA, trials=2)
        b = summarise(tuple(reversed(MIXED)), template_sha256=SHA, trials=2)
        self.assertEqual(render_report(a), render_report(a))
        self.assertEqual(render_report(a), render_report(b))
        self.assertEqual(to_json(a), to_json(b))


class JsonRecordTest(unittest.TestCase):
    def test_json_holds_both_rates_and_the_template_hash(self) -> None:
        doc = json.loads(to_json(summarise(MIXED, template_sha256=SHA, trials=2)))
        self.assertEqual(doc["template_sha256"], SHA)
        self.assertEqual(doc["schema_version"], 1)
        self.assertEqual((doc["catch"]["hits"], doc["catch"]["n"]), (1, 3))
        self.assertEqual((doc["false_positive"]["hits"], doc["false_positive"]["n"]), (1, 2))
        self.assertEqual(doc["ungraded"], 1)
        self.assertEqual([f["slug"] for f in doc["fixtures"]], ["clean-a", "seed-a", "seed-b"])
        self.assertEqual(doc["ungraded_trials"], [{"slug": "seed-b", "trial": 2, "detail": "phase CLI_ERROR (rc 1)"}])

    def test_empty_axis_is_null_in_json(self) -> None:
        results = (r("seed-a", Kind.SEEDED, 1, Outcome.CAUGHT), r("clean-a", Kind.CLEAN, 1, Outcome.UNGRADED))
        doc = json.loads(to_json(summarise(results, template_sha256=SHA, trials=1)))
        self.assertEqual(doc["false_positive"], {"hits": 0, "n": 0, "rate": None, "ci_low": None, "ci_high": None})


class ExitCodeTest(unittest.TestCase):
    def test_any_ungraded_trial_is_non_zero(self) -> None:
        self.assertEqual(exit_code(summarise(MIXED, template_sha256=SHA, trials=2)), UNGRADED_EXIT)
        self.assertNotEqual(UNGRADED_EXIT, 0)

    def test_a_fully_graded_run_is_zero(self) -> None:
        graded = (r("seed-a", Kind.SEEDED, 1, Outcome.MISSED), r("clean-a", Kind.CLEAN, 1, Outcome.FALSE_POSITIVE))
        self.assertEqual(exit_code(summarise(graded, template_sha256=SHA, trials=1)), 0)

    def test_the_core_exit_value_matches_the_cli_exit_code(self) -> None:
        self.assertEqual(UNGRADED_EXIT, ExitCode.UNGRADED)


class SummariseRefusesTest(unittest.TestCase):
    def test_a_partial_run_is_not_summarised(self) -> None:
        with self.assertRaises(ValueError):
            summarise(MIXED[:-1], template_sha256=SHA, trials=2)

    def test_no_results_raises(self) -> None:
        with self.assertRaises(ValueError):
            summarise((), template_sha256=SHA, trials=1)

    def test_an_outcome_impossible_for_the_kind_is_unrepresentable(self) -> None:
        with self.assertRaises(ValueError):
            r("clean-a", Kind.CLEAN, 1, Outcome.CAUGHT)
        with self.assertRaises(ValueError):
            r("seed-a", Kind.SEEDED, 1, Outcome.FALSE_POSITIVE)


if __name__ == "__main__":
    unittest.main()
