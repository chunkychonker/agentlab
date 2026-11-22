"""Milestone 9: ``LaunchdJob`` refuses bad fields; ``slot_for`` stays in the window (I2); argv rules (I6).

Numbers refer to research/2026-11-22-nightloop-launchd-scheduler.md.
"""

from __future__ import annotations

import unittest
from pathlib import PurePosixPath

from nightloop.cli.window import Run, window_disposition
from nightloop.model import Window
from nightloop.schedule import ScheduleError, default_label, domain_for, nightly_job, plist_path, slot_for

from .schedule_support import make_job


def every_window():
    for start in range(0, 24):
        for end in range(start + 1, 25):
            yield Window(start, end)


class LaunchdJobTest(unittest.TestCase):
    def test_a_valid_job_builds(self) -> None:
        self.assertEqual(make_job().hour, 2)

    def assert_rejected(self, field: str, **override: object) -> None:
        with self.assertRaises(ScheduleError) as caught:
            make_job(**override)
        self.assertIn(field, str(caught.exception))

    def test_bad_labels(self) -> None:
        for label in ("", "has space", "slash/label", "semi;colon", "é", 5):
            with self.subTest(label=label):
                self.assert_rejected("label", label=label)

    def test_bad_argv(self) -> None:
        cases = {
            "argv": [(), ["/bin/x"], None],
            "argv[1]": [("/bin/x", ""), ("/bin/x", "a\x00b"), ("/bin/x", "line\nbreak"), ("/bin/x", 3)],
            "argv[0]": [("python3", "-m", "nightloop"), ("relative/bin",)],
        }
        for field, values in cases.items():
            for argv in values:
                with self.subTest(argv=argv):
                    self.assert_rejected(field, argv=argv)

    def test_relative_or_non_path_paths(self) -> None:
        for field in ("workdir", "out_log", "err_log"):
            for value in (PurePosixPath("rel/dir"), "/as/a/str", PurePosixPath("/a\x00b")):
                with self.subTest(field=field, value=value):
                    self.assert_rejected(field, **{field: value})

    def test_empty_path_env(self) -> None:
        for value in ("", None, "a\x00b"):
            with self.subTest(value=value):
                self.assert_rejected("path_env", path_env=value)

    def test_hour_and_minute_ranges_and_bool(self) -> None:
        for hour in (-1, 24, True, 2.0, "2"):
            with self.subTest(hour=hour):
                self.assert_rejected("hour", hour=hour)
        for minute in (-1, 60, False, 0.5):
            with self.subTest(minute=minute):
                self.assert_rejected("minute", minute=minute)
        make_job(hour=0, minute=0)
        make_job(hour=23, minute=59)

    def test_ignore_window_can_never_be_scheduled(self) -> None:  # I6
        self.assert_rejected("--ignore-window", argv=("/bin/nightloop", "run", "--ignore-window"))

    def test_xml_special_characters_are_accepted(self) -> None:  # I4: no rejection, unlike bash
        job = make_job(workdir=PurePosixPath("/a & <b> \"c\" 'd'"), argv=("/x y/&.py", "<>"))
        self.assertEqual(job.workdir, PurePosixPath("/a & <b> \"c\" 'd'"))


class SlotTest(unittest.TestCase):
    def test_default_slot_is_inside_every_window(self) -> None:  # I2, criterion 3
        count = 0
        for window in every_window():
            hour, minute = slot_for(window)
            self.assertEqual((hour, minute), (window.start_hour, 0))
            self.assertIsInstance(window_disposition(hour, window, ignore_window=False), Run, window)
            count += 1
        self.assertEqual(count, 300)

    def test_every_explicit_hour_inside_is_accepted_and_outside_raises(self) -> None:
        for window in every_window():
            for hour in range(0, 24):
                inside = window.start_hour <= hour < window.end_hour
                with self.subTest(window=window, hour=hour):
                    if inside:
                        self.assertEqual(slot_for(window, hour, 59), (hour, 59))
                    else:
                        with self.assertRaises(ScheduleError):
                            slot_for(window, hour)

    def test_the_window_end_is_exclusive(self) -> None:
        with self.assertRaises(ScheduleError) as caught:
            slot_for(Window(1, 6), 6)
        self.assertIn("outside the run window [01:00, 06:00)", str(caught.exception))

    def test_bad_minute_or_hour_type(self) -> None:
        for hour, minute in ((None, 60), (None, -1), (True, 0), (2, True)):
            with self.subTest(hour=hour, minute=minute), self.assertRaises(ScheduleError):
                slot_for(Window(1, 6), hour, minute)


class NightlyJobTest(unittest.TestCase):
    def build(self, **overrides: object):
        fields: dict[str, object] = dict(
            label="dev.nightloop.x.00000000", command=("/venv/bin/python", "-m", "nightloop"),
            repo=PurePosixPath("/work/repo"), config_path=PurePosixPath("/work/repo/nightloop.toml"),
            path_env="/usr/bin:/bin", hour=1, minute=30,
        )
        fields.update(overrides)
        return nightly_job(**fields)  # type: ignore[arg-type]

    def test_argv_names_repo_and_config_and_never_ignore_window(self) -> None:  # I6
        job = self.build()
        self.assertEqual(job.argv, ("/venv/bin/python", "-m", "nightloop", "run",
                                    "--repo", "/work/repo", "--config", "/work/repo/nightloop.toml"))
        self.assertNotIn("--ignore-window", job.argv)
        self.assertEqual(job.workdir, PurePosixPath("/work/repo"))
        self.assertEqual(job.out_log.parent, PurePosixPath("/work/repo/logs"))

    def test_a_command_carrying_ignore_window_is_refused(self) -> None:  # I6
        with self.assertRaises(ScheduleError):
            self.build(command=("/bin/nightloop", "--ignore-window"))

    def test_relative_config_or_repo_is_refused(self) -> None:
        with self.assertRaises(ScheduleError) as caught:
            self.build(config_path=PurePosixPath("nightloop.toml"))
        self.assertIn("config_path", str(caught.exception))
        with self.assertRaises(ScheduleError) as caught:
            self.build(repo=PurePosixPath("repo"))
        self.assertIn("repo", str(caught.exception))


class NamingTest(unittest.TestCase):
    def test_default_label_is_valid_stable_and_per_path(self) -> None:
        a = default_label(PurePosixPath("/Users/me/my repo & co"))
        self.assertEqual(a, default_label(PurePosixPath("/Users/me/my repo & co")))
        self.assertRegex(a, r"^dev\.nightloop\.my-repo-co\.[0-9a-f]{8}$")
        self.assertNotEqual(default_label(PurePosixPath("/a/repo")), default_label(PurePosixPath("/b/repo")))
        make_job(label=default_label(PurePosixPath("/")))  # an empty name still gives a valid label

    def test_plist_path_and_domain(self) -> None:
        self.assertEqual(plist_path(PurePosixPath("/Users/me"), "x.y"),
                         PurePosixPath("/Users/me/Library/LaunchAgents/x.y.plist"))
        self.assertEqual(domain_for(501), "gui/501")
        for uid in (-1, True, "501"):
            with self.subTest(uid=uid), self.assertRaises(ScheduleError):
                domain_for(uid)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
