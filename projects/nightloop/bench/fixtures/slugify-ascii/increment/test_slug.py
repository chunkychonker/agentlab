import unittest

from slug import slugify


class SlugifyTest(unittest.TestCase):
    def test_accents_are_folded_to_ascii(self):
        self.assertEqual(slugify("Café au lait"), "cafe-au-lait")

    def test_case_is_lowered(self):
        self.assertEqual(slugify("Hello World"), "hello-world")

    def test_runs_of_other_characters_become_one_hyphen(self):
        self.assertEqual(slugify("a  --  b!!c"), "a-b-c")

    def test_leading_and_trailing_hyphens_are_removed(self):
        self.assertEqual(slugify("  ...Title...  "), "title")

    def test_digits_are_kept(self):
        self.assertEqual(slugify("Release 2026.11"), "release-2026-11")

    def test_the_result_is_cut_to_max_len_without_a_trailing_hyphen(self):
        self.assertEqual(slugify("abc def", max_len=4), "abc")
        self.assertEqual(slugify("abcdef", max_len=4), "abcd")
        self.assertLessEqual(len(slugify("word " * 40)), 60)

    def test_an_empty_result_raises(self):
        for text in ("", "!!!", "日本", "   "):
            with self.subTest(text=text), self.assertRaises(ValueError):
                slugify(text)

    def test_a_bad_max_len_raises(self):
        for bad in (0, -1, True, 2.5, "10"):
            with self.subTest(max_len=bad), self.assertRaises(ValueError):
                slugify("title", max_len=bad)


if __name__ == "__main__":
    unittest.main()
