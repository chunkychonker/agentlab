"""Milestones 6 and 7b: the claims core imports no I/O; only the three edge modules may.

The edges are ``backlog_file.py``, ``git_claim_source.py`` and (7b)
``git_publisher.py``; ``step.py`` and ``publish.py`` (7b) are pure.

Same ``ast`` checker as the gates, cycle and ship purity tests, so imports
inside functions count too.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from .test_cycle_purity import parent_imports
from .test_gates_purity import imports_of

CLAIMS = Path(__file__).resolve().parent.parent / "nightloop" / "claims"
PURE_MODULES = (
    "backlog_text.py", "claim_diff.py", "edit.py", "markers.py", "outcome.py", "ports.py",
    "publish.py", "reconcile.py", "step.py", "values.py",
)
IO_MODULES = ("backlog_file.py", "git_claim_source.py", "git_publisher.py")
FORBIDDEN_STDLIB = {
    "os", "pathlib", "subprocess", "signal", "time", "shutil", "io", "socket", "urllib", "http",
    "datetime", "tempfile", "stat",
}
FORBIDDEN_SIBLINGS = {"backlog_file", "git_claim_source", "git_publisher"}
FORBIDDEN_PARENT = {
    ("cycle", None), ("cycle", "git_workspace"),
    ("phase", None), ("phase", "subprocess_runner"),
    ("gates", None), ("gates", "fs"), ("gates", "dispatch"),
    ("git_runner", None), ("ship", None), ("ship", "gh"), ("ship", "gh_shipper"),
}


class ClaimsPurityTest(unittest.TestCase):
    def test_the_module_list_is_complete(self) -> None:
        on_disk = {p.name for p in CLAIMS.glob("*.py")} - {"__init__.py"}
        self.assertEqual(on_disk, set(PURE_MODULES) | set(IO_MODULES))

    def test_pure_modules_import_no_io(self) -> None:
        for name in PURE_MODULES:
            with self.subTest(module=name):
                source = (CLAIMS / name).read_text(encoding="utf-8")
                absolute, relative = imports_of(source)
                self.assertFalse(absolute & FORBIDDEN_STDLIB, f"{name} imports {absolute & FORBIDDEN_STDLIB}")
                self.assertFalse(relative & FORBIDDEN_SIBLINGS, f"{name} imports {relative & FORBIDDEN_SIBLINGS}")
                impure = parent_imports(source) & FORBIDDEN_PARENT
                self.assertFalse(impure, f"{name} imports {impure}")

    def test_only_the_source_runs_processes(self) -> None:
        absolute, _ = imports_of((CLAIMS / "git_claim_source.py").read_text(encoding="utf-8"))
        self.assertIn("subprocess", absolute)
        self.assertNotIn("subprocess.run", (CLAIMS / "git_claim_source.py").read_text(encoding="utf-8"),
                         "git and gh run only through GitRunner and Gh")
        absolute, _ = imports_of((CLAIMS / "backlog_file.py").read_text(encoding="utf-8"))
        self.assertNotIn("subprocess", absolute)

    def test_the_publisher_runs_git_only_through_git_runner(self) -> None:
        source = (CLAIMS / "git_publisher.py").read_text(encoding="utf-8")
        self.assertNotIn("subprocess.run", source)
        self.assertNotIn("subprocess.Popen", source)
        self.assertNotIn("import os", source)


if __name__ == "__main__":
    unittest.main()
