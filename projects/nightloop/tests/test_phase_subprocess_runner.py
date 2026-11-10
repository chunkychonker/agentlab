"""Milestone 3, A12: ``SubprocessRunner`` against stand-in executables.

Each stand-in is a tiny Python script written to a temp dir and run with this
interpreter; ``claude`` is never executed. POSIX only, like the runner.
"""

from __future__ import annotations

import json
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

from nightloop.phase import RawRun, SubprocessRunner
from nightloop.phase.argv import build_argv
from nightloop.phase.result import parse_cli_json

from .phase_support import request

# Each script gets the argv after the program, like the CLI would.
ECHO = """
import json, os, sys
sys.stdout.write(json.dumps({"result": json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd(),
                             "stdin": sys.stdin.read()}), "is_error": False}))
"""
FAIL = """
import sys
sys.stderr.write("Failed to authenticate\\n")
sys.exit(3)
"""
SLEEP = """
import time
time.sleep(60)
"""
IGNORE_TERM = """
import signal, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
sys.stdout.write("started"); sys.stdout.flush()
time.sleep(60)
"""
# Exits at once, but leaves a background child holding stdout open, the shape
# that makes subprocess.run(timeout=...) wait forever.
ORPHAN = """
import json, subprocess, sys
subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
sys.stdout.write(json.dumps({"result": "done early"})); sys.stdout.flush()
"""
BAD_UTF8 = """
import sys
sys.stdout.buffer.write(b'{"result": "caf\\xe9"}')
"""

# Generous bound for "returned promptly": far below the stand-ins' 60 s sleep.
PROMPT_S = 8.0


class SubprocessRunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.repo = self.base / "repo"
        self.repo.mkdir()

    def runner(self, source: str, **kwargs) -> SubprocessRunner:
        script = self.base / f"stand_in_{abs(hash(source))}.py"
        script.write_text(textwrap.dedent(source), encoding="utf-8")
        return SubprocessRunner((sys.executable, "-I", str(script)), **kwargs)

    def timed(self, runner: SubprocessRunner, timeout_s: int) -> tuple[RawRun, float]:
        start = time.monotonic()
        raw = runner.run(request("build", self.repo, timeout_s=timeout_s))
        return raw, time.monotonic() - start

    def test_passes_the_cli_argv_in_the_request_cwd_with_no_stdin(self) -> None:
        req = request("research", self.repo, prompt="Research tonight.")
        raw = self.runner(ECHO).run(req)
        self.assertEqual((raw.returncode, raw.timed_out), (0, False))
        seen = json.loads(parse_cli_json(raw.stdout).result)
        self.assertEqual(seen["argv"], list(build_argv(req)[1:]))
        self.assertEqual(seen["argv"][1], "Research tonight.")
        self.assertEqual(Path(seen["cwd"]).resolve(), self.repo)
        self.assertEqual(seen["stdin"], "")

    def test_nonzero_exit_is_reported_not_raised(self) -> None:
        raw = self.runner(FAIL).run(request("build", self.repo))
        self.assertEqual((raw.returncode, raw.timed_out), (3, False))
        self.assertIn("Failed to authenticate", raw.stderr)

    def test_timeout_kills_the_phase_and_says_so(self) -> None:
        raw, elapsed = self.timed(self.runner(SLEEP), timeout_s=1)
        self.assertTrue(raw.timed_out)
        self.assertNotEqual(raw.returncode, 0)
        self.assertLess(elapsed, PROMPT_S)

    def test_a_phase_ignoring_sigterm_is_killed_after_the_grace(self) -> None:
        raw, elapsed = self.timed(self.runner(IGNORE_TERM, term_grace_s=0.5), timeout_s=1)
        self.assertTrue(raw.timed_out)
        self.assertEqual(raw.stdout, "started")
        self.assertLess(elapsed, PROMPT_S)

    def test_a_background_child_holding_stdout_cannot_hang_the_runner(self) -> None:
        raw, elapsed = self.timed(self.runner(ORPHAN), timeout_s=2)
        self.assertTrue(raw.timed_out)
        self.assertEqual(parse_cli_json(raw.stdout).result, "done early")
        self.assertLess(elapsed, PROMPT_S)

    def test_invalid_utf8_is_replaced_not_raised(self) -> None:
        raw = self.runner(BAD_UTF8).run(request("build", self.repo))
        self.assertEqual(parse_cli_json(raw.stdout).result, "caf�")

    def test_a_missing_program_raises(self) -> None:
        runner = SubprocessRunner((str(self.base / "no-such-claude"),))
        with self.assertRaises(FileNotFoundError):
            runner.run(request("build", self.repo))

    def test_rejects_bad_construction(self) -> None:
        for program in ((), ("",), ("claude", "")):
            with self.subTest(program=program), self.assertRaises(ValueError):
                SubprocessRunner(program)
        for grace in (0, -1, True):
            with self.subTest(grace=grace), self.assertRaises(ValueError):
                SubprocessRunner(term_grace_s=grace)


if __name__ == "__main__":
    unittest.main()
