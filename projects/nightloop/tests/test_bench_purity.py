"""Milestone 11: the benchmark's deciding modules import no I/O; only ``load.py`` and ``workspace.py`` may.

Same ``ast`` checker as the other purity tests. The pure modules are held to
an allowlist of stdlib modules rather than a denylist, so a new I/O import
fails here even if no list names it.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from .test_cycle_purity import parent_imports
from .test_gates_purity import imports_of

BENCH = Path(__file__).resolve().parent.parent / "nightloop" / "bench"
PURE_MODULES = ("classify.py", "fixture.py", "fresh.py", "ports.py", "report.py", "run.py", "stats.py")
IO_MODULES = ("load.py", "workspace.py")
# __init__.py re-exports the whole package, edges included, like nightloop.gates.
ALLOWED_STDLIB = {
    "__future__", "dataclasses", "enum", "hashlib", "json", "math", "re", "tomllib", "types", "typing",
    "pathlib",  # Path and PurePosixPath as values only; the pure modules never call a filesystem method
    "contextlib",  # ports.py's ContextManager type
}
FORBIDDEN_SIBLINGS = {"load", "workspace"}
FORBIDDEN_PARENT = {
    ("cli", None), ("config", None),
    ("cycle", None), ("cycle", "git_workspace"),
    ("phase", None), ("phase", "subprocess_runner"),
    ("gates", None), ("gates", "fs"), ("gates", "dispatch"),
    ("git_runner", None), ("ship", None), ("claims", None), ("schedule", None), ("schedule_gha", None),
}
# Calls that touch the filesystem through a Path value; a pure module must not make them.
FS_METHODS = {"read_text", "read_bytes", "write_text", "write_bytes", "open", "mkdir", "iterdir",
              "exists", "is_file", "is_dir", "stat", "lstat", "unlink", "rmdir", "glob", "rglob", "resolve"}


class BenchPurityTest(unittest.TestCase):
    def test_the_module_list_is_complete(self) -> None:
        on_disk = {p.name for p in BENCH.glob("*.py")} - {"__init__.py"}
        self.assertEqual(on_disk, set(PURE_MODULES) | set(IO_MODULES))

    def test_pure_modules_import_only_allowed_stdlib(self) -> None:
        for name in PURE_MODULES:
            with self.subTest(module=name):
                source = (BENCH / name).read_text(encoding="utf-8")
                absolute, relative = imports_of(source)
                self.assertLessEqual(absolute, ALLOWED_STDLIB, f"{name} imports {absolute - ALLOWED_STDLIB}")
                self.assertFalse(relative & FORBIDDEN_SIBLINGS, f"{name} imports {relative & FORBIDDEN_SIBLINGS}")
                impure = parent_imports(source) & FORBIDDEN_PARENT
                self.assertFalse(impure, f"{name} imports {impure}")

    def test_pure_modules_call_no_filesystem_method(self) -> None:
        import ast

        for name in PURE_MODULES:
            tree = ast.parse((BENCH / name).read_text(encoding="utf-8"))
            calls = {n.func.attr for n in ast.walk(tree)
                     if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
            with self.subTest(module=name):
                self.assertFalse(calls & FS_METHODS, f"{name} calls {calls & FS_METHODS}")

    def test_the_edges_are_the_ones_doing_io(self) -> None:
        workspace, _ = imports_of((BENCH / "workspace.py").read_text(encoding="utf-8"))
        self.assertTrue({"subprocess", "tempfile", "shutil"} <= workspace)
        load, _ = imports_of((BENCH / "load.py").read_text(encoding="utf-8"))
        self.assertIn("os", load)
        self.assertNotIn("subprocess", load)


if __name__ == "__main__":
    unittest.main()
