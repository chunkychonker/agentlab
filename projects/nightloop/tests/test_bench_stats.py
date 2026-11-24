"""Milestone 11, criterion 2 (I5): the Wilson score interval."""

from __future__ import annotations

import unittest

from nightloop.bench.stats import wilson


class WilsonKnownValuesTest(unittest.TestCase):
    def test_no_trials_raises(self) -> None:
        with self.assertRaises(ValueError):
            wilson(0, 0)

    def test_zero_of_ten(self) -> None:
        low, high = wilson(0, 10)
        self.assertEqual(low, 0.0)
        self.assertAlmostEqual(high, 0.2775, places=3)

    def test_ten_of_ten(self) -> None:
        low, high = wilson(10, 10)
        self.assertEqual(high, 1.0)
        self.assertAlmostEqual(low, 0.7225, places=3)

    def test_five_of_ten(self) -> None:
        low, high = wilson(5, 10)
        self.assertAlmostEqual(low, 0.2366, places=3)
        self.assertAlmostEqual(high, 0.7634, places=3)

    def test_the_research_notes_example(self) -> None:
        # 5/6 -> 44-97%, 0/6 -> 0-39% (research/2026-11-24-nightloop-reviewer-benchmark.md).
        self.assertEqual([round(x * 100) for x in wilson(5, 6)], [44, 97])
        self.assertEqual([round(x * 100) for x in wilson(0, 6)], [0, 39])


class WilsonPropertiesTest(unittest.TestCase):
    def test_bounds_are_in_unit_range_and_contain_the_estimate(self) -> None:
        for n in range(1, 31):
            for k in range(0, n + 1):
                low, high = wilson(k, n)
                with self.subTest(k=k, n=n):
                    self.assertTrue(0.0 <= low <= k / n <= high <= 1.0)

    def test_width_shrinks_as_n_grows_for_a_fixed_proportion(self) -> None:
        for p_num, p_den in ((1, 2), (1, 4), (0, 1), (1, 1), (1, 5)):
            widths = []
            for scale in range(1, 7):
                n = p_den * scale
                low, high = wilson(p_num * scale, n)
                widths.append(high - low)
            with self.subTest(p=f"{p_num}/{p_den}"):
                self.assertEqual(widths, sorted(widths, reverse=True))
                self.assertTrue(all(a > b for a, b in zip(widths, widths[1:])))

    def test_bad_arguments_raise(self) -> None:
        for args, error in (((-1, 5), ValueError), ((6, 5), ValueError), ((1, -3), ValueError),
                            ((True, 5), TypeError), ((1, 5.0), TypeError)):
            with self.subTest(args=args), self.assertRaises(error):
                wilson(*args)
        with self.assertRaises(ValueError):
            wilson(1, 5, z=0)
        with self.assertRaises(ValueError):
            wilson(1, 5, z=float("nan"))


if __name__ == "__main__":
    unittest.main()
