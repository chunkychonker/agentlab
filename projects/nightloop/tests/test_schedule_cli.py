"""Milestone 9, criteria 2, 5, 7 and 9: ``nightloop schedule`` through ``main``.

``main`` gets a ``Host`` whose home is a temp dir and a ``FakeLaunchctl``,
so nothing reaches real launchd or the real ``~/Library/LaunchAgents``.
"""

from __future__ import annotations

import contextlib
import io
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from nightloop.cli import ExitCode, main
from nightloop.cli.schedule_cmd import Host
from nightloop.schedule import JobState, default_label, render_plist
from nightloop.schedule.job import nightly_job

from .cli_support import tree_digest, write_config
from .schedule_support import FakeLaunchctl

PYTHON = "/opt/venv/bin/python3"
PATH_ENV = "/opt/homebrew/bin:/usr/bin:/bin"


class Called(AssertionError):
    pass


class ScheduleCliCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        self.home = root / "home"
        self.home.mkdir()
        self.repo = root / "my repo & co"
        self.repo.mkdir()
        self.config = write_config(self.repo / "nightloop.toml", start=1, end=6)
        self.fake = FakeLaunchctl()
        self.factory_calls = 0
        self.label = default_label(self.repo)  # type: ignore[arg-type]
        self.plist = self.home / "Library" / "LaunchAgents" / f"{self.label}.plist"

    def host(self, **overrides: object) -> Host:
        fields: dict[str, object] = dict(platform="darwin", home=self.home, uid=501,
                                         path_env=PATH_ENV, executable=PYTHON)
        fields.update(overrides)
        return Host(**fields)  # type: ignore[arg-type]

    def factory(self) -> FakeLaunchctl:
        self.factory_calls += 1
        return self.fake

    def call(self, *argv: str, host: Host | None = None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        chosen = host or self.host()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["schedule", *argv, "--repo", str(self.repo)],
                        host_factory=lambda: chosen, launchctl_factory=self.factory)
        return code, out.getvalue(), err.getvalue()


class InstallTest(ScheduleCliCase):
    def test_install_writes_the_expected_plist_and_loads_it(self) -> None:
        code, out, _ = self.call("install")
        self.assertEqual(code, ExitCode.OK)
        expected = render_plist(nightly_job(
            label=self.label, command=(PYTHON, "-m", "nightloop"), repo=self.repo,  # type: ignore[arg-type]
            config_path=self.config, path_env=PATH_ENV, hour=1, minute=0,  # type: ignore[arg-type]
        ))
        self.assertEqual(self.plist.read_bytes(), expected)
        self.assertEqual(self.fake.states[self.label], JobState.LOADED_IDLE)
        loaded = plistlib.loads(self.plist.read_bytes())
        self.assertEqual(loaded["ProgramArguments"][3:], ["run", "--repo", str(self.repo), "--config", str(self.config)])
        self.assertNotIn("--ignore-window", loaded["ProgramArguments"])  # I6
        self.assertIn("installed and loaded", out)
        self.assertIn("sudo pmset repeat wakeorpoweron MTWRFSU 00:50:00", out)
        self.assertIn("nightloop ran nothing", out)
        self.assertTrue((self.repo / "logs").is_dir())

    def test_install_twice_is_a_no_op(self) -> None:  # I1, criterion 2
        self.call("install")
        before = self.plist.stat().st_mtime_ns
        digest = tree_digest(self.home)
        self.fake.calls.clear()
        code, out, _ = self.call("install")
        self.assertEqual(code, ExitCode.OK)
        self.assertIn("nothing to do", out)
        self.assertEqual(self.fake.mutating_calls(), [])
        self.assertEqual(self.plist.stat().st_mtime_ns, before)
        self.assertEqual(tree_digest(self.home), digest)

    def test_hour_and_minute_flags(self) -> None:
        code, _, _ = self.call("install", "--hour", "5", "--minute", "45")
        self.assertEqual(code, ExitCode.OK)
        self.assertEqual(plistlib.loads(self.plist.read_bytes())["StartCalendarInterval"], {"Hour": 5, "Minute": 45})

    def test_an_hour_outside_the_window_is_usage_and_builds_nothing(self) -> None:  # criterion 3
        for hour in ("6", "0", "23"):
            with self.subTest(hour=hour):
                code, _, err = self.call("install", "--hour", hour)
                self.assertEqual(code, ExitCode.USAGE)
                self.assertIn("outside the run window", err)
        self.assertEqual(self.factory_calls, 0)
        self.assertFalse(self.plist.exists())

    def test_command_flag(self) -> None:
        code, _, _ = self.call("install", "--command", "/opt/venv/bin/nightloop")
        self.assertEqual(code, ExitCode.OK)
        argv = plistlib.loads(self.plist.read_bytes())["ProgramArguments"]
        self.assertEqual(argv[:2], ["/opt/venv/bin/nightloop", "run"])
        code, _, err = self.call("install", "--command", "nightloop")
        self.assertEqual(code, ExitCode.USAGE)
        self.assertIn("--command: must be an absolute path", err)

    def test_unset_path_or_bad_config_is_usage(self) -> None:
        code, _, err = self.call("install", host=self.host(path_env=None))
        self.assertEqual(code, ExitCode.USAGE)
        self.assertIn("PATH is not set", err)
        self.config.write_text("schema_version = 1\n")
        code, _, err = self.call("install")
        self.assertEqual(code, ExitCode.USAGE)
        self.assertIn("config", err)
        self.assertEqual(self.factory_calls, 0)

    def test_running_job_exits_job_running_and_mutates_nothing(self) -> None:  # I5, criterion 5
        self.call("install", "--hour", "2")
        self.fake.states[self.label] = JobState.RUNNING
        self.fake.calls.clear()
        digest = tree_digest(self.home)
        code, _, err = self.call("install", "--hour", "3")
        self.assertEqual(code, ExitCode.JOB_RUNNING)
        self.assertIn("running right now", err)
        self.assertEqual(self.fake.mutating_calls(), [])
        self.assertEqual(tree_digest(self.home), digest)
        self.assertEqual(self.call("uninstall")[0], ExitCode.JOB_RUNNING)
        self.assertTrue(self.plist.exists())

    def test_a_launchd_failure_is_environment(self) -> None:
        self.fake.fail["bootstrap"] = "Input/output error"
        code, _, err = self.call("install")
        self.assertEqual(code, ExitCode.ENVIRONMENT)
        self.assertIn("UNLOADED", err)


class DryRunTest(ScheduleCliCase):
    def test_install_dry_run_changes_nothing(self) -> None:  # criterion 7
        digest = (tree_digest(self.home), tree_digest(self.repo))
        code, out, _ = self.call("install", "--dry-run")
        self.assertEqual(code, ExitCode.OK)
        self.assertEqual((tree_digest(self.home), tree_digest(self.repo)), digest)
        self.assertEqual(self.fake.mutating_calls(), [])
        self.assertEqual([c[0] for c in self.fake.calls], ["state"])
        self.assertIn("plan: write the plist, bootstrap (load) the job", out)
        self.assertIn("<key>StartCalendarInterval</key>", out)
        self.assertIn("dry run: nothing was changed", out)

    def test_uninstall_dry_run_changes_nothing(self) -> None:  # criterion 7
        self.call("install")
        self.fake.calls.clear()
        digest = tree_digest(self.home)
        code, out, _ = self.call("uninstall", "--dry-run")
        self.assertEqual(code, ExitCode.OK)
        self.assertIn("plan: bootout (unload) the job, remove the plist", out)
        self.assertEqual(tree_digest(self.home), digest)
        self.assertEqual(self.fake.mutating_calls(), [])


class UninstallTest(ScheduleCliCase):
    def test_uninstall_removes_and_a_second_does_nothing(self) -> None:
        self.call("install")
        code, out, _ = self.call("uninstall")
        self.assertEqual(code, ExitCode.OK)
        self.assertFalse(self.plist.exists())
        self.assertIs(self.fake.states[self.label], JobState.NOT_LOADED)
        self.fake.calls.clear()
        code, out, _ = self.call("uninstall")
        self.assertEqual(code, ExitCode.OK)
        self.assertIn("nothing to do", out)
        self.assertEqual(self.fake.mutating_calls(), [])


class StatusTest(ScheduleCliCase):
    def test_not_installed(self) -> None:
        code, out, _ = self.call("status")
        self.assertEqual(code, ExitCode.OK)
        self.assertIn("not installed", out)

    def test_installed_with_a_missing_program_is_flagged(self) -> None:
        self.call("install")  # PYTHON does not exist on this machine
        code, out, err = self.call("status")
        self.assertEqual(code, ExitCode.ENVIRONMENT)
        self.assertIn("inside the run window", out)
        self.assertIn(f"{PYTHON} does not exist", err)

    def test_installed_and_healthy(self) -> None:
        program = self.repo / "nightloop-bin"
        program.write_text("")
        self.call("install", "--command", str(program))
        code, out, err = self.call("status")
        self.assertEqual(code, ExitCode.OK, err)
        self.assertIn("loaded, idle", out)

    def test_a_window_that_moved_away_from_the_slot_is_flagged(self) -> None:
        program = self.repo / "nightloop-bin"
        program.write_text("")
        self.call("install", "--command", str(program))
        write_config(self.config, start=7, end=9)
        code, _, err = self.call("status")
        self.assertEqual(code, ExitCode.ENVIRONMENT)
        self.assertIn("outside the run window [07:00, 09:00)", err)

    def test_a_foreign_plist_is_flagged(self) -> None:
        self.plist.parent.mkdir(parents=True)
        self.plist.write_bytes(plistlib.dumps({"Label": self.label, "KeepAlive": True}))
        code, _, err = self.call("status")
        self.assertEqual(code, ExitCode.ENVIRONMENT)
        self.assertIn("not a plist nightloop writes", err)


class PlatformTest(ScheduleCliCase):
    def test_non_mac_exits_usage_and_builds_no_launchctl(self) -> None:  # criterion 9
        for action in ("install", "uninstall", "status"):
            with self.subTest(action=action):
                code, _, err = self.call(action, host=self.host(platform="linux"))
                self.assertEqual(code, ExitCode.USAGE)
                self.assertIn("macOS", err)
        self.assertEqual(self.factory_calls, 0)
        self.assertFalse((self.home / "Library").exists())


class RunDoesNotLoadTheSchedulerTest(unittest.TestCase):
    def test_importing_main_and_parsing_run_loads_no_plistlib(self) -> None:
        # A Python whose pyexpat cannot load (seen on Homebrew 3.12.14) cannot
        # import plistlib; `run` and `report` must not depend on it.
        project = Path(__file__).resolve().parent.parent
        probe = (
            "import sys; sys.path.insert(0, sys.argv[1]);"
            "from nightloop.cli.main import _parser;"
            "_parser().parse_args(['run', '--dry-run']);"
            "loaded = sorted(m for m in sys.modules if m == 'plistlib' or m.startswith('nightloop.schedule'));"
            "print(loaded); sys.exit(1 if loaded else 0)"
        )
        done = subprocess.run([sys.executable, "-I", "-c", probe, str(project)],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


class ExitCodeCompatTest(unittest.TestCase):
    def test_existing_codes_are_unchanged(self) -> None:
        from nightloop.cli.main import ExitCode as FromMain
        self.assertIs(FromMain, ExitCode)
        self.assertEqual([int(c) for c in ExitCode][:6], [0, 1, 2, 3, 4, 5])
        self.assertEqual(ExitCode.JOB_RUNNING, 6)


if __name__ == "__main__":
    unittest.main()
