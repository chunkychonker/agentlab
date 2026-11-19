"""Milestone 7, R1-R5: ``summarise`` and ``render_report`` over metrics lines.

Numbers refer to research/2026-11-19-nightloop-cli.md. Lines come from the
real writer (see ``cli_support.metric_line``) unless a test is about a
malformed or bash-format line.
"""

from __future__ import annotations

import json
import unittest

from nightloop.cli.summary import REFUSING_STATES, render_report, summarise
from nightloop.gates.outcome import Freshness, GateResult, Verdict
from nightloop.model import Postcondition
from nightloop.phase.status import PhaseStatus as S

from .cli_support import bash_line, metric_line

NIGHTS = [f"2026-11-{day:02d}_020001" for day in range(10, 20)]


def night(run_ts: str, *, review_blocks: bool) -> list[str]:
    """A night where research and build pass their freshness gates and review passes or fails."""
    return [
        metric_line(run_ts, "research", S.GATED_PASS, "FRESH"),
        metric_line(run_ts, "build", S.GATED_PASS, "FRESH"),
        metric_line(run_ts, "review", S.GATE_FAILED if review_blocks else S.GATED_PASS,
                    "FAIL" if review_blocks else "PASS"),
    ]


def row(report, phase):
    return next(r for r in report.rows if r.phase == phase)


class WindowOfNightsTest(unittest.TestCase):
    """R1."""

    def test_only_the_last_n_distinct_run_ts_are_kept(self) -> None:
        lines = [line for ts in NIGHTS[:4] for line in night(ts, review_blocks=False)]
        report = summarise(lines, 2)
        self.assertEqual((report.nights_asked, report.nights_found), (2, 2))
        self.assertEqual(row(report, "research").n, 2)

    def test_last_means_latest_run_ts_not_last_in_the_file(self) -> None:
        old = night(NIGHTS[0], review_blocks=True)
        new = night(NIGHTS[5], review_blocks=False)
        report = summarise(new + old, 1)  # the old night is written last
        self.assertEqual(row(report, "review").gate.blocked, 0)

    def test_torn_and_non_object_lines_are_skipped_and_counted_never_raised(self) -> None:
        good = night(NIGHTS[0], review_blocks=False)
        bad = [
            good[0][: len(good[0]) // 2],  # torn mid-write
            "not json at all",
            "[1, 2, 3]",
            "42",
            '"a string"',
            "null",
            '{"run_ts": "2026-11-10_020001", "phase": "build", "duration_ms": NaN}',
            '{"phase": "build"}',  # no run_ts: cannot be placed in a night
            '{"run_ts": "2026-11-10_020001"}',  # no phase
            '{"run_ts": 5, "phase": "build"}',
            '{"run_ts": "2026-11-10_020001", "phase": "build", "postcondition_state": 3}',
            "[" * 100_000,  # nested too deep to parse
            "�� torn bytes",
        ]
        report = summarise(good + bad + ["", "   "], 7)
        self.assertEqual(report.skipped_lines, len(bad))
        self.assertEqual([r.n for r in report.rows], [1, 1, 1])

    def test_nights_must_be_a_positive_int(self) -> None:
        for bad, error in ((0, ValueError), (-1, ValueError), (True, TypeError), (2.0, TypeError)):
            with self.subTest(nights=bad), self.assertRaises(error):
                summarise([], bad)


class GateCountsTest(unittest.TestCase):
    """R2-R4."""

    def test_five_nights_review_blocks_on_two_research_never_blocks(self) -> None:
        lines = [
            line
            for i, ts in enumerate(NIGHTS[:5])
            for line in night(ts, review_blocks=i in (1, 3))
        ]
        report = summarise(lines, 7)
        review, research = row(report, "review").gate, row(report, "research").gate
        self.assertEqual((review.ran, review.blocked), (5, 2))
        self.assertEqual((review.nights_ran, review.nights_blocked), (5, 2))
        self.assertEqual((research.nights_ran, research.nights_blocked), (5, 0))
        text = render_report(report)
        self.assertIn("5 night(s) of data (asked for 7)", text)
        research_line = next(l for l in text.splitlines() if l.startswith("research\t5\t0\t"))
        self.assertTrue(research_line.endswith("never blocked in 5 night(s)"), research_line)
        review_line = next(l for l in text.splitlines() if l.startswith("review\t5\t2\t"))
        self.assertTrue(review_line.endswith("\t-"), review_line)

    def test_the_header_omits_asked_for_when_the_window_is_full(self) -> None:
        lines = [line for ts in NIGHTS[:3] for line in night(ts, review_blocks=False)]
        self.assertTrue(render_report(summarise(lines, 3)).startswith("metrics: 3 night(s) of data\n"))

    def test_a_gate_that_never_ran_is_not_gated_not_blocked(self) -> None:
        # CLI error / timeout / unreadable output: the gate never fired.
        lines = [
            metric_line(NIGHTS[0], "build", S.CLI_ERROR, None, rc=1),
            metric_line(NIGHTS[1], "build", S.TIMED_OUT, None, rc=-15),
            metric_line(NIGHTS[2], "build", S.UNREADABLE, None, parse_error=True),
        ]
        gate = row(summarise(lines, 7), "build").gate
        self.assertEqual((gate.ran, gate.blocked, gate.not_gated), (0, 0, 3))
        self.assertEqual((gate.nights_ran, gate.nights_blocked), (0, 0))

    def test_an_ungated_phase_is_reported_ungated_never_ran(self) -> None:
        lines = [metric_line(ts, "research", S.GATED_PASS, "NONE") for ts in NIGHTS[:4]]
        report = summarise(lines, 7)
        gate = row(report, "research").gate
        self.assertEqual((gate.ran, gate.ungated, gate.nights_ran), (0, 4, 0))
        line = next(l for l in render_report(report).splitlines() if l.startswith("research\t0\t"))
        self.assertTrue(line.endswith("\tungated"), line)

    def test_nights_count_distinct_run_ts_not_lines(self) -> None:
        # Two cycles in one night, both blocked: one night, two blocks.
        lines = [
            metric_line(NIGHTS[0], "review", S.GATE_FAILED, "FAIL", cycle=1),
            metric_line(NIGHTS[0], "review", S.GATE_FAILED, "MALFORMED", cycle=2),
            metric_line(NIGHTS[1], "review", S.GATED_PASS, "PASS"),
        ]
        gate = row(summarise(lines, 7), "review").gate
        self.assertEqual((gate.ran, gate.blocked, gate.nights_ran, gate.nights_blocked), (3, 2, 2, 1))

    def test_a_phase_skipped_on_a_night_does_not_count_that_night(self) -> None:
        # Night 2 ended at research, so build wrote no line.
        lines = night(NIGHTS[0], review_blocks=False) + [
            metric_line(NIGHTS[1], "research", S.GATE_FAILED, "STALE"),
        ]
        report = summarise(lines, 7)
        self.assertEqual(report.nights_found, 2)
        self.assertEqual(row(report, "build").gate.nights_ran, 1)
        self.assertEqual(row(report, "research").gate.nights_blocked, 1)

    def test_bash_lines_without_outcome_block_on_a_refusing_state(self) -> None:
        lines = [
            bash_line(NIGHTS[0], "research", "FRESH"),
            bash_line(NIGHTS[1], "research", "STALE"),
            bash_line(NIGHTS[2], "research", "MISSING"),
            bash_line(NIGHTS[3], "research", None, rc=1),
            bash_line(NIGHTS[3], "review", "NONE"),
        ]
        report = summarise(lines, 7)
        gate = row(report, "research").gate
        self.assertEqual((gate.ran, gate.blocked, gate.not_gated), (3, 2, 1))
        self.assertEqual(row(report, "review").gate.ungated, 1)

    def test_refusing_states_are_exactly_the_gate_outcomes_that_do_not_advance(self) -> None:
        pairs = [(Postcondition.REVIEW_VERDICT, v) for v in Verdict] + [
            (Postcondition.RESEARCH_NOTE_FRESH, f) for f in Freshness
        ]
        refusing = {o.name for post, o in pairs if not GateResult(post, o, "x").advance}
        self.assertEqual(REFUSING_STATES, refusing)

    def test_rows_come_in_phase_order_then_other_phases_sorted(self) -> None:
        lines = [
            bash_line(NIGHTS[0], "maintain", "NONE"),
            bash_line(NIGHTS[0], "review", "NONE"),
            bash_line(NIGHTS[0], "health", "NONE"),
            bash_line(NIGHTS[0], "research", "FRESH"),
        ]
        self.assertEqual([r.phase for r in summarise(lines, 1).rows],
                         ["research", "review", "health", "maintain"])


class MediansTest(unittest.TestCase):
    """R5, and ``ok``."""

    def test_medians_skip_nulls_and_show_a_dash_when_nothing_remains(self) -> None:
        lines = [
            metric_line(NIGHTS[0], "build", S.GATED_PASS, "FRESH", duration_ms=60_000,
                        num_turns=10, output_tokens=100, cost=0.10),
            metric_line(NIGHTS[1], "build", S.GATED_PASS, "FRESH", duration_ms=None,
                        num_turns=20, output_tokens=None, cost=0.30),
            metric_line(NIGHTS[2], "build", S.GATED_PASS, "FRESH", duration_ms=180_000,
                        num_turns=None, output_tokens=None, cost=None),
            metric_line(NIGHTS[3], "build", S.UNREADABLE, None, parse_error=True),
        ]
        build = row(summarise(lines, 7), "build")
        self.assertEqual(build.median_min, 2.0)  # (1 + 3) / 2: the null is not a zero
        self.assertEqual(build.median_turns, 15.0)
        self.assertEqual(build.median_out_tokens, 100.0)
        self.assertAlmostEqual(build.median_cost_usd, 0.2)

        only_unreadable = row(summarise(lines[3:], 7), "build")
        self.assertIsNone(only_unreadable.median_min)
        table = render_report(summarise(lines[3:], 7)).splitlines()
        build_line = next(l for l in table if l.startswith("build\t1\t"))
        self.assertEqual(build_line, "build\t1\t1\t-\t-\t-\t-")

    def test_duration_is_minutes_to_one_decimal_and_cost_to_cents(self) -> None:
        lines = [metric_line(NIGHTS[0], "build", S.GATED_PASS, "FRESH", duration_ms=749_771,
                             num_turns=54, output_tokens=70_295, cost=3.169061)]
        table = render_report(summarise(lines, 7)).splitlines()
        self.assertIn("build\t1\t1\t12.5\t54\t70295\t3.17", table)

    def test_ok_counts_rc_zero_only_and_a_bool_rc_is_not_zero(self) -> None:
        lines = [
            bash_line(NIGHTS[0], "build", "FRESH", rc=0),
            bash_line(NIGHTS[1], "build", None, rc=1),
            json.dumps({"run_ts": NIGHTS[2], "phase": "build", "rc": False,
                        "postcondition_state": None}),
        ]
        build = row(summarise(lines, 7), "build")
        self.assertEqual((build.n, build.ok), (3, 1))

    def test_wrong_typed_measurements_drop_out_of_their_median(self) -> None:
        lines = [
            json.dumps({"run_ts": NIGHTS[0], "phase": "build", "postcondition_state": None,
                        "duration_ms": "60000", "num_turns": True, "tokens": "lots",
                        "total_cost_usd": 10 ** 400}),
            bash_line(NIGHTS[1], "build", "FRESH"),
        ]
        build = row(summarise(lines, 7), "build")
        self.assertEqual((build.median_min, build.median_turns), (1.0, 5.0))
        self.assertEqual((build.median_out_tokens, build.median_cost_usd), (10.0, 0.1))


if __name__ == "__main__":
    unittest.main()
