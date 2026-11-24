"""Milestones 7, 9, 10 and 11: the CLI's deciding modules import no I/O; only ``main.py``, ``metrics_file.py``, ``schedule_cmd.py``, ``gha_cmd.py`` and ``bench_cmd.py`` may.

Same ``ast`` checker as the other purity tests, so imports inside functions
count too.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from .test_cycle_purity import parent_imports
from .test_gates_purity import imports_of

CLI = Path(__file__).resolve().parent.parent / "nightloop" / "cli"
PURE_MODULES = (
    "bench_text.py", "claim_text.py", "exit_codes.py", "gha_text.py", "night_text.py", "plan.py", "schedule_text.py", "summary.py",
    "window.py",
)
# schedule_args.py, gha_args.py and bench_args.py do no I/O but are argparse, which the pure modules may not import.
IO_MODULES = ("bench_args.py", "bench_cmd.py", "gha_args.py", "gha_cmd.py", "main.py", "metrics_file.py", "schedule_args.py", "schedule_cmd.py")
FORBIDDEN_STDLIB = {
    "os", "subprocess", "signal", "time", "shutil", "io", "socket", "urllib", "http",
    "datetime", "tempfile", "stat", "sys", "argparse",
}
FORBIDDEN_SIBLINGS = {"bench_args", "bench_cmd", "gha_args", "gha_cmd", "main", "metrics_file", "schedule_args", "schedule_cmd"}
FORBIDDEN_PARENT = {
    ("config", None),
    ("cycle", None), ("cycle", "git_workspace"),
    ("phase", None), ("phase", "subprocess_runner"),
    ("gates", None), ("gates", "fs"), ("gates", "dispatch"),
    ("git_runner", None), ("ship", None), ("ship", "gh"), ("ship", "gh_shipper"),
    ("claims", None), ("claims", "backlog_file"), ("claims", "git_claim_source"),
    ("claims", "git_publisher"),
    ("schedule", None), ("schedule", "apply"), ("schedule", "launchctl"),
    ("schedule_gha", None), ("schedule_gha", "write"),
    ("bench", None), ("bench", "load"), ("bench", "workspace"),
}


class CliPurityTest(unittest.TestCase):
    def test_the_module_list_is_complete(self) -> None:
        on_disk = {p.name for p in CLI.glob("*.py")} - {"__init__.py"}
        self.assertEqual(on_disk, set(PURE_MODULES) | set(IO_MODULES))

    def test_pure_modules_import_no_io(self) -> None:
        for name in PURE_MODULES:
            with self.subTest(module=name):
                source = (CLI / name).read_text(encoding="utf-8")
                absolute, relative = imports_of(source)
                self.assertFalse(absolute & FORBIDDEN_STDLIB, f"{name} imports {absolute & FORBIDDEN_STDLIB}")
                self.assertFalse(relative & FORBIDDEN_SIBLINGS, f"{name} imports {relative & FORBIDDEN_SIBLINGS}")
                impure = parent_imports(source) & FORBIDDEN_PARENT
                self.assertFalse(impure, f"{name} imports {impure}")

    def test_main_is_the_module_that_reads_the_clock_and_argv(self) -> None:
        absolute, _ = imports_of((CLI / "main.py").read_text(encoding="utf-8"))
        self.assertTrue({"time", "sys", "argparse"} <= absolute)


if __name__ == "__main__":
    unittest.main()
