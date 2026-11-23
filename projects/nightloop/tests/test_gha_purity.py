"""Milestone 10, criterion 7: the generator's deciding modules import only an allowlist; only ``write.py`` writes.

Same ``ast`` walk as the other purity tests, so imports inside functions
count too. Also covers the shared ``nightloop/schedule_slot.py``.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from .test_gates_purity import imports_of
from .test_schedule_purity import parent_modules

PACKAGE = Path(__file__).resolve().parent.parent / "nightloop"
GHA = PACKAGE / "schedule_gha"
PURE_MODULES = ("budget.py", "errors.py", "render.py", "spec.py")
IO_MODULES = ("write.py",)
ALLOWED_STDLIB = {"__future__", "dataclasses", "enum", "re", "typing"}
ALLOWED_PARENT = {"model", "schedule_slot"}
EDGE_SIBLINGS = {"write"}


class GhaPurityTest(unittest.TestCase):
    def test_the_module_list_is_complete(self) -> None:
        on_disk = {p.name for p in GHA.glob("*.py")} - {"__init__.py"}
        self.assertEqual(on_disk, set(PURE_MODULES) | set(IO_MODULES))

    def test_pure_modules_import_only_the_allowlist(self) -> None:
        for name in PURE_MODULES:
            with self.subTest(module=name):
                source = (GHA / name).read_text(encoding="utf-8")
                absolute, relative = imports_of(source)
                self.assertLessEqual(absolute, ALLOWED_STDLIB, f"{name} imports {absolute - ALLOWED_STDLIB}")
                self.assertFalse(relative & EDGE_SIBLINGS, f"{name} imports an edge module")
                self.assertLessEqual(parent_modules(source), ALLOWED_PARENT)

    def test_only_write_touches_the_filesystem(self) -> None:
        for name in PURE_MODULES + IO_MODULES:
            absolute, _ = imports_of((GHA / name).read_text(encoding="utf-8"))
            with self.subTest(module=name):
                self.assertEqual({"os", "tempfile"} <= absolute, name == "write.py")
                self.assertNotIn("subprocess", absolute)

    def test_the_shared_slot_module_is_pure(self) -> None:
        source = (PACKAGE / "schedule_slot.py").read_text(encoding="utf-8")
        absolute, relative = imports_of(source)
        self.assertEqual(absolute, {"__future__"})
        self.assertEqual(relative, {"model"})


if __name__ == "__main__":
    unittest.main()
