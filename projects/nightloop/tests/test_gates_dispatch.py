"""Acceptance tests for milestone 2: ``evaluate`` and ``GateResult``.

Numbered comments refer to the acceptance criteria in
research/2026-11-09-nightloop-gates-library.md.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from nightloop.gates import Freshness, GateResult, Ungated, Verdict, evaluate
from nightloop.model import Postcondition

OLD = 1767225600  # 2026-01-01T00:00:00Z
FLOOR = 1780000000
NEW = FLOOR + 60
VERDICT_FILE = Path("logs/last-review.md")

# The spec's list, written out independently of the implementation.
ADVANCING = {Verdict.PASS, Freshness.FRESH, Ungated.NONE}

VERDICT_BODIES: dict[Verdict, bytes | None] = {
    Verdict.PASS: b"VERDICT: PASS\n",
    Verdict.FAIL: b"VERDICT: FAIL\n",
    Verdict.MALFORMED: b"looks fine\n",
    Verdict.MISSING: None,
}
# Freshness -> the mtime of the one note to write (None: write nothing).
FRESHNESS_MTIMES: dict[Freshness, int | None] = {
    Freshness.FRESH: NEW,
    Freshness.STALE: OLD,
    Freshness.MISSING: None,
}


def write(path: Path, data: bytes, mtime: float | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


class EvaluateTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self._n = 0

    def fresh_repo(self) -> Path:
        self._n += 1
        repo = self.base / f"repo{self._n}"
        repo.mkdir()
        return repo

    # 3. advance is True for exactly PASS, FRESH and NONE, over every outcome.
    def test_advance_table_over_every_outcome(self) -> None:
        seen: set[object] = set()
        for want, body in VERDICT_BODIES.items():
            repo = self.fresh_repo()
            if body is not None:
                write(repo / VERDICT_FILE, body)
            result = evaluate(Postcondition.REVIEW_VERDICT, repo, FLOOR, VERDICT_FILE)
            with self.subTest(outcome=want):
                self.assertIs(result.outcome, want)
                self.assertEqual(result.advance, want in ADVANCING)
            seen.add(result.outcome)
        for want, mtime in FRESHNESS_MTIMES.items():
            for post, rel in (
                (Postcondition.RESEARCH_NOTE_FRESH, "research/note.md"),
                (Postcondition.INCREMENT_BUILT, "projects/p/main.py"),
            ):
                repo = self.fresh_repo()
                if mtime is not None:
                    write(repo / rel, b"", mtime)
                result = evaluate(post, repo, FLOOR, VERDICT_FILE)
                with self.subTest(post=post, outcome=want):
                    self.assertIs(result.outcome, want)
                    self.assertEqual(result.advance, want in ADVANCING)
                seen.add(result.outcome)
        none = evaluate(Postcondition.NONE, self.fresh_repo(), FLOOR, VERDICT_FILE)
        self.assertIs(none.outcome, Ungated.NONE)
        self.assertTrue(none.advance)
        seen.add(none.outcome)
        self.assertEqual(seen, set(Verdict) | set(Freshness) | set(Ungated), "table must cover every outcome")

    def test_result_names_its_postcondition_and_logs_one_line(self) -> None:
        repo = self.fresh_repo()
        for post in Postcondition:
            result = evaluate(post, repo, FLOOR, VERDICT_FILE)
            with self.subTest(post=post):
                self.assertIs(result.postcondition, post)
                self.assertTrue(result.detail)
                self.assertNotIn("\n", result.detail)
                self.assertIn(result.outcome.name, result.detail)

    def test_detail_stays_one_line_for_a_path_with_a_newline(self) -> None:
        repo = self.base / "odd\nname"
        repo.mkdir()
        result = evaluate(Postcondition.RESEARCH_NOTE_FRESH, repo, FLOOR, VERDICT_FILE)
        self.assertIs(result.outcome, Freshness.MISSING)
        self.assertNotIn("\n", result.detail)

    # 4. Each gate reads only what it needs.
    def test_review_verdict_ignores_phase_started(self) -> None:
        repo = self.fresh_repo()
        write(repo / VERDICT_FILE, b"VERDICT: PASS\n", OLD)  # written long before the floor
        for floor in (0, OLD, FLOOR, 2**40):
            with self.subTest(floor=floor):
                self.assertIs(
                    evaluate(Postcondition.REVIEW_VERDICT, repo, floor, VERDICT_FILE).outcome,
                    Verdict.PASS,
                )

    def test_research_note_fresh_ignores_verdict_path(self) -> None:
        repo = self.fresh_repo()
        write(repo / "research" / "note.md", b"", NEW)
        write(repo / VERDICT_FILE, b"VERDICT: FAIL\n")
        for verdict_path in (VERDICT_FILE, Path("does/not/exist.md"), repo):
            with self.subTest(verdict_path=verdict_path):
                self.assertIs(
                    evaluate(Postcondition.RESEARCH_NOTE_FRESH, repo, FLOOR, verdict_path).outcome,
                    Freshness.FRESH,
                )

    def test_absolute_verdict_path_is_used_as_is(self) -> None:
        repo = self.fresh_repo()
        elsewhere = self.base / "review.md"
        write(elsewhere, b"VERDICT: PASS\n")
        result = evaluate(Postcondition.REVIEW_VERDICT, repo, FLOOR, elsewhere)
        self.assertIs(result.outcome, Verdict.PASS)

    def test_PC14_none_advances_without_a_repo(self) -> None:
        result = evaluate(Postcondition.NONE, self.base / "absent", FLOOR, Path("/absent/v.md"))
        self.assertIs(result.outcome, Ungated.NONE)
        self.assertTrue(result.advance)

    def test_non_postcondition_is_a_type_error(self) -> None:
        with self.assertRaises(TypeError):
            evaluate("none", self.fresh_repo(), FLOOR, VERDICT_FILE)  # type: ignore[arg-type]


class GateResultTest(unittest.TestCase):
    OUTCOME_TYPE = {
        Postcondition.REVIEW_VERDICT: Verdict,
        Postcondition.RESEARCH_NOTE_FRESH: Freshness,
        Postcondition.INCREMENT_BUILT: Freshness,
        Postcondition.NONE: Ungated,
    }

    def test_advance_is_derived_for_every_valid_pair(self) -> None:
        for post, kind in self.OUTCOME_TYPE.items():
            for outcome in kind:
                with self.subTest(post=post, outcome=outcome):
                    self.assertEqual(GateResult(post, outcome, "x").advance, outcome in ADVANCING)

    def test_outcome_must_match_the_postcondition(self) -> None:
        for post, kind in self.OUTCOME_TYPE.items():
            for other in (Verdict, Freshness, Ungated):
                if other is kind:
                    continue
                for outcome in other:
                    with self.subTest(post=post, outcome=outcome):
                        with self.assertRaises(TypeError):
                            GateResult(post, outcome, "x")

    def test_outcome_name_string_is_not_an_outcome(self) -> None:
        with self.assertRaises(TypeError):
            GateResult(Postcondition.REVIEW_VERDICT, "PASS", "x")  # type: ignore[arg-type]

    def test_detail_must_be_one_non_empty_line(self) -> None:
        for detail in ("", "two\nlines", "carriage\rreturn"):
            with self.subTest(detail=detail):
                with self.assertRaises(ValueError):
                    GateResult(Postcondition.NONE, Ungated.NONE, detail)

    def test_is_immutable(self) -> None:
        result = GateResult(Postcondition.REVIEW_VERDICT, Verdict.FAIL, "x")
        with self.assertRaises(AttributeError):
            result.outcome = Verdict.PASS  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
