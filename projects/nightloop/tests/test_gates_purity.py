"""Acceptance criterion 5 for milestone 2: the pure gate modules import no I/O.

Parses the source with ``ast`` rather than importing it, so the check sees
every import statement, including ones inside functions.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

GATES = Path(__file__).resolve().parent.parent / "nightloop" / "gates"
PURE_MODULES = ("verdict.py", "freshness.py", "outcome.py")
FORBIDDEN_STDLIB = {"os", "pathlib", "time", "subprocess", "shutil", "io", "socket", "datetime"}
# Sibling modules that do I/O; a pure module importing them is impure too.
FORBIDDEN_SIBLINGS = {"fs", "dispatch"}


def imports_of(source: str) -> tuple[set[str], set[str]]:
    """Return (absolute top-level modules, relative sibling modules) imported by ``source``."""
    absolute: set[str] = set()
    relative: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            absolute.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                absolute.add((node.module or "").split(".")[0])
            elif node.level == 1 and node.module is None:
                relative.update(alias.name for alias in node.names)
            elif node.level == 1:
                relative.add(node.module.split(".")[0])
    return absolute, relative


class PurityTest(unittest.TestCase):
    def test_pure_modules_import_no_io(self) -> None:
        for name in PURE_MODULES:
            with self.subTest(module=name):
                absolute, relative = imports_of((GATES / name).read_text(encoding="utf-8"))
                self.assertFalse(absolute & FORBIDDEN_STDLIB, f"{name} imports {absolute & FORBIDDEN_STDLIB}")
                self.assertFalse(relative & FORBIDDEN_SIBLINGS, f"{name} imports {relative & FORBIDDEN_SIBLINGS}")

    def test_the_checker_sees_an_io_import(self) -> None:
        # Guards against a checker that passes vacuously.
        absolute, relative = imports_of("def f():\n    import os.path\nfrom . import fs\n")
        self.assertIn("os", absolute)
        self.assertIn("fs", relative)


if __name__ == "__main__":
    unittest.main()
