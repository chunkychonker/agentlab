"""Milestone 3, A11: the phase core imports no I/O; only one module may spawn processes.

Parses the source with ``ast`` (reusing the gates purity checker), so imports
inside functions count too.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from .test_gates_purity import imports_of

PHASE = Path(__file__).resolve().parent.parent / "nightloop" / "phase"
PURE_MODULES = (
    "argv.py", "execute.py", "fake_runner.py", "metrics.py",
    "request.py", "result.py", "runner.py", "status.py",
)
# The only module allowed to import subprocess/os/signal.
PROCESS_MODULE = "subprocess_runner.py"
FORBIDDEN_STDLIB = {"os", "subprocess", "signal", "time", "shutil", "io", "socket", "datetime"}
# I/O-doing siblings: the process runner here, and the gates' filesystem layer.
FORBIDDEN_SIBLINGS = {"subprocess_runner", "fs", "dispatch"}


def gates_imports(source: str) -> set[str]:
    """Modules imported from ``..gates`` (``from ..gates.fs import x`` -> ``fs``)."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.level == 2 and node.module:
            parts = node.module.split(".")
            if parts[0] == "gates":
                found.add(parts[1] if len(parts) > 1 else "gates")
    return found


class PhasePurityTest(unittest.TestCase):
    def test_the_module_list_is_complete(self) -> None:
        on_disk = {p.name for p in PHASE.glob("*.py")} - {"__init__.py"}
        self.assertEqual(on_disk, set(PURE_MODULES) | {PROCESS_MODULE})

    def test_pure_modules_import_no_io(self) -> None:
        for name in PURE_MODULES:
            with self.subTest(module=name):
                source = (PHASE / name).read_text(encoding="utf-8")
                absolute, relative = imports_of(source)
                self.assertFalse(absolute & FORBIDDEN_STDLIB, f"{name} imports {absolute & FORBIDDEN_STDLIB}")
                self.assertFalse(relative & FORBIDDEN_SIBLINGS, f"{name} imports {relative & FORBIDDEN_SIBLINGS}")
                # 'gates' itself re-exports fs and dispatch, so the package import is impure too.
                impure_gates = gates_imports(source) & (FORBIDDEN_SIBLINGS | {"gates"})
                self.assertFalse(impure_gates, f"{name} imports gates' {impure_gates}")

    def test_the_process_module_is_the_one_that_spawns(self) -> None:
        absolute, _ = imports_of((PHASE / PROCESS_MODULE).read_text(encoding="utf-8"))
        self.assertIn("subprocess", absolute)

    def test_the_gates_checker_sees_an_impure_gates_import(self) -> None:
        self.assertEqual(gates_imports("from ..gates.fs import check_verdict\nfrom ..gates import evaluate\n"),
                         {"fs", "gates"})


if __name__ == "__main__":
    unittest.main()
