"""Acceptance tests for milestone 2: the artifact-freshness gate.

``PC<n>`` names are the case ids in agentlab's ``.pipeline/test_gates.sh``;
each runs against a real temp directory, as ``research_note_fresh`` /
``increment_built`` do, via ``evaluate``. Every mtime is set explicitly with
``os.utime`` so no test depends on the clock. Numbered comments refer to the
acceptance criteria in research/2026-11-09-nightloop-gates-library.md.
"""

from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path

from nightloop.gates import (
    Freshness,
    check_freshness,
    check_increment,
    classify_freshness,
    evaluate,
)
from nightloop.model import Postcondition

OLD = 1767225600  # 2026-01-01T00:00:00Z, the bash suite's `touch -t 202601010000`
FLOOR = 1780000000  # the phase's start; comfortably after OLD
NEW = FLOOR + 60
UNUSED_VERDICT = Path("logs/last-review.md")


def touch(path: Path, mtime: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    os.utime(path, (mtime, mtime))
    return path


class SandboxTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name)

    def gate(self, post: Postcondition, floor: int = FLOOR) -> Freshness:
        result = evaluate(post, self.repo, floor, UNUSED_VERDICT)
        assert isinstance(result.outcome, Freshness)
        return result.outcome

    def research(self, floor: int = FLOOR) -> Freshness:
        return self.gate(Postcondition.RESEARCH_NOTE_FRESH, floor)

    def increment(self) -> Freshness:
        return self.gate(Postcondition.INCREMENT_BUILT)


class ResearchNoteTest(SandboxTest):
    # 2. PC1-PC7, same expected word as the bash suite (PC5/PC6: see FloorTest).
    def test_PC1_absent_research_dir_is_missing(self) -> None:
        self.assertIs(self.research(), Freshness.MISSING)

    def test_PC2_empty_research_dir_is_missing(self) -> None:
        (self.repo / "research").mkdir()
        self.assertIs(self.research(), Freshness.MISSING)

    def test_PC3_only_previous_nights_notes_is_stale(self) -> None:
        touch(self.repo / "research" / "2026-08-31-yesterday.md", OLD)
        self.assertIs(self.research(), Freshness.STALE)

    def test_PC4_note_written_after_phase_start_is_fresh(self) -> None:
        touch(self.repo / "research" / "2026-08-31-yesterday.md", OLD)
        touch(self.repo / "research" / "2026-09-02-tonight.md", NEW)
        self.assertIs(self.research(), Freshness.FRESH)

    def test_PC7_fresh_file_of_another_type_does_not_count(self) -> None:
        touch(self.repo / "research" / "note.md", OLD)
        touch(self.repo / "research" / "scratch.txt", NEW)
        self.assertIs(self.research(), Freshness.STALE)

    def test_glob_is_case_sensitive_like_find_name(self) -> None:
        touch(self.repo / "research" / "NOTE.MD", NEW)
        self.assertIs(self.research(), Freshness.MISSING)

    def test_research_dir_that_is_a_file_is_missing(self) -> None:
        touch(self.repo / "research", NEW)
        self.assertIs(self.research(), Freshness.MISSING)

    def test_nested_notes_are_found(self) -> None:
        touch(self.repo / "research" / "2026" / "11" / "note.md", NEW)
        self.assertIs(self.research(), Freshness.FRESH)


class IncrementTest(SandboxTest):
    # 2. PC8-PC13.
    def test_PC8_neither_root_existing_is_missing(self) -> None:
        self.assertIs(self.increment(), Freshness.MISSING)

    def test_PC9_only_pre_floor_work_is_stale(self) -> None:
        touch(self.repo / "examples" / "old-demo" / "main.py", OLD)
        self.assertIs(self.increment(), Freshness.STALE)

    def test_PC10_fresh_in_projects_is_enough(self) -> None:
        touch(self.repo / "examples" / "old-demo" / "main.py", OLD)
        touch(self.repo / "projects" / "live" / "step-3.md", NEW)
        self.assertIs(self.increment(), Freshness.FRESH)

    def test_PC10_fresh_in_examples_is_enough(self) -> None:
        touch(self.repo / "examples" / "new-demo" / "main.py", NEW)
        touch(self.repo / "projects" / "live" / "step-3.md", OLD)
        self.assertIs(self.increment(), Freshness.FRESH)

    def test_PC11_PC12_PC13_prune_skips_noise_not_work(self) -> None:
        demo = self.repo / "examples" / "old-demo"
        touch(demo / "main.py", OLD)
        touch(demo / "__pycache__" / "main.cpython-313.pyc", NEW)
        self.assertIs(self.increment(), Freshness.STALE, "PC11: fresh __pycache__ forged an increment")
        touch(demo / ".venv" / "lib" / "pyvenv.cfg", NEW)
        self.assertIs(self.increment(), Freshness.STALE, "PC12: fresh .venv forged an increment")
        touch(self.repo / "examples" / "new-demo" / "main.py", NEW)
        self.assertIs(self.increment(), Freshness.FRESH, "PC13: the prune swallowed real work")

    def test_node_modules_and_git_are_pruned(self) -> None:
        touch(self.repo / "examples" / "old-demo" / "main.py", OLD)
        touch(self.repo / "examples" / "old-demo" / "node_modules" / "x" / "index.js", NEW)
        touch(self.repo / "projects" / "p" / ".git" / "index", NEW)
        self.assertIs(self.increment(), Freshness.STALE)

    def test_a_pruned_name_is_skipped_as_a_file_too(self) -> None:
        # find's `-name .git -prune` also drops a submodule's `.git` *file*.
        touch(self.repo / "examples" / "old-demo" / "main.py", OLD)
        touch(self.repo / "examples" / "sub" / ".git", NEW)
        self.assertIs(self.increment(), Freshness.STALE)


class FloorBoundaryTest(SandboxTest):
    # 2. Inclusive comparison on the float mtime, never truncated.
    def test_mtime_exactly_at_floor_is_fresh(self) -> None:
        touch(self.repo / "research" / "note.md", FLOOR)
        self.assertIs(self.research(), Freshness.FRESH)

    def test_half_a_second_before_floor_is_stale(self) -> None:
        touch(self.repo / "research" / "note.md", FLOOR - 0.5)
        self.assertIs(self.research(), Freshness.STALE)

    def test_half_a_second_after_floor_is_fresh(self) -> None:
        touch(self.repo / "research" / "note.md", FLOOR + 0.5)
        self.assertIs(self.research(), Freshness.FRESH)

    def test_floor_zero_is_valid(self) -> None:
        touch(self.repo / "research" / "note.md", OLD)
        self.assertIs(self.research(floor=0), Freshness.FRESH)


class FloorTest(SandboxTest):
    """PC5/PC6: a bad floor raises ValueError (decisions log, 2026-11-09)."""

    BAD_FLOORS: tuple[object, ...] = ("not-a-number", -1, True, False, 1.5, float(FLOOR), None)

    def test_PC5_PC6_bad_floor_raises_from_every_entry_point(self) -> None:
        touch(self.repo / "research" / "note.md", NEW)
        for floor in self.BAD_FLOORS:
            with self.subTest(floor=floor):
                with self.assertRaises(ValueError):
                    classify_freshness([float(NEW)], floor)  # type: ignore[arg-type]
                with self.assertRaises(ValueError):
                    check_freshness(self.repo / "research", "*.md", floor)  # type: ignore[arg-type]
                with self.assertRaises(ValueError):
                    check_increment([self.repo / "research"], "*", floor)  # type: ignore[arg-type]
                for post in Postcondition:
                    with self.assertRaises(ValueError):
                        evaluate(post, self.repo, floor, UNUSED_VERDICT)  # type: ignore[arg-type]

    def test_bad_floor_raises_even_when_the_root_is_missing(self) -> None:
        with self.assertRaises(ValueError):
            check_freshness(self.repo / "absent", "*.md", -1)


class SymlinkTest(SandboxTest):
    """Regular files only, never following a symlink: `find -type f` under -P."""

    def test_symlink_loop_does_not_hang(self) -> None:
        (self.repo / "examples" / "a").mkdir(parents=True)
        (self.repo / "examples" / "a" / "loop").symlink_to(self.repo / "examples")
        touch(self.repo / "examples" / "a" / "old.py", OLD)
        result: list[Freshness] = []
        worker = threading.Thread(target=lambda: result.append(self.increment()), daemon=True)
        worker.start()
        worker.join(timeout=5)
        self.assertFalse(worker.is_alive(), "walk followed a symlink loop")
        self.assertEqual(result, [Freshness.STALE])

    def test_symlinked_directory_is_not_descended(self) -> None:
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        outside = Path(other.name)
        touch(outside / "note.md", NEW)
        (self.repo / "research").mkdir()
        (self.repo / "research" / "elsewhere").symlink_to(outside)
        self.assertIs(self.research(), Freshness.MISSING)

    def test_symlinked_file_does_not_count(self) -> None:
        target = touch(self.repo / "outside.md", NEW)
        (self.repo / "research").mkdir()
        (self.repo / "research" / "link.md").symlink_to(target)
        self.assertIs(self.research(), Freshness.MISSING)


class ClassifyFreshnessTest(unittest.TestCase):
    """The pure core, without files."""

    def test_empty_is_missing(self) -> None:
        self.assertIs(classify_freshness([], FLOOR), Freshness.MISSING)

    def test_unreadable_mtime_alone_is_stale_not_fresh(self) -> None:
        self.assertIs(classify_freshness([None], FLOOR), Freshness.STALE)
        self.assertIs(classify_freshness([None], 0), Freshness.STALE)

    def test_any_mtime_at_or_after_floor_is_fresh(self) -> None:
        self.assertIs(classify_freshness([OLD, None, FLOOR], FLOOR), Freshness.FRESH)

    def test_all_before_floor_is_stale(self) -> None:
        self.assertIs(classify_freshness([OLD, FLOOR - 0.001], FLOOR), Freshness.STALE)


class CheckIncrementTest(SandboxTest):
    def test_no_roots_is_missing(self) -> None:
        self.assertIs(check_increment([], "*", FLOOR), Freshness.MISSING)

    def test_stale_in_one_root_and_missing_in_the_other_is_stale(self) -> None:
        touch(self.repo / "b" / "x", OLD)
        self.assertIs(check_increment([self.repo / "a", self.repo / "b"], "*", FLOOR), Freshness.STALE)


if __name__ == "__main__":
    unittest.main()
