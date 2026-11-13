"""Milestone 4, B12 and milestone 5, A10: the cycle core imports no I/O.

Only ``git_workspace.py`` and ``gh_shipper.py`` may: the record composer,
the output parsers and the mergeable decision are pure.

Parses the source with ``ast`` (reusing the gates purity checker), so
imports inside functions count too. Imports from sibling packages are
checked as well: the ``nightloop.phase`` and ``nightloop.gates`` packages
re-export their process and filesystem modules, so the core must import
their pure submodules directly.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from .test_gates_purity import imports_of

CYCLE = Path(__file__).resolve().parent.parent / "nightloop" / "cycle"
PURE_MODULES = (
    "cycle.py", "fake_shipper.py", "gh_parse.py", "mergeable.py", "metrics_tag.py", "naming.py",
    "night.py", "ports.py", "prompts.py", "recording.py", "report.py", "result.py",
    "review_record.py", "run_id.py", "usage_limit.py",
)
IO_MODULES = ("git_workspace.py", "gh_shipper.py")
FORBIDDEN_STDLIB = {
    "os", "subprocess", "signal", "time", "shutil", "io", "socket", "urllib", "http", "datetime",
}
FORBIDDEN_SIBLINGS = {"git_workspace", "gh_shipper"}
# (package, module) pairs under nightloop that do I/O, or re-export modules that do.
FORBIDDEN_PARENT = {
    ("phase", None), ("phase", "subprocess_runner"),
    ("gates", None), ("gates", "fs"), ("gates", "dispatch"),
    ("cycle", None),
}


def parent_imports(source: str) -> set[tuple[str, str | None]]:
    """``from ..pkg.mod import x`` -> ``(pkg, mod)``; ``from ..pkg import x`` -> ``(pkg, None)``."""
    found: set[tuple[str, str | None]] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.level == 2 and node.module:
            parts = node.module.split(".")
            found.add((parts[0], parts[1] if len(parts) > 1 else None))
    return found


class CyclePurityTest(unittest.TestCase):
    def test_the_module_list_is_complete(self) -> None:
        on_disk = {p.name for p in CYCLE.glob("*.py")} - {"__init__.py"}
        self.assertEqual(on_disk, set(PURE_MODULES) | set(IO_MODULES))

    def test_pure_modules_import_no_io(self) -> None:
        for name in PURE_MODULES:
            with self.subTest(module=name):
                source = (CYCLE / name).read_text(encoding="utf-8")
                absolute, relative = imports_of(source)
                self.assertFalse(absolute & FORBIDDEN_STDLIB, f"{name} imports {absolute & FORBIDDEN_STDLIB}")
                self.assertFalse(relative & FORBIDDEN_SIBLINGS, f"{name} imports {relative & FORBIDDEN_SIBLINGS}")
                impure = parent_imports(source) & FORBIDDEN_PARENT
                self.assertFalse(impure, f"{name} imports {impure}")

    def test_the_io_modules_are_the_ones_that_run_processes(self) -> None:
        for name in IO_MODULES:
            with self.subTest(module=name):
                absolute, _ = imports_of((CYCLE / name).read_text(encoding="utf-8"))
                self.assertIn("subprocess", absolute)

    def test_the_parent_checker_sees_an_impure_import(self) -> None:
        self.assertEqual(
            parent_imports("from ..phase import run_phase\nfrom ..gates.fs import check_verdict\n"),
            {("phase", None), ("gates", "fs")},
        )


if __name__ == "__main__":
    unittest.main()
