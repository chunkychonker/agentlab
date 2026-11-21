"""Milestone 8, criterion 7: the checker's deciding modules import no I/O; only ``tree.py`` reads.

Same ``ast`` checker as the other purity tests. ``pathlib`` is allowed in the
pure modules (``rules.py`` uses ``PurePosixPath``, which does no I/O);
``publish_guard.py`` and ``run_silent`` are edge scripts and are exempt.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from .test_cycle_purity import parent_imports
from .test_gates_purity import imports_of

PACKAGE = Path(__file__).resolve().parent.parent / "nightloop" / "plugin_check"
PURE_MODULES = ("check.py", "frontmatter.py", "layout.py", "problem.py", "rules.py", "snapshot.py")
IO_MODULE = "tree.py"
# check.py composes the reader and the rules; it is the one pure-listed
# module allowed to import the reader.
COMPOSER = "check.py"
ALLOWED_STDLIB = {"__future__", "dataclasses", "enum", "json", "re", "typing", "types", "pathlib"}
FORBIDDEN_PARENT_PACKAGES = {"phase", "cycle", "gates", "ship", "claims", "cli", "git_runner", "config"}


class PluginCheckPurityTest(unittest.TestCase):
    def test_the_module_list_is_complete(self) -> None:
        on_disk = {p.name for p in PACKAGE.glob("*.py")} - {"__init__.py"}
        self.assertEqual(on_disk, set(PURE_MODULES) | {IO_MODULE})

    def test_pure_modules_import_only_allowed_stdlib(self) -> None:
        for name in PURE_MODULES:
            with self.subTest(module=name):
                source = (PACKAGE / name).read_text(encoding="utf-8")
                absolute, relative = imports_of(source)
                self.assertLessEqual(absolute, ALLOWED_STDLIB, absolute - ALLOWED_STDLIB)
                if name != COMPOSER:
                    self.assertNotIn("tree", relative)
                self.assertFalse({pkg for pkg, _ in parent_imports(source)} & FORBIDDEN_PARENT_PACKAGES)

    def test_pure_modules_never_touch_the_filesystem_through_pathlib(self) -> None:
        # pathlib is allowed for PurePosixPath only; a concrete Path would read the disk.
        for name in PURE_MODULES:
            with self.subTest(module=name):
                source = (PACKAGE / name).read_text(encoding="utf-8")
                if name == COMPOSER:
                    continue
                self.assertNotRegex(source, r"from pathlib import .*\bPath\b")

    def test_the_checker_sees_an_io_import(self) -> None:
        absolute, relative = imports_of("def f():\n    import os\nfrom . import tree\n")
        self.assertIn("os", absolute)
        self.assertIn("tree", relative)


if __name__ == "__main__":
    unittest.main()
