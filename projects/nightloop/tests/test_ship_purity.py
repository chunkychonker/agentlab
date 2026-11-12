"""Milestone 5, P1: the ``ship`` core imports no I/O, and no ``ship`` module can merge, force or push main.

Parses the source with ``ast``, reusing the gates and cycle purity checkers.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from .test_cycle_purity import parent_imports
from .test_gates_purity import imports_of

SHIP = Path(__file__).resolve().parent.parent / "nightloop" / "ship"
PURE_MODULES = ("mergeable.py", "ports.py", "pr_text.py", "review_record.py", "shipper.py")
IO_MODULES = ("gh_cli.py", "git_publisher.py", "verdict_file.py")
FORBIDDEN_STDLIB = {
    "os", "pathlib", "subprocess", "signal", "time", "shutil", "io", "socket", "urllib", "http", "datetime",
}
FORBIDDEN_SIBLINGS = {"gh_cli", "git_publisher", "verdict_file"}
FORBIDDEN_PARENT = {
    ("phase", None), ("phase", "subprocess_runner"),
    ("gates", None), ("gates", "fs"), ("gates", "dispatch"),
    ("cycle", None), ("cycle", "git_workspace"),
}
# Text that would mean a merge, a forced push, or a push to main.
FORBIDDEN_TEXT = ("pr merge", "--force", "push origin main")
# Argv words with the same meaning, as string literals anywhere in the source.
FORBIDDEN_ARGS = {"merge", "--force", "--force-with-lease", "-f", "--mirror", "--all", "--delete", "--admin"}


def string_constants(source: str) -> set[str]:
    return {node.value for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Constant) and isinstance(node.value, str)}


def violations(source: str) -> set[str]:
    """Every forbidden text or argv word in ``source``."""
    found = {text for text in FORBIDDEN_TEXT if text in source}
    return found | (string_constants(source) & FORBIDDEN_ARGS)


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

    def test_no_ship_module_can_merge_force_or_push_main(self) -> None:
        for path in sorted(SHIP.glob("*.py")):
            with self.subTest(module=path.name):
                self.assertEqual(violations(path.read_text(encoding="utf-8")), set())

    def test_the_checker_sees_a_violation(self) -> None:
        self.assertEqual(violations('run("pr", "merge", "7")'), {"merge"})
        self.assertEqual(violations('run("push", "--force", b)'), {"--force"})
        self.assertEqual(violations('# gh pr merge 7\n'), {"pr merge"})


if __name__ == "__main__":
    unittest.main()
