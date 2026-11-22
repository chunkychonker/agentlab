"""Milestone 9, criterion 8: the scheduler's deciding modules import only an allowlist.

Stricter than the denylist the other purity tests use: every absolute
import in a pure module must be on ``ALLOWED_STDLIB``, and ``pathlib`` may
only give ``PurePosixPath``. Same ``ast`` walk, so imports inside functions
count too.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from .test_gates_purity import imports_of

SCHEDULE = Path(__file__).resolve().parent.parent / "nightloop" / "schedule"
PURE_MODULES = ("advice.py", "errors.py", "job.py", "launchd_print.py", "plan.py", "plist.py", "ports.py")
IO_MODULES = ("apply.py", "launchctl.py")
ALLOWED_STDLIB = {"__future__", "dataclasses", "enum", "hashlib", "pathlib", "plistlib", "re", "typing"}
ALLOWED_PATHLIB_NAMES = {"PurePosixPath"}
ALLOWED_PARENT = {"model"}  # ..model is the pure config value types


def pathlib_names(source: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module == "pathlib":
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import) and any(alias.name == "pathlib" for alias in node.names):
            names.add("<module>")
    return names


def parent_modules(source: str) -> set[str]:
    return {
        (node.module or "").split(".")[0]
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.level == 2
    }


class SchedulePurityTest(unittest.TestCase):
    def test_the_module_list_is_complete(self) -> None:
        on_disk = {p.name for p in SCHEDULE.glob("*.py")} - {"__init__.py"}
        self.assertEqual(on_disk, set(PURE_MODULES) | set(IO_MODULES))

    def test_pure_modules_import_only_the_allowlist(self) -> None:
        for name in PURE_MODULES:
            with self.subTest(module=name):
                source = (SCHEDULE / name).read_text(encoding="utf-8")
                absolute, relative = imports_of(source)
                self.assertLessEqual(absolute, ALLOWED_STDLIB, f"{name} imports {absolute - ALLOWED_STDLIB}")
                self.assertFalse(relative & {"apply", "launchctl"}, f"{name} imports an edge module")
                self.assertLessEqual(pathlib_names(source), ALLOWED_PATHLIB_NAMES)
                self.assertLessEqual(parent_modules(source), ALLOWED_PARENT)

    def test_only_launchctl_starts_a_process_and_only_apply_writes(self) -> None:
        for name in PURE_MODULES + IO_MODULES:
            absolute, _ = imports_of((SCHEDULE / name).read_text(encoding="utf-8"))
            with self.subTest(module=name):
                self.assertEqual("subprocess" in absolute, name == "launchctl.py")
                self.assertEqual({"os", "shutil", "tempfile"} <= absolute, name == "apply.py")

    def test_the_checker_sees_a_forbidden_import(self) -> None:
        source = "def f():\n    import os\nfrom pathlib import Path\nfrom ..cycle import x\n"
        absolute, _ = imports_of(source)
        self.assertIn("os", absolute)
        self.assertEqual(pathlib_names(source), {"Path"})
        self.assertEqual(parent_modules(source), {"cycle"})


if __name__ == "__main__":
    unittest.main()
