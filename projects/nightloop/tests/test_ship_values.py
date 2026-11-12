"""Milestone 5: the pure ``ship`` pieces (review record, PR text, mergeable decision), S2 and S4's patterns.

Numbers refer to research/2026-11-12-nightloop-real-shipper.md.
"""

from __future__ import annotations

import unittest

from nightloop.cycle import RunId
from nightloop.ship.mergeable import Mergeable, decide
from nightloop.ship.pr_text import branch_name, pr_base_body, pr_number_from_url, pr_title
from nightloop.ship.review_record import (
    BODY_LIMIT,
    REVIEW_MARKER,
    compose_body,
    has_secret,
    review_section,
)

RUN = RunId("2026-11-12_020001")
SHA = "3f2a9c41d0e8b7a6c5f4e3d2c1b0a9f8e7d6c5b4"
VERDICT = f"""\
VERDICT: PASS
Spec: research/2026-11-12-note.md
Ran: python3 -m unittest
- 12 passed
Findings:
- nit: README typo at line 3 (see {SHA})
- warn: no test for the empty case
"""


class ReviewSectionTest(unittest.TestCase):
    def test_carries_marker_verdict_ran_and_findings_verbatim(self) -> None:
        section = review_section(VERDICT)
        self.assertTrue(section.startswith(REVIEW_MARKER + "\n## Independent review\n"))
        self.assertIn("**Verdict:** PASS\n", section)
        self.assertIn("**Ran:**\n\npython3 -m unittest\n- 12 passed\n\n**Findings:**", section)
        self.assertTrue(section.endswith(f"- nit: README typo at line 3 (see {SHA})\n- warn: no test for the empty case\n"))
        self.assertNotIn("Spec:", section)

    def test_no_findings_line_gives_none(self) -> None:
        self.assertIsNone(review_section("VERDICT: PASS\nRan: tests\nAll good.\n"))
        self.assertIsNone(review_section(""))
        self.assertIsNone(review_section("VERDICT: PASS\n  Findings: indented does not count\n"))

    def test_missing_pieces_are_named_not_invented(self) -> None:
        section = review_section("Findings:\n")
        self.assertIn("**Verdict:** unknown", section)
        self.assertIn("(not stated)", section)
        self.assertIn("(none listed)", section)

    def test_inline_findings_text_is_kept(self) -> None:
        self.assertIn("**Findings:**\n\nnone, ship it\n", review_section("VERDICT: PASS\nFindings: none, ship it\n"))

    def test_rejects_non_str(self) -> None:
        with self.assertRaises(TypeError):
            review_section(b"Findings:\n")


class SecretTest(unittest.TestCase):
    def test_s4_credential_patterns_are_caught(self) -> None:
        for secret in (
            "sk-ant-" + "a1B2c3D4e5F6g7H8i9J0",
            "ghp_" + "A" * 36,
            "github_pat_" + "x" * 30,
            "AKIA" + "ABCDEFGHIJKLMNOP",
            "xoxb-1234567890-abc",
            "-----BEGIN RSA PRIVATE KEY-----",
            "ANTHROPIC_API_KEY = abcdefgh12345",
            "DB_PASSWORD=hunter2hunter2",
        ):
            with self.subTest(secret=secret[:12]):
                self.assertTrue(has_secret(f"Findings:\n- leaked {secret} in the log\n"))

    def test_s4_commit_shas_and_placeholders_are_not_secrets(self) -> None:
        self.assertFalse(has_secret(review_section(VERDICT)))
        self.assertFalse(has_secret("set ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY or API_KEY=<your key>"))
        self.assertFalse(has_secret("sk-ant-short"))


class ComposeBodyTest(unittest.TestCase):
    def test_a_section_that_fits_follows_the_base_after_a_blank_line(self) -> None:
        self.assertEqual(compose_body("base text", "SECTION\n"), "base text\n\nSECTION\n")

    def test_s2_a_huge_section_is_cut_and_the_base_is_intact(self) -> None:
        base = "Opened by nightloop.\nLine two of the base."
        section = review_section("VERDICT: PASS\nFindings:\n" + "x" * 70_000 + "\n")
        body = compose_body(base, section)
        self.assertLessEqual(len(body.encode("utf-8")), BODY_LIMIT)
        self.assertTrue(body.startswith(base + "\n\n" + REVIEW_MARKER))
        self.assertTrue(body.endswith("The full verdict is in the run's verdict file.)_"))
        self.assertIn("Truncated to fit GitHub's 65536-character PR body limit", body)

    def test_s2_a_cut_never_splits_a_character(self) -> None:
        section = review_section("Findings:\n" + "é" * 40_000 + "\n")
        body = compose_body("base", section)
        self.assertLessEqual(len(body.encode("utf-8")), BODY_LIMIT)
        body.encode("utf-8").decode("utf-8")  # still valid UTF-8
        self.assertNotIn("�", body)

    def test_s2_too_little_room_gives_none(self) -> None:
        base = "b" * (BODY_LIMIT - 1 - 2 - 100)  # 100 bytes of room left
        self.assertIsNone(compose_body(base, review_section(VERDICT)))

    def test_s2_exactly_enough_room_fits_uncut(self) -> None:
        section = review_section(VERDICT)
        base = "b" * (BODY_LIMIT - 1 - 2 - len(section.encode("utf-8")))
        self.assertEqual(compose_body(base, section), base + "\n\n" + section)


class PrTextTest(unittest.TestCase):
    def test_branch_title_and_body_are_deterministic(self) -> None:
        self.assertEqual(branch_name(RUN, 2), "nightloop/2026-11-12_020001-2")
        self.assertEqual(pr_title(RUN, 2), "nightloop: cycle 2 of run 2026-11-12_020001")
        for text in (pr_title(RUN, 2), pr_base_body(RUN, 2)):
            self.assertNotIn("\n", text)
        self.assertEqual(pr_base_body(RUN, 2), pr_base_body(RUN, 2))

    def test_bad_cycles_are_rejected(self) -> None:
        for bad in (0, -1, True, "1", 1.0):
            with self.subTest(cycle=bad), self.assertRaises(ValueError):
                branch_name(RUN, bad)

    def test_pr_number_comes_only_from_a_pr_url_on_the_last_line(self) -> None:
        self.assertEqual(pr_number_from_url("https://github.com/acme/widgets/pull/123\n"), 123)
        self.assertEqual(pr_number_from_url("Creating pull request\n\nhttps://github.com/a/b/pull/7\n\n"), 7)
        self.assertEqual(pr_number_from_url("https://ghe.example.com:8443/a/b/pull/9"), 9)
        for bad in (
            "",
            "\n\n",
            "https://github.com/acme/widgets/pull/",
            "https://github.com/acme/widgets/pull/0",
            "https://github.com/acme/widgets/pull/12abc",
            "https://github.com/acme/widgets/issues/12",
            "http://github.com/acme/widgets/pull/12",
            "see https://github.com/acme/widgets/pull/12",
            "https://github.com/acme/widgets/pull/12\nwarning: something else",
            "https://github.com/acme/widgets/pull/١٢",
            "pull request 12 created",
        ):
            with self.subTest(stdout=bad):
                self.assertIsNone(pr_number_from_url(bad))


class MergeableTest(unittest.TestCase):
    def test_only_mergeable_is_ready_and_only_unknown_waits(self) -> None:
        self.assertIs(decide("MERGEABLE"), Mergeable.READY)
        self.assertIs(decide("UNKNOWN"), Mergeable.WAIT)
        for other in ("CONFLICTING", "", "mergeable", "MERGEABLE ", "null"):
            with self.subTest(value=other):
                self.assertIs(decide(other), Mergeable.BLOCKED)


if __name__ == "__main__":
    unittest.main()
