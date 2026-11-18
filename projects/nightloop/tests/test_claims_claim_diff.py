"""Milestone 6, C-diff: the claim read out of a backlog diff (bash C15-C17, C33-C35)."""

from __future__ import annotations

import unittest

from nightloop.claims import Claim, claim_from_diff

KEY = "Server-side compaction (`compact_20260112`, beta `compact-2026-01-12`): the"
INVENTED_KEY = "MCP's other transport: Streamable HTTP, not stdio. Every MCP example"
INVENTED_REST = (
    "  in the lab so far (`mcp-hello-world`) either uses stdio or bypasses the",
    "\twire entirely via the SDK's in-memory `Client`.",
)


def diff(*lines: str) -> str:
    return "\n".join(lines) + "\n"


class ClaimFromDiffTest(unittest.TestCase):
    def test_c15_c17_a_rewritten_claim_line_gives_its_key(self) -> None:
        text = diff(
            "diff --git a/BACKLOG.md b/BACKLOG.md",
            "--- a/BACKLOG.md",
            "+++ b/BACKLOG.md",
            "@@ -50,7 +50,7 @@",
            " ## Context & cost",
            f"-- [ ] {KEY}",
            f"+- [building] {KEY}",
            "   summarize-don't-prune sibling of context editing.",
        )
        self.assertEqual(claim_from_diff(text), Claim(KEY, KEY))

    def test_both_claim_markers_are_recognised(self) -> None:
        for marker in ("researching", "building"):
            with self.subTest(marker=marker):
                self.assertEqual(claim_from_diff(diff(f"+- [{marker}] {KEY}")), Claim(KEY, KEY))

    def test_c16_c34_no_claim_is_none(self) -> None:
        for text in (
            diff("@@ -1 +1 @@", "-old", "+new"),
            "",
            diff(f"+- [done #27] {KEY}", f"+- [stranded cycle/x] {KEY}", f"+- [ ] {KEY}"),
            diff(f"-- [building] {KEY}", f" - [building] {KEY}", f"+  - [building] {KEY}"),
            diff("+- [building] ", "+- [researching]    "),
        ):
            with self.subTest(text=text):
                self.assertIsNone(claim_from_diff(text))

    def test_c33_indented_added_lines_extend_the_item(self) -> None:
        text = diff(
            "@@ -100,6 +100,10 @@",
            " ## MCP",
            f"+- [researching] {INVENTED_KEY}",
            *(f"+{line}" for line in INVENTED_REST),
            " ",
        )
        claim = claim_from_diff(text)
        self.assertEqual(claim, Claim(INVENTED_KEY, "\n".join((INVENTED_KEY, *INVENTED_REST))))
        assert claim is not None
        self.assertEqual(claim.continuation, INVENTED_REST)

    def test_c35_a_column_zero_or_context_line_ends_the_item(self) -> None:
        text = diff(
            f"+- [researching] {INVENTED_KEY}",
            "+  its indented continuation",
            "+- [ ] a SEPARATE item added by the same commit",
            "+  whose continuation is not ours",
        )
        self.assertEqual(claim_from_diff(text), Claim(INVENTED_KEY, f"{INVENTED_KEY}\n  its indented continuation"))
        text = diff(f"+- [building] {KEY}", "   context, unchanged", "+  added later, not ours")
        self.assertEqual(claim_from_diff(text), Claim(KEY, KEY))

    def test_the_first_claim_wins(self) -> None:
        text = diff(f"+- [building] {KEY}", "+  mine", "+- [researching] second", "+  theirs")
        self.assertEqual(claim_from_diff(text), Claim(KEY, f"{KEY}\n  mine"))

    def test_a_bracketed_key_is_kept_whole(self) -> None:
        ugly = "`thinking` [building] (streaming): [not prose]"
        self.assertEqual(claim_from_diff(diff(f"+- [building] {ugly}")), Claim(ugly, ugly))

    def test_crlf_is_dropped(self) -> None:
        self.assertEqual(claim_from_diff(f"+- [building] {KEY}\r\n+  more\r\n"), Claim(KEY, f"{KEY}\n  more"))

    def test_a_non_str_is_a_type_error(self) -> None:
        with self.assertRaises(TypeError):
            claim_from_diff(None)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
