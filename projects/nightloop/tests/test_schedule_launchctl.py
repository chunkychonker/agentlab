"""Milestone 9: ``SubprocessLaunchctl`` runs the right argv and turns failures into ``ScheduleError``.

Uses Python stand-in executables for ``launchctl`` and ``plutil`` that log
their argv and answer from a script, so no test touches real launchd.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath

from nightloop.schedule import JobState, ScheduleError, SubprocessLaunchctl

from .schedule_support import PRINT_IDLE, PRINT_NOT_FOUND_STDERR, PRINT_RUNNING

STUB = """\
#!{python}
import json, sys
log, reply = {log!r}, {reply!r}
with open(log, "a") as fp:
    fp.write(json.dumps(sys.argv[1:]) + "\\n")
answer = json.load(open(reply))
sys.stdout.write(answer.get("stdout", ""))
sys.stderr.write(answer.get("stderr", ""))
if answer.get("sleep"):
    import time; time.sleep(answer["sleep"])
sys.exit(answer.get("rc", 0))
"""


class SubprocessLaunchctlTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.log = self.dir / "argv.log"
        self.reply = self.dir / "reply.json"
        self.answer()
        self.program = self.dir / "fake-launchctl"
        self.program.write_text(STUB.format(python=sys.executable, log=str(self.log), reply=str(self.reply)))
        self.program.chmod(0o755)
        self.launchctl = SubprocessLaunchctl(launchctl=str(self.program), plutil=str(self.program), timeout_s=5)

    def answer(self, **reply: object) -> None:
        self.reply.write_text(json.dumps(reply))

    def argvs(self) -> list[list[str]]:
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_state_running_idle_and_not_found(self) -> None:
        self.answer(stdout=PRINT_RUNNING)
        self.assertIs(self.launchctl.state("gui/501", "a.b"), JobState.RUNNING)
        self.answer(stdout=PRINT_IDLE)
        self.assertIs(self.launchctl.state("gui/501", "a.b"), JobState.LOADED_IDLE)
        self.answer(stderr=PRINT_NOT_FOUND_STDERR, rc=113)
        self.assertIs(self.launchctl.state("gui/501", "a.b"), JobState.NOT_LOADED)
        self.assertEqual(self.argvs(), [["print", "gui/501/a.b"]] * 3)

    def test_mutating_commands_pass_exact_argv(self) -> None:
        self.launchctl.enable("gui/501", "a.b")
        self.launchctl.bootstrap("gui/501", PurePosixPath("/h/Library/LaunchAgents/a b.plist"))
        self.launchctl.bootout("gui/501", "a.b")
        self.launchctl.lint(PurePosixPath("/tmp/x.plist"))
        self.assertEqual(self.argvs(), [
            ["enable", "gui/501/a.b"],
            ["bootstrap", "gui/501", "/h/Library/LaunchAgents/a b.plist"],
            ["bootout", "gui/501/a.b"],
            ["-lint", "/tmp/x.plist"],
        ])

    def test_a_failure_quotes_the_command_and_last_line(self) -> None:
        self.answer(stderr="noise\nBootstrap failed: 5: Input/output error\n", rc=5)
        with self.assertRaises(ScheduleError) as caught:
            self.launchctl.bootstrap("gui/501", PurePosixPath("/p.plist"))
        message = str(caught.exception)
        self.assertIn("bootstrap gui/501 /p.plist", message)
        self.assertIn("exited 5: Bootstrap failed: 5: Input/output error", message)
        self.assertNotIn("noise", message)

    def test_a_timeout_is_a_schedule_error(self) -> None:
        self.answer(sleep=3)
        slow = SubprocessLaunchctl(launchctl=str(self.program), timeout_s=0.5)
        with self.assertRaises(ScheduleError) as caught:
            slow.bootout("gui/501", "a.b")
        self.assertIn("ran past", str(caught.exception))

    def test_a_program_that_cannot_start_raises_oserror(self) -> None:
        missing = SubprocessLaunchctl(launchctl=os.path.join(str(self.dir), "nope"))
        with self.assertRaises(OSError):
            missing.state("gui/501", "a.b")


if __name__ == "__main__":
    unittest.main()
