"""Milestone 10, criterion 4 (I5): a night whose worst case does not fit the job is refused."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nightloop.config import load_config
from nightloop.phase.subprocess_runner import TERM_GRACE_S
from nightloop.schedule_gha import JOB_SLACK_MINUTES, MAX_JOB_MINUTES, SpecError, worst_case_minutes
from nightloop.schedule_gha.budget import PHASE_KILL_GRACE_S, check_budget

from .cli_support import write_config
from .gha_support import plugin_config, valid


class WorstCaseTest(unittest.TestCase):
    def test_counts_every_phase_to_the_kill_and_rounds_up(self) -> None:
        # 1 x 3 x (3600 + 10) = 10830 s = 180.5 min -> 181
        self.assertEqual(worst_case_minutes(1, 3, 3600), 181)
        self.assertEqual(worst_case_minutes(2, 3, 3600), 361)
        self.assertEqual(worst_case_minutes(1, 3, 10), 1)  # 60 s exactly
        self.assertEqual(worst_case_minutes(1, 3, 11), 2)  # 63 s

    def test_grace_mirrors_the_runners_kill_grace(self) -> None:
        self.assertEqual(PHASE_KILL_GRACE_S, TERM_GRACE_S)

    def test_the_hosted_limit_is_six_hours(self) -> None:
        self.assertEqual(MAX_JOB_MINUTES, 360)


class CheckBudgetTest(unittest.TestCase):
    def test_boundary_table(self) -> None:
        # (cycles, phases, timeout_s, job minutes, accepted)
        fits = 181 + JOB_SLACK_MINUTES
        table = [
            (1, 3, 3600, fits, True),           # exactly enough
            (1, 3, 3600, fits - 1, False),      # one minute short
            (1, 3, 3600, MAX_JOB_MINUTES, True),
            (2, 3, 3600, MAX_JOB_MINUTES, False),  # the note's "2 cycles already exceed 6 h"
            (2, 3, 1800, MAX_JOB_MINUTES, True),   # 2 x 3 x 1810 s = 181 min + slack
            (1, 3, 60, MAX_JOB_MINUTES + 1, False),  # over the hosted limit, however small the night
        ]
        for cycles, phases, timeout_s, minutes, accepted in table:
            with self.subTest(cycles=cycles, timeout_s=timeout_s, minutes=minutes):
                if accepted:
                    needed = check_budget(cycles, phases, timeout_s, minutes)
                    self.assertLessEqual(needed, minutes)
                else:
                    with self.assertRaises(SpecError) as caught:
                        check_budget(cycles, phases, timeout_s, minutes)
                    self.assertEqual(caught.exception.field, "job_timeout_minutes")

    def test_a_rejection_names_both_numbers(self) -> None:
        with self.assertRaises(SpecError) as caught:
            check_budget(2, 3, 3600, 300)
        message = str(caught.exception)
        self.assertIn(str(361 + JOB_SLACK_MINUTES), message)
        self.assertIn("300", message)

    def test_over_the_hosted_limit_names_both_numbers(self) -> None:
        with self.assertRaises(SpecError) as caught:
            check_budget(1, 3, 60, 361)
        self.assertIn("361", str(caught.exception))
        self.assertIn(str(MAX_JOB_MINUTES), str(caught.exception))


class ValidateSpecBudgetTest(unittest.TestCase):
    """The budget uses the config's cycles and all three phases, through ``validate_spec``."""

    def test_config_cycles_drive_the_check(self) -> None:
        self.assertEqual(valid().needed_minutes, 181 + JOB_SLACK_MINUTES)
        with tempfile.TemporaryDirectory() as tmp:
            two = load_config(write_config(Path(tmp) / "n.toml", cycles=2))
        with self.assertRaises(SpecError) as caught:
            valid(two)
        self.assertEqual(caught.exception.field, "job_timeout_minutes")
        self.assertEqual(valid(two, timeout_s=1800).cycles, 2)

    def test_the_phase_count_is_three(self) -> None:
        self.assertEqual(valid(plugin_config()).phases, 3)


if __name__ == "__main__":
    unittest.main()
