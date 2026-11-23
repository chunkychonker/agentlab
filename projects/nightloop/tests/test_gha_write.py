"""Milestone 10, criterion 6 (I9): writing the workflow is idempotent and atomic."""

from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from nightloop.schedule_gha import Unchanged, Written, install_workflow

TEXT = "name: nightloop\non:\n  workflow_dispatch:\n"


class InstallWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.path = self.root / ".github" / "workflows" / "nightloop.yml"

    def test_creates_missing_directories_and_a_0644_file(self) -> None:
        self.assertEqual(install_workflow(self.path, TEXT), Written(self.path))
        self.assertEqual(self.path.read_bytes(), TEXT.encode("utf-8"))
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o644)

    def test_a_second_write_changes_neither_bytes_nor_mtime(self) -> None:
        install_workflow(self.path, TEXT)
        past = 1_600_000_000
        os.utime(self.path, (past, past))
        before = self.path.stat().st_mtime_ns
        self.assertEqual(install_workflow(self.path, TEXT), Unchanged(self.path))
        self.assertEqual(self.path.stat().st_mtime_ns, before)
        self.assertEqual(self.path.read_bytes(), TEXT.encode("utf-8"))

    def test_a_different_file_is_replaced_keeping_its_mode(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text("old\n", encoding="utf-8")
        os.chmod(self.path, 0o600)
        self.assertEqual(install_workflow(self.path, TEXT), Written(self.path))
        self.assertEqual(self.path.read_text(encoding="utf-8"), TEXT)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(sorted(p.name for p in self.path.parent.iterdir()), ["nightloop.yml"])

    def test_a_failed_replace_leaves_the_original_and_no_temp_file(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text("old\n", encoding="utf-8")
        with mock.patch("nightloop.schedule_gha.write.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                install_workflow(self.path, TEXT)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "old\n")
        self.assertEqual(sorted(p.name for p in self.path.parent.iterdir()), ["nightloop.yml"])

    def test_an_unreadable_existing_path_raises(self) -> None:
        self.path.mkdir(parents=True)  # a directory where the file should be
        with self.assertRaises(OSError):
            install_workflow(self.path, TEXT)


if __name__ == "__main__":
    unittest.main()
