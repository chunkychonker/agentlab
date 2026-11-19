"""Milestone 7, W1: ``window_disposition`` judges the hour it is handed.

Numbers refer to research/2026-11-19-nightloop-cli.md. The window is
``[start, end)``, the same as bash's ``schedule_disposition``.
"""

from __future__ import annotations

import unittest

from nightloop.cli.window import Refuse, Run, window_disposition
from nightloop.model import Window

NIGHT = Window(start_hour=1, end_hour=6)


class WindowTest(unittest.TestCase):
    def test_every_hour_of_the_day_against_bash_semantics(self) -> None:
        for hour in range(24):
            with self.subTest(hour=hour):
                got = window_disposition(hour, NIGHT, ignore_window=False)
                self.assertIsInstance(got, Run if 1 <= hour < 6 else Refuse)

    def test_the_start_hour_runs_and_the_end_hour_refuses(self) -> None:
        self.assertIsInstance(window_disposition(1, NIGHT, ignore_window=False), Run)
        self.assertIsInstance(window_disposition(5, NIGHT, ignore_window=False), Run)
        self.assertIsInstance(window_disposition(6, NIGHT, ignore_window=False), Refuse)
        self.assertIsInstance(window_disposition(0, NIGHT, ignore_window=False), Refuse)

    def test_eight_and_nine_are_plain_ints(self) -> None:
        # bash's octal trap: 08 and 09 abort arithmetic. Ints have no such trap.
        morning = Window(start_hour=8, end_hour=10)
        self.assertIsInstance(window_disposition(8, morning, ignore_window=False), Run)
        self.assertIsInstance(window_disposition(9, morning, ignore_window=False), Run)
        self.assertIsInstance(window_disposition(10, morning, ignore_window=False), Refuse)

    def test_a_window_to_midnight_includes_hour_23(self) -> None:
        late = Window(start_hour=22, end_hour=24)
        self.assertIsInstance(window_disposition(23, late, ignore_window=False), Run)
        self.assertIsInstance(window_disposition(21, late, ignore_window=False), Refuse)

    def test_ignore_window_runs_outside_the_window(self) -> None:
        got = window_disposition(14, NIGHT, ignore_window=True)
        self.assertIsInstance(got, Run)
        self.assertIn("--ignore-window", got.reason)

    def test_a_nonsense_hour_refuses_even_when_the_window_is_ignored(self) -> None:
        for hour in (24, -1, 25, True, False, "3", 3.0, None):
            for ignore in (False, True):
                with self.subTest(hour=hour, ignore=ignore):
                    self.assertIsInstance(window_disposition(hour, NIGHT, ignore_window=ignore), Refuse)

    def test_the_refusal_names_the_hour_and_both_window_hours(self) -> None:
        got = window_disposition(14, NIGHT, ignore_window=False)
        self.assertIsInstance(got, Refuse)
        for text in ("14", "01:00", "06:00", "--ignore-window"):
            self.assertIn(text, got.reason)
        self.assertNotIn("\n", got.reason)


if __name__ == "__main__":
    unittest.main()
