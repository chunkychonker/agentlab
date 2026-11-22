"""Milestone 9, open question 1: the ``launchctl print`` parse, on real captured output."""

from __future__ import annotations

import unittest

from nightloop.schedule import JobState, ScheduleError
from nightloop.schedule.launchd_print import parse_print

from .schedule_support import PRINT_IDLE, PRINT_NOT_FOUND_STDERR, PRINT_RUNNING


class ParsePrintTest(unittest.TestCase):
    def test_running(self) -> None:
        self.assertIs(parse_print(0, PRINT_RUNNING), JobState.RUNNING)

    def test_idle_ignores_nested_state_and_job_state_keys(self) -> None:
        self.assertIs(parse_print(0, PRINT_IDLE), JobState.LOADED_IDLE)

    def test_not_found(self) -> None:
        self.assertIs(parse_print(113, "", PRINT_NOT_FOUND_STDERR), JobState.NOT_LOADED)

    def test_other_exit_codes_raise_with_the_last_line(self) -> None:
        with self.assertRaises(ScheduleError) as caught:
            parse_print(5, "", "first\nInput/output error\n")
        self.assertIn("exited 5: Input/output error", str(caught.exception))

    def test_no_or_two_top_level_states_raise(self) -> None:
        for text in ("gui/1/x = {\n\t\tstate = active\n}\n",
                     "gui/1/x = {\n\tstate = running\n\tstate = waiting\n}\n"):
            with self.subTest(text=text), self.assertRaises(ScheduleError):
                parse_print(0, text)

    def test_an_unknown_state_is_not_guessed_idle(self) -> None:
        with self.assertRaises(ScheduleError) as caught:
            parse_print(0, "gui/1/x = {\n\tstate = exiting\n}\n")
        self.assertIn("'exiting'", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
