import unittest

from window import in_window


class InWindowTest(unittest.TestCase):
    def test_the_start_hour_is_inside(self):
        self.assertTrue(in_window(1, 1, 6))

    def test_the_hour_before_the_end_is_inside(self):
        self.assertTrue(in_window(5, 1, 6))

    def test_the_end_hour_is_outside(self):
        self.assertFalse(in_window(6, 1, 6))

    def test_the_hour_before_the_start_is_outside(self):
        self.assertFalse(in_window(0, 1, 6))

    def test_every_hour_against_a_hand_list(self):
        inside = [h for h in range(24) if in_window(h, 1, 6)]
        self.assertEqual(inside, [1, 2, 3, 4, 5])

    def test_end_24_runs_until_midnight(self):
        self.assertTrue(in_window(23, 20, 24))
        self.assertFalse(in_window(19, 20, 24))

    def test_bad_values_raise_naming_the_argument(self):
        cases = [
            ((24, 1, 6), "hour"), ((-1, 1, 6), "hour"), ((True, 1, 6), "hour"), ((1.0, 1, 6), "hour"),
            ((1, 24, 25), "start"), ((1, -1, 6), "start"), ((1, False, 6), "start"),
            ((1, 0, 0), "end"), ((1, 0, 25), "end"), ((1, 0, "6"), "end"),
            ((1, 6, 6), "end"), ((1, 22, 2), "end"),
        ]
        for args, name in cases:
            with self.subTest(args=args), self.assertRaises(ValueError) as caught:
                in_window(*args)
            self.assertIn(name, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
