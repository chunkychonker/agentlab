"""Milestone 5, R6: the shipper's pure modules import no I/O; only ``gh.py`` and ``gh_shipper.py`` may.

Same ``ast`` checker as the gates and cycle purity tests. The pure modules
may use the cycle package's pure value modules, never its package
``__init__`` (which re-exports ``GitWorkspace``) or anything that runs a
process.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from .test_cycle_purity import parent_imports
from .test_gates_purity import imports_of

NIGHTLOOP = Path(__file__).resolve().parent.parent / "nightloop"
SHIP = NIGHTLOOP / "ship"
PURE_MODULES = ("mergeable.py", "naming.py", "pr_list.py", "pr_text.py", "pr_url.py", "review_section.py")
IO_MODULES = ("gh.py", "gh_shipper.py")
FORBIDDEN_STDLIB = {
    "os", "pathlib", "subprocess", "signal", "time", "shutil", "io", "socket", "urllib", "http", "datetime",
}
FORBIDDEN_SIBLINGS = {"gh", "gh_shipper"}
FORBIDDEN_PARENT = {
    ("cycle", None), ("cycle", "git_workspace"),
    ("phase", None), ("phase", "subprocess_runner"),
    ("gates", None), ("gates", "fs"), ("gates", "dispatch"),
    ("git_runner", None),
}


class ShipPurityTest(unittest.TestCase):
    def test_the_module_list_is_complete(self) -> None:
        on_disk = {p.name for p in SHIP.glob("*.py")} - {"__init__.py"}
        self.assertEqual(on_disk, set(PURE_MODULES) | set(IO_MODULES))

    def test_pure_modules_import_no_io(self) -> None:
        for name in PURE_MODULES:
            with self.subTest(module=name):
                source = (SHIP / name).read_text(encoding="utf-8")
                absolute, relative = imports_of(source)
                self.assertFalse(absolute & FORBIDDEN_STDLIB, f"{name} imports {absolute & FORBIDDEN_STDLIB}")
                self.assertFalse(relative & FORBIDDEN_SIBLINGS, f"{name} imports {relative & FORBIDDEN_SIBLINGS}")
                impure = parent_imports(source) & FORBIDDEN_PARENT
                self.assertFalse(impure, f"{name} imports {impure}")

    def test_only_gh_py_runs_gh(self) -> None:
        absolute, _ = imports_of((SHIP / "gh.py").read_text(encoding="utf-8"))
        self.assertIn("subprocess", absolute)
        shipper = (SHIP / "gh_shipper.py").read_text(encoding="utf-8")
        self.assertNotIn("subprocess.run", shipper, "the shipper runs git and gh only through their edges")

    def test_the_shared_tail_helper_is_pure(self) -> None:
        absolute, _ = imports_of((NIGHTLOOP / "process_tail.py").read_text(encoding="utf-8"))
        self.assertFalse(absolute & FORBIDDEN_STDLIB)


if __name__ == "__main__":
    unittest.main()
