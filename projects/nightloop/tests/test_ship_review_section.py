"""Milestone 5: the review record section, its credential scan and the body-length cut.

The cases port agentlab's ``.pipeline/test_review_record.sh`` contract: no
``Findings:`` line is refused, a credential is refused without being
quoted, a commit SHA is not a credential, and only the review section is
ever cut.
"""

from __future__ import annotations

import unittest

from nightloop.ship.review_section import (
    BODY_LIMIT,
    MARKER,
    MIN_KEPT_BYTES,
    ReviewRecordError,
    compose_body,
    has_marker,
    review_section,
)

VERDICT = """\
VERDICT: PASS
Spec: research/2026-11-15-note.md
Ran: python3 -m unittest
  242 tests OK
Findings:
- warning: the timeout is not documented
- nit: typo in README
"""


class SectionTest(unittest.TestCase):
    def test_carries_marker_heading_verdict_ran_and_findings(self) -> None:
        section = review_section(VERDICT)
        self.assertTrue(section.startswith(MARKER + "\n## Independent review\n"))
        self.assertIn("**Verdict:** PASS\n", section)
        self.assertIn("**Ran:**\n\npython3 -m unittest\n  242 tests OK\n\n", section)
        self.assertTrue(section.endswith(
            "**Findings:**\n\n- warning: the timeout is not documented\n- nit: typo in README\n"
        ))
        self.assertNotIn("Spec:", section, "only Ran and Findings are posted")

    def test_findings_on_the_same_line_and_missing_blocks_have_placeholders(self) -> None:
        section = review_section("Findings: none\n")
        self.assertIn("**Verdict:** unknown", section)
        self.assertIn("**Ran:**\n\n(not stated)", section)
        self.assertIn("**Findings:**\n\nnone\n", section)
        self.assertIn("**Findings:**\n\n(none listed)\n", review_section("VERDICT: PASS\nFindings:\n\n"))

    def test_the_first_verdict_line_names_the_verdict(self) -> None:
        self.assertIn("**Verdict:** FAIL", review_section("VERDICT: FAIL (tests)\nVERDICT: PASS\nFindings: x\n"))
        self.assertIn("**Verdict:** unknown", review_section("VERDICT:\nFindings: x\n"))

    def test_a_ran_line_after_findings_is_part_of_findings(self) -> None:
        section = review_section("Findings: a\nRan: late\n")
        self.assertIn("**Ran:**\n\n(not stated)", section)
        self.assertIn("**Findings:**\n\na\nRan: late\n", section)

    def test_no_findings_line_is_refused(self) -> None:
        for text in ("VERDICT: PASS\nRan: x\n", "", "  Findings: indented does not count\n"):
            with self.subTest(text=text), self.assertRaises(ReviewRecordError):
                review_section(text)

    def test_credentials_are_refused_without_being_quoted(self) -> None:
        cases = [
            "sk-ant-abcdefgh12345",
            "sk-" + "a" * 20,
            "ghp_" + "A" * 20,
            "gho_" + "b" * 24,
            "github_pat_" + "c" * 22,
            "AKIA" + "ABCDEFGHIJKLMNOP",
            "xoxb-1234567890",
            "-----BEGIN RSA PRIVATE KEY-----",
            "API_KEY=supersecretvalue",
            "ACCESS_TOKEN_PROD = abcdefgh123",
        ]
        for secret in cases:
            with self.subTest(secret=secret):
                with self.assertRaises(ReviewRecordError) as caught:
                    review_section(f"VERDICT: PASS\nFindings: saw {secret} in the log\n")
                self.assertNotIn(secret, str(caught.exception))
                self.assertIn("credential", str(caught.exception))

    def test_a_secret_in_ran_is_caught_too(self) -> None:
        with self.assertRaises(ReviewRecordError):
            review_section("Ran: ANTHROPIC_API_KEY=sk-ant-abcdefgh12345 make test\nFindings: ok\n")

    def test_shas_and_placeholders_are_not_credentials(self) -> None:
        section = review_section(
            "Findings: fixed in 3f2a9c1e4b7d8a6f5e3c2b1a0f9e8d7c6b5a4f3e\n"
            "API_KEY=$ANTHROPIC_API_KEY is read from the env\n"
            "PASSWORD=<redacted>\n"
            "sk-short\n"
        )
        self.assertIn("3f2a9c1e", section)

    def test_has_marker(self) -> None:
        self.assertTrue(has_marker(f"intro\n\n{MARKER}\n## Independent review"))
        self.assertFalse(has_marker("<!-- agentlab-review"))


class ComposeTest(unittest.TestCase):
    def test_a_section_that_fits_follows_the_body_after_a_blank_line(self) -> None:
        self.assertEqual(compose_body("intro", "SECTION\n"), "intro\n\nSECTION\n")

    def test_only_the_section_is_cut_and_the_result_fits(self) -> None:
        intro = "intro " * 100
        section = review_section("Findings:\n" + "- finding é\n" * 10000)
        body = compose_body(intro, section)
        self.assertTrue(body.startswith(intro + "\n\n" + MARKER))
        self.assertLess(len(body.encode("utf-8")), BODY_LIMIT)
        self.assertIn("Truncated to fit GitHub's 65536-character PR body limit", body)
        body.encode("utf-8").decode("utf-8")  # no split multi-byte character

    def test_a_cut_never_splits_a_multibyte_character(self) -> None:
        section = "é" * BODY_LIMIT
        for pad in range(4):
            with self.subTest(pad=pad):
                body = compose_body("x" * (1000 + pad), section)
                self.assertNotIn("�", body)
                self.assertLess(len(body.encode("utf-8")), BODY_LIMIT)

    def test_a_body_that_leaves_too_little_room_is_refused(self) -> None:
        with self.assertRaises(ReviewRecordError):
            compose_body("x" * (BODY_LIMIT - MIN_KEPT_BYTES), "S" * 1000)

    def test_the_boundary_fits_exactly(self) -> None:
        intro = "x" * 100
        room = BODY_LIMIT - 1 - 100 - 2
        self.assertEqual(compose_body(intro, "s" * room), f"{intro}\n\n{'s' * room}")
        self.assertIn("Truncated", compose_body(intro, "s" * (room + 1)))


if __name__ == "__main__":
    unittest.main()
