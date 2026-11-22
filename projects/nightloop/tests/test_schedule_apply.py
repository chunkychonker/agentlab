"""Milestone 9, criteria 2, 5 and 6: ``apply`` against a fake launchd and a temp ``~/Library/LaunchAgents``.

I1 (a second install changes nothing), I5 (RUNNING mutates nothing) and the
failure ordering: a failure after ``bootout`` says UNLOADED and gives the
restore command, and a lint failure never writes the final path.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nightloop.schedule import (
    JobState, ScheduleError, Step, apply, backup_path, plan_install, plan_uninstall, render_plist,
)

from .schedule_support import FakeLaunchctl, make_job

DOMAIN = "gui/501"


class ApplyCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name).resolve()
        self.job = make_job()
        self.plist = self.home / "Library" / "LaunchAgents" / f"{self.job.label}.plist"
        self.logs = self.home / "repo" / "logs"
        self.desired = render_plist(self.job)

    def install(self, launchctl: FakeLaunchctl, desired: bytes | None = None) -> tuple[Step, ...]:
        """One install the way the CLI does it: read state and file, plan, apply."""
        desired = self.desired if desired is None else desired
        installed = self.plist.read_bytes() if self.plist.exists() else None
        steps = plan_install(desired, installed, launchctl.state(DOMAIN, self.job.label))
        apply(steps, plist_path=self.plist, desired=desired, launchctl=launchctl, domain=DOMAIN,
              label=self.job.label, log_dirs=(self.logs,))
        return steps

    def stray_temp_files(self) -> list[str]:
        return [p.name for p in self.plist.parent.iterdir() if p.name.endswith(".tmp")]


class IdempotenceTest(ApplyCase):
    def test_install_twice_second_is_nothing(self) -> None:  # I1, criterion 2
        fake = FakeLaunchctl()
        self.assertEqual(self.install(fake), (Step.WRITE_PLIST, Step.BOOTSTRAP))
        self.assertEqual(self.plist.read_bytes(), self.desired)
        self.assertEqual(fake.states[self.job.label], JobState.LOADED_IDLE)
        self.assertTrue(self.logs.is_dir())
        before = self.plist.stat()
        fake.calls.clear()
        self.assertEqual(self.install(fake), (Step.NOTHING,))
        self.assertEqual(fake.mutating_calls(), [])
        after = self.plist.stat()
        self.assertEqual((before.st_mtime_ns, before.st_ino), (after.st_mtime_ns, after.st_ino))
        self.assertEqual(self.plist.read_bytes(), self.desired)
        self.assertFalse(backup_path(self.plist).exists())

    def test_a_changed_job_replaces_and_backs_up_once(self) -> None:
        fake = FakeLaunchctl()
        self.install(fake)
        second = render_plist(make_job(hour=3))
        self.assertEqual(self.install(fake, second), (Step.BOOTOUT, Step.WRITE_PLIST, Step.BOOTSTRAP))
        self.assertEqual(self.plist.read_bytes(), second)
        self.assertEqual(backup_path(self.plist).read_bytes(), self.desired)
        self.assertEqual(fake.loaded_plists[self.job.label], second)
        third = render_plist(make_job(hour=4))
        self.install(fake, third)
        self.assertEqual(backup_path(self.plist).read_bytes(), self.desired, "the backup is kept from the first replace")
        self.assertEqual(self.plist.stat().st_mode & 0o777, 0o644)
        self.assertEqual(self.stray_temp_files(), [])

    def test_uninstall_then_uninstall_again(self) -> None:
        fake = FakeLaunchctl()
        self.install(fake)
        steps = plan_uninstall(self.plist.read_bytes(), fake.state(DOMAIN, self.job.label))
        apply(steps, plist_path=self.plist, desired=b"", launchctl=fake, domain=DOMAIN,
              label=self.job.label, log_dirs=())
        self.assertFalse(self.plist.exists())
        self.assertEqual(fake.states[self.job.label], JobState.NOT_LOADED)
        fake.calls.clear()
        again = plan_uninstall(None, fake.state(DOMAIN, self.job.label))
        self.assertEqual(again, (Step.NOTHING,))
        apply(again, plist_path=self.plist, desired=b"", launchctl=fake, domain=DOMAIN,
              label=self.job.label, log_dirs=())
        self.assertEqual(fake.mutating_calls(), [])


class RunningTest(ApplyCase):
    def test_running_causes_no_mutation(self) -> None:  # I5, criterion 5
        self.plist.parent.mkdir(parents=True)
        self.plist.write_bytes(b"old")
        fake = FakeLaunchctl({self.job.label: JobState.RUNNING})
        with self.assertRaises(ScheduleError) as caught:
            self.install(fake)
        self.assertIn("running right now", str(caught.exception))
        self.assertEqual(fake.mutating_calls(), [])
        self.assertEqual(self.plist.read_bytes(), b"old")
        self.assertFalse(backup_path(self.plist).exists())

    def test_an_empty_plan_is_refused(self) -> None:
        with self.assertRaises(ScheduleError):
            apply((), plist_path=self.plist, desired=self.desired, launchctl=FakeLaunchctl(),
                  domain=DOMAIN, label=self.job.label, log_dirs=())


class FailureOrderingTest(ApplyCase):
    def loaded_with_old_plist(self, **fail: str) -> FakeLaunchctl:
        fake = FakeLaunchctl()
        self.install(fake, render_plist(make_job(hour=5)))
        fake.fail.update(fail)
        fake.calls.clear()
        return fake

    def test_bootstrap_failing_after_bootout_says_unloaded_and_how_to_restore(self) -> None:  # criterion 6
        fake = self.loaded_with_old_plist(bootstrap="Input/output error")
        with self.assertRaises(ScheduleError) as caught:
            self.install(fake)
        message = str(caught.exception)
        self.assertIn("UNLOADED", message)
        self.assertIn(f"launchctl bootstrap {DOMAIN} {self.plist}", message)
        self.assertIn(str(backup_path(self.plist)), message)
        self.assertEqual([c[0] for c in fake.mutating_calls()], ["bootout", "enable", "bootstrap"])

    def test_lint_failure_writes_nothing_at_the_final_path(self) -> None:  # criterion 6
        fake = self.loaded_with_old_plist(lint="bad plist")
        old = self.plist.read_bytes()
        with self.assertRaises(ScheduleError) as caught:
            self.install(fake)
        message = str(caught.exception)
        self.assertEqual(self.plist.read_bytes(), old)
        self.assertEqual(self.stray_temp_files(), [])
        self.assertIn("UNLOADED", message)
        self.assertIn(f"launchctl bootstrap {DOMAIN} {self.plist}", message)
        self.assertNotIn("bootstrap", [c[0] for c in fake.calls])

    def test_lint_failure_on_a_first_install_says_nothing_changed(self) -> None:
        fake = FakeLaunchctl(fail={"lint": "bad plist"})
        with self.assertRaises(ScheduleError) as caught:
            self.install(fake)
        self.assertIn("Nothing was loaded or unloaded", str(caught.exception))
        self.assertFalse(self.plist.exists())
        self.assertEqual(fake.mutating_calls(), [])

    def test_bootout_failure_leaves_the_old_job_and_plist(self) -> None:
        fake = self.loaded_with_old_plist(bootout="Operation not permitted")
        old = self.plist.read_bytes()
        with self.assertRaises(ScheduleError) as caught:
            self.install(fake)
        self.assertIn("still loaded", str(caught.exception))
        self.assertEqual(self.plist.read_bytes(), old)
        self.assertEqual(fake.states[self.job.label], JobState.LOADED_IDLE)

    def test_a_backup_that_cannot_be_saved_changes_nothing(self) -> None:
        fake = self.loaded_with_old_plist()
        self.plist.parent.chmod(0o500)  # the directory is read-only: the copy fails
        self.addCleanup(self.plist.parent.chmod, 0o755)
        with self.assertRaises(ScheduleError) as caught:
            self.install(fake)
        self.assertIn("Nothing was changed", str(caught.exception))
        self.assertEqual(fake.mutating_calls(), [])


if __name__ == "__main__":
    unittest.main()
