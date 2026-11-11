"""Milestone 4, B9: the pure usage-limit detector matches bash's ``usage_limit_reset``.

Cases L1a-L5c are ``.pipeline/test_usage_limit.sh``'s, including the two
real messages from 2026-10-19.
"""

from __future__ import annotations

import unittest

from nightloop.cycle import UNKNOWN_RESET, usage_limit_reset


class UsageLimitResetTest(unittest.TestCase):
    def test_real_messages_give_their_reset_time(self) -> None:  # L1a, L1b
        real1 = "--- phase: cycle 1/1: build (model: opus) ---\nYou've hit your session limit · resets 4:10pm (America/Chicago)\n"
        self.assertEqual(usage_limit_reset(real1), "4:10pm (America/Chicago)")
        self.assertEqual(
            usage_limit_reset("You've hit your session limit · resets 8:10pm (America/Chicago)"),
            "8:10pm (America/Chicago)",
        )

    def test_a_limit_without_a_reset_time_is_unknown(self) -> None:  # L1c
        self.assertEqual(usage_limit_reset("Claude usage limit reached.\n"), UNKNOWN_RESET)
        self.assertEqual(UNKNOWN_RESET, "unknown")

    def test_session_usage_and_weekly_wording_all_match(self) -> None:
        for word in ("session", "usage", "weekly"):
            with self.subTest(word=word):
                self.assertEqual(usage_limit_reset(f"You've hit your {word} limit · resets 9pm"), "9pm")

    def test_case_is_ignored_in_the_message(self) -> None:
        self.assertEqual(usage_limit_reset("YOU'VE HIT YOUR WEEKLY LIMIT · resets Monday"), "Monday")
        self.assertEqual(usage_limit_reset("Usage Limit Reached"), UNKNOWN_RESET)

    def test_only_the_r_of_resets_is_case_insensitive_as_in_bash(self) -> None:
        self.assertEqual(usage_limit_reset("hit your usage limit, Resets 6am"), "6am")
        self.assertEqual(usage_limit_reset("hit your usage limit, RESETS 6am"), UNKNOWN_RESET)

    def test_the_last_matching_line_wins(self) -> None:
        text = "hit your session limit · resets 1pm\nunrelated\nhit your session limit · resets 5pm\nmore\n"
        self.assertEqual(usage_limit_reset(text), "5pm")

    def test_resets_at_drops_the_at_and_trailing_space(self) -> None:
        self.assertEqual(usage_limit_reset("usage limit reached; resets at 3:00am  \r"), "3:00am")

    def test_the_last_resets_on_the_line_is_used(self) -> None:
        self.assertEqual(usage_limit_reset("hit your usage limit (resets soon) resets 7pm"), "7pm")

    def test_a_carriage_return_inside_the_reset_becomes_a_space(self) -> None:
        self.assertEqual(usage_limit_reset("hit your usage limit resets 7pm\rdone"), "7pm done")

    def test_ordinary_failures_are_not_limits(self) -> None:  # L5a
        ordinary = (
            "Error: rate limit of the GitHub API exceeded\nnpm ERR! session expired\n"
            "VERIFY: FAIL (1): routes stay in corridors\ntoken limit for this example is 4096\n"
        )
        self.assertIsNone(usage_limit_reset(ordinary))

    def test_empty_text_is_not_a_limit(self) -> None:  # L5c
        self.assertIsNone(usage_limit_reset(""))

    def test_the_message_split_across_lines_does_not_match(self) -> None:
        self.assertIsNone(usage_limit_reset("hit your session\nlimit"))


if __name__ == "__main__":
    unittest.main()
