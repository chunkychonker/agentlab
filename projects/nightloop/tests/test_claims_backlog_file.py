"""Milestone 6: ``BacklogFile`` reads and replaces the file byte for byte, atomically, leaving no temp file."""

from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

from nightloop.claims import BacklogFile


class BacklogFileTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name).resolve()

    def listing(self) -> list[str]:
        return sorted(p.name for p in self.repo.iterdir())

    def test_round_trip_keeps_every_byte(self) -> None:
        raw = "# B\r\n- [ ] é `x`\n\n- [ ] no newline".encode("utf-8")
        (self.repo / "BACKLOG.md").write_bytes(raw)
        backlog = BacklogFile(self.repo)
        text = backlog.read()
        self.assertEqual(text.encode("utf-8"), raw)
        backlog.write(text + "\r\nmore")
        self.assertEqual((self.repo / "BACKLOG.md").read_bytes(), raw + b"\r\nmore")
        self.assertEqual(self.listing(), ["BACKLOG.md"], "no temp file is left")

    def test_write_keeps_the_permission_bits(self) -> None:
        path = self.repo / "BACKLOG.md"
        path.write_text("x\n", encoding="utf-8")
        path.chmod(0o640)
        BacklogFile(self.repo).write("y\n")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)

    def test_a_nested_relative_path(self) -> None:
        (self.repo / "docs").mkdir()
        (self.repo / "docs" / "TODO.md").write_text("t\n", encoding="utf-8")
        backlog = BacklogFile(self.repo, "docs/TODO.md")
        self.assertEqual((backlog.relative, backlog.read()), ("docs/TODO.md", "t\n"))

    def test_missing_file_raises(self) -> None:
        backlog = BacklogFile(self.repo)
        with self.assertRaises(FileNotFoundError):
            backlog.read()
        with self.assertRaises(FileNotFoundError):
            backlog.write("x\n")
        self.assertEqual(self.listing(), [])

    def test_a_failed_replace_leaves_the_target_and_no_temp_file(self) -> None:
        (self.repo / "BACKLOG.md").mkdir()  # os.replace of a file onto a directory fails
        with self.assertRaises(OSError):
            BacklogFile(self.repo).write("x\n")
        self.assertEqual(self.listing(), ["BACKLOG.md"])
        self.assertTrue((self.repo / "BACKLOG.md").is_dir())

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root ignores permission bits")
    def test_an_unwritable_directory_raises_and_leaves_the_file(self) -> None:
        path = self.repo / "BACKLOG.md"
        path.write_text("keep\n", encoding="utf-8")
        self.repo.chmod(0o555)
        self.addCleanup(self.repo.chmod, 0o755)
        with self.assertRaises(PermissionError):
            BacklogFile(self.repo).write("lost\n")
        self.assertEqual(path.read_text(encoding="utf-8"), "keep\n")
        self.assertEqual(self.listing(), ["BACKLOG.md"])

    def test_bad_paths_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            BacklogFile(Path("relative"))
        with self.assertRaises(TypeError):
            BacklogFile(str(self.repo))  # type: ignore[arg-type]
        for relative in ("", " ", "/etc/passwd", "../outside.md", "a/../../b", "a\\b"):
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                BacklogFile(self.repo, relative)


if __name__ == "__main__":
    unittest.main()
