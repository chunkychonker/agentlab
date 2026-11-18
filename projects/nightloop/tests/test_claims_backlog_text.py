"""Milestone 6: the pure backlog edits (C-count, C-literal, C-stranded, C-file, C-done, I1-I4).

Ports ``.pipeline/test_backlog.sh`` C17-C21, C24-C29 and C36-C39 against the
spec in research/2026-11-18-nightloop-claim-tracking.md. The item texts
are the real ones the bash cases use: backticks, parentheses, brackets
(``[building]`` and ``[not prose]`` inside the text) and colons, so a
pattern-built match would misfire on them.
"""

from __future__ import annotations

import itertools
import unittest

from nightloop.claims import (
    AlreadyHandled,
    Claim,
    ItemNotFound,
    PrNumber,
    Rewritten,
    StrandedBranch,
    apply_stranded,
    count_unclaimed,
    file_stranded,
    mark_done,
)
from nightloop.claims.markers import STRANDED_SECTION

KEY = "Server-side compaction (`compact_20260112`, beta `compact-2026-01-12`): the"
UGLY = "`thinking` blocks (streaming): assemble `input_json_delta` [not prose] after a [building] step"
BRANCH = StrandedBranch("cycle/2026-08-12-unshipped-213702-1")
OTHER_BRANCH = StrandedBranch("origin/cycle/2026-08-12-unshipped-213702-1")
PR = PrNumber(123)

INVENTED_KEY = "MCP's other transport: Streamable HTTP, not stdio. Every MCP example"
INVENTED_REST = (
    "  in the lab so far (`mcp-hello-world`) either uses stdio or bypasses the",
    "  wire entirely via the SDK's in-memory `Client`.",
)
INVENTED = Claim(INVENTED_KEY, "\n".join((INVENTED_KEY, *INVENTED_REST)))


def backlog(marker: str, key: str = KEY) -> str:
    """The bash C15-C30 fixture: one item under ``marker`` plus decoys, one sharing its prefix."""
    return (
        "# BACKLOG\n"
        "\n"
        "## Context & cost\n"
        "- [done #25] Previewing server-side context editing\n"
        f"- [{marker}] {key}\n"
        "  summarize-don't-prune sibling of context editing. Different response\n"
        f"- [ ] {key} and a longer item sharing its first words\n"
        f"  - [ ] {key}\n"
        "- [ ] an unrelated unclaimed item\n"
        "- [researching] a different cycle's claim\n"
    )


def changed_lines(before: str, after: str) -> list[tuple[str, str]]:
    old, new = before.split("\n"), after.split("\n")
    assert len(old) == len(new), "a rewrite must not add or drop lines"
    return [(a, b) for a, b in zip(old, new) if a != b]


class CountUnclaimedTest(unittest.TestCase):
    def test_only_a_column_zero_open_box_counts(self) -> None:
        text = (
            "- [ ] one\n"
            "  - [ ] indented\n"
            "- [building] two\n"
            "- [researching] three\n"
            "- [done #3] four\n"
            "- [stranded cycle/x] five\n"
            "-  [ ] wrong spacing\n"
            "- [ ]no space after\n"
            "- [ ] six"
        )
        self.assertEqual(count_unclaimed(text), 2)

    def test_an_empty_backlog_counts_zero(self) -> None:
        self.assertEqual(count_unclaimed(""), 0)

    def test_crlf_lines_count(self) -> None:
        self.assertEqual(count_unclaimed("- [ ] a\r\n- [ ] b\r\n"), 2)

    def test_a_non_str_is_a_type_error(self) -> None:
        with self.assertRaises(TypeError):
            count_unclaimed(b"- [ ] a\n")  # type: ignore[arg-type]


class ApplyStrandedTest(unittest.TestCase):
    def test_c18_an_open_item_is_marked_and_leaves_the_count(self) -> None:
        before = backlog(" ")
        edit = apply_stranded(before, Claim(KEY, KEY), BRANCH)
        self.assertIsInstance(edit, Rewritten)
        assert isinstance(edit, Rewritten)
        self.assertEqual(changed_lines(before, edit.text), [(f"- [ ] {KEY}", f"- [stranded {BRANCH}] {KEY}")])
        self.assertEqual(count_unclaimed(edit.text), count_unclaimed(before) - 1)

    def test_c_literal_a_bracketed_item_is_found(self) -> None:
        before = backlog(" ", UGLY)
        edit = apply_stranded(before, Claim(UGLY, UGLY), BRANCH)
        assert isinstance(edit, Rewritten), edit
        self.assertEqual(changed_lines(before, edit.text), [(f"- [ ] {UGLY}", f"- [stranded {BRANCH}] {UGLY}")])

    def test_c_literal_a_prefix_of_another_item_is_not_found(self) -> None:
        # The fixture holds "<KEY> and a longer item ..."; only the key itself may match.
        text = f"- [ ] {KEY} and a longer item sharing its first words\n"
        self.assertEqual(apply_stranded(text, Claim(KEY, KEY), BRANCH), ItemNotFound())
        self.assertEqual(apply_stranded(text, Claim(KEY[:-4], KEY[:-4]), BRANCH), ItemNotFound())

    def test_c19_c21_other_markers_are_already_handled(self) -> None:
        for marker in (f"stranded {OTHER_BRANCH}", "done #27", "building", "researching"):
            with self.subTest(marker=marker):
                self.assertEqual(apply_stranded(backlog(marker), Claim(KEY, KEY), BRANCH), AlreadyHandled())

    def test_c20_an_absent_item_is_not_found(self) -> None:
        self.assertEqual(apply_stranded(backlog(" "), Claim("reworded", "reworded"), BRANCH), ItemNotFound())

    def test_an_indented_copy_is_not_an_item(self) -> None:
        self.assertEqual(apply_stranded(f"  - [ ] {KEY}\n", Claim(KEY, KEY), BRANCH), ItemNotFound())

    def test_wrong_argument_types_are_type_errors(self) -> None:
        with self.assertRaises(TypeError):
            apply_stranded(backlog(" "), Claim(KEY, KEY), "cycle/x")  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            apply_stranded(backlog(" "), KEY, BRANCH)  # type: ignore[arg-type]


class FileStrandedTest(unittest.TestCase):
    def test_c36_the_item_is_appended_with_its_continuation_and_not_counted(self) -> None:
        before = backlog(" ")
        edit = file_stranded(before, INVENTED, BRANCH)
        assert isinstance(edit, Rewritten), edit
        self.assertTrue(edit.text.startswith(before), "nothing above the new section changes")
        tail = edit.text[len(before):].split("\n")
        self.assertEqual(tail[0], "")
        self.assertEqual(tail[1], STRANDED_SECTION)
        item_at = tail.index(f"- [stranded {BRANCH}] {INVENTED_KEY}")
        self.assertEqual(tuple(tail[item_at + 1:item_at + 3]), INVENTED_REST)
        self.assertEqual(edit.text[-1], "\n")
        self.assertEqual(count_unclaimed(edit.text), count_unclaimed(before))

    def test_c37_the_section_is_made_once(self) -> None:
        first = file_stranded(backlog(" "), INVENTED, BRANCH)
        assert isinstance(first, Rewritten)
        other = Claim("a second invented item", "a second invented item")
        second = file_stranded(first.text, other, OTHER_BRANCH)
        assert isinstance(second, Rewritten)
        self.assertEqual(second.text.count(STRANDED_SECTION), 1)
        lines = second.text.split("\n")
        self.assertEqual(
            lines.index(f"- [stranded {OTHER_BRANCH}] a second invented item"),
            lines.index(INVENTED_REST[-1]) + 1,
            "the second item goes after the first one's last continuation line",
        )

    def test_c38_c39_any_marker_on_the_key_is_already_handled(self) -> None:
        for marker in (f"stranded {BRANCH}", "done #44", " ", "building", "researching"):
            with self.subTest(marker=marker):
                text = f"# B\n- [{marker}] {INVENTED_KEY}\n"
                self.assertEqual(file_stranded(text, INVENTED, BRANCH), AlreadyHandled())

    def test_an_item_lands_inside_its_section_not_after_a_later_one(self) -> None:
        text = (
            "# B\n"
            f"{STRANDED_SECTION}\n"
            "intro\n"
            "- [stranded cycle/old] old item\n"
            "\n"
            "## Health-check findings\n"
            "- [ ] fix (health 2026-11-01): x\n"
        )
        edit = file_stranded(text, Claim("new item", "new item\n  wrapped"), BRANCH)
        assert isinstance(edit, Rewritten)
        self.assertEqual(
            edit.text,
            text.replace("old item\n", f"old item\n- [stranded {BRANCH}] new item\n  wrapped\n"),
        )

    def test_a_missing_final_newline_is_supplied_before_appending(self) -> None:
        edit = file_stranded("# B\n- [ ] last", Claim("n", "n"), BRANCH)
        assert isinstance(edit, Rewritten)
        self.assertTrue(edit.text.startswith("# B\n- [ ] last\n\n" + STRANDED_SECTION + "\n"))
        section = f"{STRANDED_SECTION}\nintro\n- [stranded cycle/a] a"
        edit = file_stranded(section, Claim("n", "n"), BRANCH)
        assert isinstance(edit, Rewritten)
        self.assertEqual(edit.text, f"{section}\n- [stranded {BRANCH}] n\n")

    def test_an_empty_backlog_gets_just_the_section(self) -> None:
        edit = file_stranded("", Claim("n", "n"), BRANCH)
        assert isinstance(edit, Rewritten)
        self.assertTrue(edit.text.startswith(STRANDED_SECTION + "\n"))


class MarkDoneTest(unittest.TestCase):
    def test_c24_building_becomes_done_and_only_that_line_changes(self) -> None:
        before = backlog("building")
        edit = mark_done(before, KEY, PR)
        assert isinstance(edit, Rewritten), edit
        self.assertEqual(changed_lines(before, edit.text), [(f"- [building] {KEY}", f"- [done #123] {KEY}")])
        self.assertEqual(count_unclaimed(edit.text), count_unclaimed(before))

    def test_c29_a_bracketed_item_is_matched_literally(self) -> None:
        edit = mark_done(backlog("building", UGLY), UGLY, PR)
        assert isinstance(edit, Rewritten), edit
        self.assertIn(f"- [done #123] {UGLY}\n", edit.text)

    def test_c25_c27_other_markers_are_already_handled_and_keep_their_number(self) -> None:
        for marker in ("done #99", "researching", " ", f"stranded {BRANCH}"):
            with self.subTest(marker=marker):
                self.assertEqual(mark_done(backlog(marker), KEY, PR), AlreadyHandled())

    def test_c26_an_absent_item_is_not_found(self) -> None:
        self.assertEqual(mark_done(backlog("building"), "reworded", PR), ItemNotFound())
        self.assertEqual(mark_done(f"- [building] {KEY} and more\n", KEY, PR), ItemNotFound())

    def test_c28_a_bad_pr_number_or_key_is_refused(self) -> None:
        for text in ("", "#41", "041", "4a", "-1", " 41", "41\n", "٤١"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                PrNumber.parse(text)
        with self.assertRaises(TypeError):
            mark_done(backlog("building"), KEY, 123)  # type: ignore[arg-type]
        for key in ("", "   ", "two\nlines"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                mark_done(backlog("building"), key, PR)


class BytePreservationTest(unittest.TestCase):
    """I2: a rewrite keeps every other byte, including line endings and a missing final newline."""

    def test_no_final_newline_survives(self) -> None:
        text = f"# B\n- [building] {KEY}"
        edit = mark_done(text, KEY, PR)
        self.assertEqual(edit, Rewritten(f"# B\n- [done #123] {KEY}"))
        edit = apply_stranded(f"# B\n- [ ] {KEY}", Claim(KEY, KEY), BRANCH)
        self.assertEqual(edit, Rewritten(f"# B\n- [stranded {BRANCH}] {KEY}"))

    def test_crlf_endings_survive(self) -> None:
        text = f"# B\r\n- [building] {KEY}\r\n  more\r\n"
        self.assertEqual(mark_done(text, KEY, PR), Rewritten(f"# B\r\n- [done #123] {KEY}\r\n  more\r\n"))

    def test_odd_whitespace_elsewhere_survives(self) -> None:
        text = f"\n\n# B  \n\t\n- [ ] {KEY}\n\n\n"
        edit = apply_stranded(text, Claim(KEY, KEY), BRANCH)
        self.assertEqual(edit, Rewritten(text.replace("- [ ] ", f"- [stranded {BRANCH}] ")))


class IdempotenceTest(unittest.TestCase):
    """I1 over many backlogs: an edit applied to its own output is ``AlreadyHandled``; I3 alongside."""

    MARKERS = (" ", "building", "researching", "done #7", f"stranded {OTHER_BRANCH}", None)
    ENDINGS = ("\n", "", "\r\n")
    SECTIONS = ("", f"\n{STRANDED_SECTION}\nintro\n- [stranded cycle/a] a\n")

    def texts(self, key: str):
        for marker, ending, section in itertools.product(self.MARKERS, self.ENDINGS, self.SECTIONS):
            item = "" if marker is None else f"- [{marker}] {key}{ending}"
            yield f"# B\n- [ ] {key} longer\n{section}{item}"

    def test_every_edit_is_idempotent(self) -> None:
        claim = Claim(UGLY, UGLY + "\n  continued")
        edits = {
            "apply_stranded": lambda t: apply_stranded(t, claim, BRANCH),
            "file_stranded": lambda t: file_stranded(t, claim, BRANCH),
            "mark_done": lambda t: mark_done(t, UGLY, PR),
        }
        rewrites = 0
        for name, edit in edits.items():
            for text in self.texts(UGLY):
                with self.subTest(edit=name, text=text):
                    first = edit(text)
                    if isinstance(first, Rewritten):
                        rewrites += 1
                        self.assertEqual(edit(first.text), AlreadyHandled())
                        delta = count_unclaimed(text) - count_unclaimed(first.text)
                        was_open = f"- [ ] {UGLY}" in text.replace("\r", "").split("\n")
                        self.assertEqual(delta, 1 if name == "apply_stranded" and was_open else 0)
                    else:
                        self.assertEqual(edit(text), first, "a no-op is stable")
        self.assertGreater(rewrites, 10, "the property must exercise real rewrites")


class StrandedBranchTest(unittest.TestCase):
    def test_i4_a_blank_or_spaced_branch_is_refused(self) -> None:
        for name in ("", " ", "cycle/a b", "cycle/a\n"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                StrandedBranch(name)


if __name__ == "__main__":
    unittest.main()
