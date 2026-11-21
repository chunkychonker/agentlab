"""Milestone 8, criterion 4: ``bin/run_silent``, run as an agent runs it (a bare executable).

The child commands are this interpreter running short ``-c`` scripts, so no
test depends on what else is installed.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from .plugin_support import RUN_SILENT, SUBPROCESS_TIMEOUT_S, SYSTEM_PYTHON, system_python_differs

PY = sys.executable
LINES = 5000
INTERLEAVED = (
    "import sys\n"
    "print('out-1', flush=True)\n"
    "print('err-1', file=sys.stderr, flush=True)\n"
    "print('out-2', flush=True)\n"
    "sys.exit(3)\n"
)


def run_silent(*args: str, launcher: tuple[str, ...] = ()) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*launcher, str(RUN_SILENT), *args],
        capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT_S,
    )


class SuccessTest(unittest.TestCase):
    def test_success_is_exactly_one_line(self) -> None:
        done = run_silent("unit", "--", PY, "-c", "import sys; print('noise'); print('more', file=sys.stderr)")
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "ok: unit\n", ""))

    def test_it_is_executable_with_a_shebang(self) -> None:
        self.assertTrue(os.access(RUN_SILENT, os.X_OK))
        self.assertTrue(RUN_SILENT.read_text(encoding="utf-8").startswith("#!/usr/bin/env python3\n"))


class FailureTest(unittest.TestCase):
    def test_failure_prints_the_header_and_the_combined_output_in_order(self) -> None:
        done = run_silent("unit", "--", PY, "-c", INTERLEAVED)
        self.assertEqual(done.returncode, 3)
        self.assertEqual(done.stdout, "FAIL: unit (exit 3)\nout-1\nerr-1\nout-2\n")
        self.assertEqual(done.stderr, "")

    def test_a_long_failure_is_not_truncated(self) -> None:
        script = f"import sys\nfor i in range({LINES}): print('line', i, file=sys.stderr)\nsys.exit(1)\n"
        done = run_silent("big", "--", PY, "-c", script)
        self.assertEqual(done.returncode, 1)
        lines = done.stdout.split("\n")
        self.assertEqual(lines[0], "FAIL: big (exit 1)")
        self.assertEqual(lines[1:-1], [f"line {i}" for i in range(LINES)])

    def test_the_exit_code_is_the_childs(self) -> None:
        for code in (1, 2, 42, 255):
            with self.subTest(code=code):
                done = run_silent("c", "--", PY, "-c", f"raise SystemExit({code})")
                self.assertEqual(done.returncode, code)
                self.assertTrue(done.stdout.startswith(f"FAIL: c (exit {code})\n"))

    def test_a_child_killed_by_a_signal(self) -> None:
        done = run_silent("sig", "--", PY, "-c", "import os, signal; os.kill(os.getpid(), signal.SIGKILL)")
        self.assertEqual(done.returncode, 128 + 9)
        self.assertEqual(done.stdout, "FAIL: sig (signal 9)\n")

    def test_the_child_gets_no_stdin(self) -> None:
        # Our stdin stays open; if run_silent passed it on, the child would block forever.
        with subprocess.Popen(
            [str(RUN_SILENT), "stdin", "--", PY, "-c", "import sys; sys.stdin.read()"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ) as proc:
            try:
                code = proc.wait(timeout=SUBPROCESS_TIMEOUT_S)
            finally:
                proc.kill()
            self.assertEqual(code, 0)
            self.assertEqual(proc.stdout.read() if proc.stdout else b"", b"ok: stdin\n")


class CannotRunTest(unittest.TestCase):
    def test_missing_binary_is_127(self) -> None:
        done = run_silent("gone", "--", "nightloop-no-such-binary-xyz")
        self.assertEqual(done.returncode, 127)
        self.assertEqual(done.stdout, "")
        self.assertIn("FAIL: gone (cannot run nightloop-no-such-binary-xyz", done.stderr)

    def test_non_executable_file_is_127(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "not_exec.sh"
            script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            script.chmod(0o644)
            done = run_silent("perm", "--", str(script))
        self.assertEqual(done.returncode, 127)
        self.assertIn("FAIL: perm (cannot run", done.stderr)


class UsageTest(unittest.TestCase):
    def test_bad_usage_is_2_with_the_usage_on_stderr(self) -> None:
        cases = {
            "no --": ("tests", PY, "-c", "pass"),
            "no label": ("--", PY, "-c", "pass"),
            "nothing after --": ("tests", "--"),
            "no arguments": (),
            "empty label": ("", "--", PY, "-c", "pass"),
            "label with a newline": ("a\nb", "--", PY, "-c", "pass"),
        }
        for label, args in cases.items():
            with self.subTest(case=label):
                done = run_silent(*args)
                self.assertEqual(done.returncode, 2)
                self.assertEqual(done.stdout, "")
                self.assertIn("usage: run_silent <label> -- <cmd>", done.stderr)

    def test_usage_errors_run_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "ran"
            run_silent("tests", PY, "-c", f"open({str(marker)!r}, 'w').close()")
            self.assertFalse(marker.exists())


@unittest.skipUnless(system_python_differs(), f"{SYSTEM_PYTHON} absent or the same interpreter")
class SystemPythonTest(unittest.TestCase):
    """The shebang finds `python3` on PATH, which may be macOS's 3.9."""

    def test_success_and_failure_under_the_system_python(self) -> None:
        launcher = (str(SYSTEM_PYTHON), "-I", "-B")
        ok = run_silent("unit", "--", PY, "-c", "print('x')", launcher=launcher)
        self.assertEqual((ok.returncode, ok.stdout), (0, "ok: unit\n"))
        failed = run_silent("unit", "--", PY, "-c", INTERLEAVED, launcher=launcher)
        self.assertEqual((failed.returncode, failed.stdout), (3, "FAIL: unit (exit 3)\nout-1\nerr-1\nout-2\n"))


if __name__ == "__main__":
    unittest.main()
