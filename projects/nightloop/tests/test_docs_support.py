"""The docs drift guard's parsers catch what they claim to, on synthetic Markdown.

``test_docs.py`` passes on today's docs; these tests show each parser would
also see a stale reference, so that pass is not vacuous.
"""

from __future__ import annotations

import unittest

from .docs_support import (
    NO_INCIDENT,
    exit_code_table,
    forbidden_words,
    gate_sections,
    heading_anchors,
    invocations,
    is_external,
    links,
    marked_fence,
    split_fences,
    split_target,
)


class Fences(unittest.TestCase):
    def test_fences_are_split_out_and_blanked_in_prose(self) -> None:
        prose, fences = split_fences("a\n```sh\nx\ny\n```\nb")
        self.assertEqual(prose.split("\n"), ["a", "", "", "", "", "b"])
        self.assertEqual([(f.info, f.lines) for f in fences], [("sh", ("x", "y"))])

    def test_an_unclosed_fence_raises(self) -> None:
        with self.assertRaises(ValueError):
            split_fences("a\n```sh\nx\n")


class Links(unittest.TestCase):
    def test_links_outside_code_are_found_and_links_in_code_are_not(self) -> None:
        md = "[a](x.md) `[b](y.md)` ![i](img.png)\n```\n[c](z.md)\n```\n[d](https://e.org#f)"
        self.assertEqual(links(md), ["x.md", "img.png", "https://e.org#f"])

    def test_a_link_title_is_not_part_of_the_target(self) -> None:
        self.assertEqual(links('[a](x.md "t")'), ["x.md"])

    def test_external_and_target_split(self) -> None:
        self.assertTrue(is_external("https://x"))
        self.assertTrue(is_external("mailto:a@b"))
        self.assertFalse(is_external("../a.md#b"))
        self.assertEqual(split_target("a.md#b"), ("a.md", "b"))
        self.assertEqual(split_target("#b"), ("", "b"))
        self.assertEqual(split_target("a.md"), ("a.md", None))


class Anchors(unittest.TestCase):
    def test_github_slugs_with_punctuation_and_repeats(self) -> None:
        md = "# Gate: Run window\n## 6. Make it (human step)\n## Notes\n## Notes\n```\n# not a heading\n```"
        self.assertEqual(
            heading_anchors(md),
            {"gate-run-window", "6-make-it-human-step", "notes", "notes-1"},
        )


class Invocations(unittest.TestCase):
    def test_commands_in_fences_and_spans_with_words_and_flags(self) -> None:
        md = (
            "```sh\n"
            "cd x && python3 -m nightloop run --dry-run \\\n  --repo /r  # comment --nope\n"
            "nightloop schedule install --dry-run | tee out\n"
            "```\n"
            "Use `nightloop report --nights 3` or `nightloop --help`."
        )
        got = [(i.words, i.flags) for i in invocations(md)]
        self.assertEqual(got, [
            (("run",), ("--dry-run", "--repo")),
            (("schedule", "install"), ("--dry-run",)),
            (("report",), ("--nights",)),
        ])

    def test_prose_mentions_are_not_commands(self) -> None:
        self.assertEqual(invocations("nightloop frobnicate is mentioned in prose."), [])

    def test_an_unknown_subcommand_is_reported_as_written(self) -> None:
        [inv] = invocations("`nightloop frobnicate --fast`")
        self.assertEqual((inv.words, inv.flags), (("frobnicate",), ("--fast",)))

    def test_a_path_argument_is_not_an_action_word(self) -> None:
        [inv] = invocations("```\nnightloop run /tmp/x\n```")
        self.assertEqual(inv.words, ("run",))


class ExitCodes(unittest.TestCase):
    TABLE = "## Exit codes\n\n| Code | Name |\n|---|---|\n| 0 | `OK` | fine |\n| 3 | `REFUSED` | no |\n\n## Next\n| 9 | `LATER` |"

    def test_rows_of_the_section_only(self) -> None:
        self.assertEqual(exit_code_table(self.TABLE), {"OK": 0, "REFUSED": 3})

    def test_duplicates_missing_section_and_empty_section_raise(self) -> None:
        for md in (
            "## Exit codes\n| 0 | `OK` |\n| 0 | `ALSO` |",
            "## Exit codes\n| 0 | `OK` |\n| 1 | `OK` |",
            "## Other\n| 0 | `OK` |",
            "## Exit codes\nnone here",
        ):
            with self.subTest(md=md), self.assertRaises(ValueError):
                exit_code_table(md)


class Gates(unittest.TestCase):
    def test_fields_with_wrapped_values_and_repeats(self) -> None:
        md = (
            "## Gate: One\n\nProse.\n\n"
            "- **Module:** `a.py`,\n  `b.py`\n- **Tests:** `tests/test_a.py`\n"
            f"- **Incident:** {NO_INCIDENT}\n"
            "## Gate: Two\n- **Incident:** it broke\n- **Source:** [s](x.md)\n- **Source:** [t](y.md)\n"
            "## Other\n- **Module:** `ignored.py`\n"
        )
        one, two = gate_sections(md)
        self.assertEqual(one.name, "One")
        self.assertEqual(one.fields["Module"], ("`a.py`, `b.py`",))
        self.assertEqual(one.fields["Incident"], (NO_INCIDENT,))
        self.assertEqual(two.fields["Source"], ("[s](x.md)", "[t](y.md)"))
        self.assertNotIn("Module", two.fields)


class Markers(unittest.TestCase):
    def test_the_fence_after_the_marker(self) -> None:
        md = "<!-- docs-check: cmd -->\n\n```sh\nrun it\n```\n```\nother\n```"
        self.assertEqual(marked_fence(md, "cmd").lines, ("run it",))

    def test_missing_repeated_or_unfenced_markers_raise(self) -> None:
        for md in (
            "no marker",
            "<!-- docs-check: cmd -->\n```\na\n```\n<!-- docs-check: cmd -->\n```\nb\n```",
            "<!-- docs-check: cmd -->\nprose\n```\na\n```",
        ):
            with self.subTest(md=md), self.assertRaises(ValueError):
                marked_fence(md, "cmd")


class Placeholders(unittest.TestCase):
    def test_placeholder_markers_are_found(self) -> None:
        self.assertEqual(forbidden_words("a TODO b tbd. Lorem ipsum. todos XXXL"), ["TODO", "tbd", "Lorem ipsum"])


if __name__ == "__main__":
    unittest.main()
