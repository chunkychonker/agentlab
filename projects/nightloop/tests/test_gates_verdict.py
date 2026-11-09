"""Acceptance tests for milestone 2: the review-verdict gate.

``V<n>`` names are the case ids in agentlab's ``.pipeline/test_gates.sh``
(V13 reads the live repo and is not ported). Numbered comments refer to the
acceptance criteria in research/2026-11-09-nightloop-gates-library.md. Run
from projects/nightloop/:

    python3 -I -m unittest discover -v
"""

from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path

from nightloop.gates import Verdict, check_verdict, parse_verdict


def lines(*body: str) -> str:
    """The file test_gates.sh writes with `printf '%s\\n' "$@"`."""
    return "".join(f"{line}\n" for line in body)


class VerdictFileTest(unittest.TestCase):
    """Cases ported from test_gates.sh, run through real files like the bash suite."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def verdict_of(self, data: bytes | str) -> Verdict:
        path = self.dir / "verdict.md"
        path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
        return check_verdict(path)

    # 1. V1-V12, same expected word as the bash suite.
    def test_V1_absent_file_is_missing(self) -> None:
        self.assertIs(check_verdict(self.dir / "nope.md"), Verdict.MISSING)

    def test_V2_zero_byte_file_is_missing(self) -> None:
        self.assertIs(self.verdict_of(b""), Verdict.MISSING)

    def test_V2b_whitespace_only_file_is_malformed(self) -> None:
        self.assertIs(self.verdict_of(lines("", "   ")), Verdict.MALFORMED)

    def test_V3_clean_pass(self) -> None:
        body = lines("VERDICT: PASS", "Increment: examples/foo/", "Findings:", "- (nit) whatever")
        self.assertIs(self.verdict_of(body), Verdict.PASS)

    def test_V4_clean_fail(self) -> None:
        body = lines("VERDICT: FAIL", "Increment: examples/foo/", "Findings:", "- (blocker) it does not run")
        self.assertIs(self.verdict_of(body), Verdict.FAIL)

    def test_V5_no_verdict_line_is_malformed(self) -> None:
        body = lines("Increment: examples/foo/", "Looks good to me, ship it")
        self.assertIs(self.verdict_of(body), Verdict.MALFORMED)

    def test_V6_two_verdict_lines_are_malformed_not_a_vote(self) -> None:
        body = lines("VERDICT: PASS", "...on reflection...", "VERDICT: FAIL")
        self.assertIs(self.verdict_of(body), Verdict.MALFORMED)

    def test_V6_two_agreeing_verdict_lines_are_still_malformed(self) -> None:
        self.assertIs(self.verdict_of(lines("VERDICT: PASS", "VERDICT: PASS")), Verdict.MALFORMED)

    def test_V7_unrecognised_word_is_malformed(self) -> None:
        self.assertIs(self.verdict_of(lines("VERDICT: MAYBE")), Verdict.MALFORMED)

    def test_V8_lowercase_is_malformed(self) -> None:
        self.assertIs(self.verdict_of(lines("VERDICT: pass")), Verdict.MALFORMED)

    def test_V9_no_space_after_colon_parses(self) -> None:
        self.assertIs(self.verdict_of(lines("VERDICT:PASS")), Verdict.PASS)

    def test_V10_indented_verdict_line_does_not_count(self) -> None:
        self.assertIs(self.verdict_of(lines("  VERDICT: PASS")), Verdict.MALFORMED)

    def test_V11_pass_in_prose_does_not_authorise(self) -> None:
        body = lines("The increment would PASS if the test were fixed.", "I am not writing a verdict line.")
        self.assertIs(self.verdict_of(body), Verdict.MALFORMED)

    def test_V12_later_pass_in_prose_does_not_flip_fail(self) -> None:
        body = lines("VERDICT: FAIL", "The self-test would PASS if the import were fixed.")
        self.assertIs(self.verdict_of(body), Verdict.FAIL)

    # 1. Additional cases from the research note.
    def test_crlf_line_endings_parse(self) -> None:
        self.assertIs(self.verdict_of(b"VERDICT: PASS\r\nFindings:\r\n- none\r\n"), Verdict.PASS)

    def test_word_is_captured_whole_not_as_prefix(self) -> None:
        self.assertIs(self.verdict_of(lines("VERDICT: PASSED")), Verdict.MALFORMED)
        self.assertIs(self.verdict_of(lines("VERDICT: FAILURE")), Verdict.MALFORMED)

    def test_trailing_text_after_a_non_letter_is_ignored(self) -> None:
        self.assertIs(self.verdict_of(lines("VERDICT: PASS - ship it")), Verdict.PASS)
        self.assertIs(self.verdict_of(lines("VERDICT: FAIL.")), Verdict.FAIL)

    def test_verdict_after_unicode_line_separator_is_not_a_line_start(self) -> None:
        for sep in (" ", " ", "\x85", "\x0b", "\x0c", "\x1c"):
            with self.subTest(sep=repr(sep)):
                body = "Review prose." + sep + "VERDICT: PASS\n"
                self.assertIs(self.verdict_of(body), Verdict.MALFORMED)

    def test_invalid_utf8_elsewhere_does_not_break_a_pass(self) -> None:
        body = b"VERDICT: PASS\nFindings: caf\xe9 \xff\xfe broken bytes\n"
        self.assertIs(self.verdict_of(body), Verdict.PASS)

    def test_byte_order_mark_hides_the_column_0_verdict(self) -> None:
        # Parity with grep '^VERDICT:', which sees the BOM bytes first.
        self.assertIs(self.verdict_of(b"\xef\xbb\xbfVERDICT: PASS\n"), Verdict.MALFORMED)

    def test_final_line_without_newline_counts(self) -> None:
        self.assertIs(self.verdict_of(b"Findings: none\nVERDICT: PASS"), Verdict.PASS)

    def test_directory_is_missing(self) -> None:
        self.assertIs(check_verdict(self.dir), Verdict.MISSING)

    def test_symlink_to_a_regular_file_is_followed(self) -> None:
        target = self.dir / "real.md"
        target.write_text(lines("VERDICT: FAIL"))
        link = self.dir / "link.md"
        link.symlink_to(target)
        self.assertIs(check_verdict(link), Verdict.FAIL)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "needs os.mkfifo")
    def test_fifo_is_missing_and_does_not_hang(self) -> None:
        fifo = self.dir / "verdict.md"
        os.mkfifo(fifo)
        result: list[Verdict] = []
        worker = threading.Thread(target=lambda: result.append(check_verdict(fifo)), daemon=True)
        worker.start()
        worker.join(timeout=5)
        self.assertFalse(worker.is_alive(), "check_verdict blocked on a FIFO")
        self.assertEqual(result, [Verdict.MISSING])

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root can read mode-000 files")
    def test_unreadable_file_is_missing(self) -> None:
        path = self.dir / "verdict.md"
        path.write_text(lines("VERDICT: PASS"))
        path.chmod(0)
        self.addCleanup(path.chmod, 0o600)
        self.assertIs(check_verdict(path), Verdict.MISSING)


class ParseVerdictTest(unittest.TestCase):
    """The pure core, without files."""

    def test_none_means_unreadable_and_is_missing(self) -> None:
        self.assertIs(parse_verdict(None), Verdict.MISSING)

    def test_empty_string_is_missing(self) -> None:
        self.assertIs(parse_verdict(""), Verdict.MISSING)

    def test_verdict_line_with_no_word_is_malformed(self) -> None:
        self.assertIs(parse_verdict("VERDICT:\n"), Verdict.MALFORMED)
        self.assertIs(parse_verdict("VERDICT:   \n"), Verdict.MALFORMED)

    def test_whitespace_between_colon_and_word_is_ascii_only(self) -> None:
        self.assertIs(parse_verdict("VERDICT:\t PASS\n"), Verdict.PASS)
        # U+00A0 is whitespace to Python's \s but not to POSIX [[:space:]].
        self.assertIs(parse_verdict("VERDICT: PASS\n"), Verdict.MALFORMED)

    def test_verdict_must_be_the_only_line_starting_with_the_prefix(self) -> None:
        self.assertIs(parse_verdict("VERDICT: FAIL\nVERDICT:\n"), Verdict.MALFORMED)


if __name__ == "__main__":
    unittest.main()
