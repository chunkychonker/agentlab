"""Milestone 5: the shipper's pure helpers (mergeable poll, gh output parsing, branch name)."""

from __future__ import annotations

import unittest

from nightloop.cycle import RunId
from nightloop.cycle.naming import recovery_branch
from nightloop.ship.mergeable import Mergeable, Polled, classify, poll
from nightloop.ship.naming import ship_branch
from nightloop.ship.pr_list import PrListError, open_pr
from nightloop.ship.pr_url import pr_number

RUN = RunId("2026-11-15_020003")


class MergeableTest(unittest.TestCase):
    def test_classify_knows_three_values_and_fails_closed_on_the_rest(self) -> None:
        self.assertIs(classify("MERGEABLE\n"), Mergeable.MERGEABLE)
        self.assertIs(classify("CONFLICTING"), Mergeable.CONFLICTING)
        self.assertIs(classify(" UNKNOWN "), Mergeable.UNKNOWN)
        for raw in ("mergeable", "", "MERGEABLE CLEAN", "true", "OTHER"):
            with self.subTest(raw=raw):
                self.assertIs(classify(raw), Mergeable.OTHER)

    def reads(self, values: list[str]):
        calls = []

        def read() -> str:
            calls.append(1)
            return values[min(len(calls), len(values)) - 1]

        return read, calls

    def test_polls_while_unknown_sleeping_only_between_reads(self) -> None:
        read, calls = self.reads(["UNKNOWN", "UNKNOWN", "MERGEABLE"])
        sleeps: list[float] = []
        self.assertEqual(poll(read, sleeps.append), Polled(Mergeable.MERGEABLE, "MERGEABLE"))
        self.assertEqual((len(calls), sleeps), (3, [2.0, 2.0]))

    def test_gives_up_after_the_attempts(self) -> None:
        read, calls = self.reads(["UNKNOWN"])
        sleeps: list[float] = []
        self.assertEqual(poll(read, sleeps.append, attempts=5, delay_s=0.5).state, Mergeable.UNKNOWN)
        self.assertEqual((len(calls), sleeps), (5, [0.5] * 4))

    def test_a_decided_value_stops_at_once(self) -> None:
        read, calls = self.reads(["CONFLICTING"])
        sleeps: list[float] = []
        self.assertEqual(poll(read, sleeps.append).state, Mergeable.CONFLICTING)
        self.assertEqual((len(calls), sleeps), (1, []))

    def test_the_shown_value_is_one_short_line(self) -> None:
        read, _ = self.reads(["weird\nmulti line " + "x" * 100])
        shown = poll(read, lambda _: None).shown
        self.assertNotIn("\n", shown)
        self.assertLessEqual(len(shown), 40)
        self.assertEqual(poll(lambda: "", lambda _: None).shown, "<empty>")

    def test_a_read_error_propagates(self) -> None:
        def read() -> str:
            raise RuntimeError("gh broke")

        with self.assertRaises(RuntimeError):
            poll(read, lambda _: None)

    def test_attempts_must_be_positive(self) -> None:
        with self.assertRaises(ValueError):
            poll(lambda: "MERGEABLE", lambda _: None, attempts=0)


class PrUrlTest(unittest.TestCase):
    def test_reads_the_number_from_the_printed_url(self) -> None:
        self.assertEqual(pr_number("https://github.com/acme/widgets/pull/42\n"), 42)
        self.assertEqual(pr_number("Warning: 2 uncommitted changes\nhttps://ghe.example/a/b/pull/9/\n"), 9)

    def test_no_pr_url_is_none(self) -> None:
        for out in ("", "Creating pull request...", "https://github.com/acme/widgets/issues/4",
                    "see https://github.com/acme/widgets/pull/4 for details",
                    "https://github.com/acme/widgets/pull/0", "https://github.com/a/b/pull/12345678901"):
            with self.subTest(out=out):
                self.assertIsNone(pr_number(out))


class PrListTest(unittest.TestCase):
    def test_empty_list_is_none_and_one_entry_is_its_number(self) -> None:
        self.assertIsNone(open_pr("[]\n"))
        self.assertEqual(open_pr('[{"number": 31, "url": "https://x/pull/31"}]'), 31)

    def test_unreadable_or_ambiguous_output_is_an_error(self) -> None:
        for out in ("", "not json", "{}", '[{"url": "u"}]', '[{"number": true}]', '[{"number": "3"}]',
                    '[{"number": 1}, {"number": 2}]', "[3]"):
            with self.subTest(out=out), self.assertRaises(PrListError):
                open_pr(out)


class ShipBranchTest(unittest.TestCase):
    def test_is_a_function_of_the_run_and_cycle_only(self) -> None:
        self.assertEqual(ship_branch(RUN, 1), "cycle/2026-11-15-020003-1-ship")
        self.assertEqual(ship_branch(RUN, 1), ship_branch(RUN, 1))
        self.assertNotEqual(ship_branch(RUN, 1), ship_branch(RUN, 2))

    def test_cannot_collide_with_a_recovery_branch(self) -> None:
        self.assertNotEqual(ship_branch(RUN, 1), recovery_branch(RUN, 1, "020003"))

    def test_rejects_a_bad_cycle(self) -> None:
        for cycle in (0, -1, True, 1.0):
            with self.subTest(cycle=cycle), self.assertRaises(ValueError):
                ship_branch(RUN, cycle)  # type: ignore[arg-type]

    def test_run_id_exposes_its_time(self) -> None:
        self.assertEqual(RUN.hhmmss, "020003")


if __name__ == "__main__":
    unittest.main()
