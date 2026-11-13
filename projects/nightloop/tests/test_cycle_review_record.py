"""Milestone 5, A9: the review record, ported from ``.pipeline/test_review_record.sh``.

Case ids in the test names (S, K, X, C) are the bash file's. The bash A
cases (``attach_review_to_pr``) have no port: here the record goes into
the body at creation, which ``test_cycle_gh_shipper.py`` covers.
"""

from __future__ import annotations

import unittest

from nightloop.cycle.review_record import (
    BODY_LIMIT,
    REVIEW_MARKER,
    PrBody,
    RecordRefused,
    compose_pr_body,
    has_marker,
)

from .gh_support import FINDINGS_LINES, VERDICT

SUMMARY = "Body text"


def body_of(verdict: str, summary: str = SUMMARY) -> str:
    composed = compose_pr_body(summary, verdict)
    assert isinstance(composed, PrBody), composed
    return composed.text


def section_of(verdict: str) -> str:
    """The part after the summary and its blank line."""
    return body_of(verdict)[len(SUMMARY) + 2:]


class SectionTest(unittest.TestCase):
    def test_s2_s3_the_section_starts_with_the_marker_then_the_heading(self) -> None:
        lines = section_of(VERDICT).splitlines()
        self.assertEqual(lines[0], REVIEW_MARKER)
        self.assertEqual(lines[1], "## Independent review")

    def test_s4_every_findings_bullet_is_in_the_section_verbatim(self) -> None:
        lines = section_of(VERDICT).splitlines()
        for finding in FINDINGS_LINES:
            self.assertIn(finding, lines)

    def test_s5_both_ran_lines_are_in_the_section(self) -> None:
        lines = section_of(VERDICT).splitlines()
        self.assertIn("python3 -m unittest -v -> 12 passed", lines)
        self.assertIn("ruff check projects/demo -> clean", lines)

    def test_s6_the_verdict_word_is_in_the_section(self) -> None:
        self.assertIn("**Verdict:** PASS", section_of(VERDICT))

    def test_s7_no_findings_line_is_refused(self) -> None:
        refused = compose_pr_body(SUMMARY, "VERDICT: PASS\nIncrement: x\nRan: y\n")
        self.assertIsInstance(refused, RecordRefused)
        self.assertIn("Findings", refused.reason)

    def test_s7b_findings_must_start_a_line(self) -> None:
        self.assertIsInstance(compose_pr_body(SUMMARY, "VERDICT: PASS\nRan: y\n  Findings: indented\n"),
                              RecordRefused)

    def test_s9_s10_a_spec_block_does_not_change_the_section(self) -> None:
        with_spec = VERDICT.replace(
            "Increment: projects/demo/\n",
            "Increment: projects/demo/\nSpec: research/2026-11-13-x.md\n- MET A1 - test: t.py\n- CUT A2 - README\n",
        )
        self.assertNotEqual(with_spec, VERDICT)
        self.assertEqual(section_of(with_spec), section_of(VERDICT))

    def test_missing_ran_and_empty_findings_say_so(self) -> None:
        section = section_of("VERDICT: FAIL because\nFindings:\n")
        self.assertIn("**Verdict:** FAIL", section)
        self.assertIn("**Ran:**\n\n(not stated)", section)
        self.assertIn("**Findings:**\n\n(none listed)", section)

    def test_no_verdict_word_is_unknown(self) -> None:
        self.assertIn("**Verdict:** unknown", section_of("Ran: x\nFindings: none\n"))

    def test_text_on_the_label_lines_is_kept(self) -> None:
        section = section_of("VERDICT: PASS\nRan: one command\nFindings: none at all\n")
        self.assertIn("**Ran:**\n\none command\n", section)
        self.assertIn("**Findings:**\n\nnone at all\n", section)


class MarkerTest(unittest.TestCase):
    def test_k1_k2(self) -> None:
        self.assertTrue(has_marker(body_of(VERDICT)))
        self.assertFalse(has_marker("Body text\n"))


class SecretTest(unittest.TestCase):
    FLAGGED = (
        "- blocker: hardcoded key sk-ant-api03-abcdefghijklmnop in config.py",  # X1
        "token ghp_abcdefghijklmnopqrstuvwxyz0123 leaked",  # X2
        "AWS AKIAIOSFODNN7EXAMPLE in .env",  # X3
        "-----BEGIN RSA PRIVATE KEY-----",  # X4
        "ANTHROPIC_API_KEY=abcd1234efgh5678",  # X5
        "github_pat_11ABCDEFG0123456789_abcdefghij",  # X6
        "- blocker: sk-ant-aaaaaaaaaaaa in app.py",  # the research note's A5 value
        "slack xoxb-1234567890-abc",
        "sk-abcdefghijklmnopqrstuvwxyz",
    )
    PASSED = (
        "- nit: commit 4f1c2e8b9a7d6c5b4a3f2e1d0c9b8a7f6e5d4c3b is fine",  # X7
        "- warning: task-runner retries forever",  # X8
        "Ran: export ANTHROPIC_API_KEY=$KEY from the env (no value inline)",  # X9
    )

    def test_x_flagged_patterns_refuse_and_return_no_review_text(self) -> None:
        for text in self.FLAGGED:
            with self.subTest(text=text):
                composed = compose_pr_body(SUMMARY, VERDICT + text + "\n")
                self.assertIsInstance(composed, RecordRefused)
                self.assertIn("credential pattern", composed.reason)
                self.assertNotIn(text, composed.reason)

    def test_x_clean_lines_pass(self) -> None:
        for text in self.PASSED:
            with self.subTest(text=text):
                self.assertIsInstance(compose_pr_body(SUMMARY, VERDICT + text + "\n"), PrBody)

    def test_a_key_value_secret_does_not_span_lines(self) -> None:
        self.assertIsInstance(compose_pr_body(SUMMARY, VERDICT + "- nit: PASSWORD=\nabcdefghijkl\n"), PrBody)

    def test_a_secret_in_the_summary_refuses_too(self) -> None:
        self.assertIsInstance(compose_pr_body("leak sk-ant-api03-abcdefghijklmnop", VERDICT), RecordRefused)


class ComposeTest(unittest.TestCase):
    def test_c1_a_small_body_and_section_compose_unchanged(self) -> None:
        body = body_of(VERDICT)
        self.assertTrue(body.startswith(SUMMARY + "\n\n" + REVIEW_MARKER + "\n"))
        self.assertNotIn("Truncated", body)

    def test_c2_to_c6_an_oversized_section_is_cut_to_fit(self) -> None:
        padding = "".join(f"- nit: finding number {i}, padded é with some text to make it long\n" for i in range(3000))
        body = body_of(VERDICT + padding)
        size = len(body.encode("utf-8"))
        self.assertLess(size, BODY_LIMIT)  # C2
        self.assertTrue(body.endswith("_"))
        self.assertIn("Truncated to fit", body.splitlines()[-1])  # C3
        self.assertEqual(body.splitlines()[0], SUMMARY)  # C4
        self.assertTrue(has_marker(body))  # C5
        body.encode("utf-8")  # C6: a str that encodes is valid UTF-8 (no surrogates)
        self.assertGreater(size, BODY_LIMIT - 400)  # it used the room rather than cutting short

    def test_c6_a_cut_never_splits_a_multibyte_character(self) -> None:
        # Every cut offset lands on a 2-, 3- or 4-byte character in turn.
        for shift in range(4):
            with self.subTest(shift=shift):
                padding = "x" * shift + "é€😀" * 20000
                body = body_of(VERDICT + padding + "\n")
                self.assertNotIn("�", body)
                self.assertLess(len(body.encode("utf-8")), BODY_LIMIT)

    def test_c7_a_summary_that_leaves_no_room_is_refused(self) -> None:
        refused = compose_pr_body("a" * 65500, VERDICT)
        self.assertIsInstance(refused, RecordRefused)
        self.assertIn("no room", refused.reason)

    def test_the_200_byte_floor(self) -> None:
        huge = VERDICT + "- nit: " + "y" * 70000 + "\n"
        # Find the longest summary that still composes; one byte more must refuse.
        fits = lambda n: isinstance(compose_pr_body("s" * n, huge), PrBody)  # noqa: E731
        low, high = 0, BODY_LIMIT  # fits(low) and not fits(high): bisect to the boundary
        self.assertTrue(fits(low))
        self.assertFalse(fits(high))
        while high - low > 1:
            mid = (low + high) // 2
            low, high = (mid, high) if fits(mid) else (low, mid)
        longest = low
        self.assertIsInstance(compose_pr_body("s" * (longest + 1), huge), RecordRefused)
        kept = compose_pr_body("s" * longest, huge).text[longest + 2:]
        section_kept = kept[: kept.index("\n\n_(Truncated")]
        self.assertEqual(len(section_kept.encode("utf-8")), 200)
        self.assertTrue(section_kept.startswith(REVIEW_MARKER))

    def test_pr_body_cannot_hold_an_illegal_value(self) -> None:
        with self.assertRaises(ValueError):
            PrBody("no marker here")
        with self.assertRaises(ValueError):
            PrBody(REVIEW_MARKER + "x" * BODY_LIMIT)
        with self.assertRaises(ValueError):
            RecordRefused("two\nlines")


if __name__ == "__main__":
    unittest.main()
