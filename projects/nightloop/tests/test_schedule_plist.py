"""Milestone 9, criterion 4 (I3, I4): the plist is deterministic, round-trips, and always has Hour and Minute."""

from __future__ import annotations

import plistlib
import unittest
from pathlib import PurePosixPath

from nightloop.schedule import ScheduleError, parse_plist, render_plist

from .schedule_support import make_job

AWKWARD = "/Users/a b/& <c> \"d\" 'e'"


class RenderTest(unittest.TestCase):
    def test_same_job_same_bytes(self) -> None:  # I3
        self.assertEqual(render_plist(make_job()), render_plist(make_job()))

    def test_keys_and_values(self) -> None:
        loaded = plistlib.loads(render_plist(make_job(hour=3, minute=7)))
        self.assertEqual(loaded["Label"], "dev.nightloop.test.0123abcd")
        self.assertEqual(loaded["ProgramArguments"][0], "/usr/bin/python3")
        self.assertEqual(loaded["WorkingDirectory"], "/r")
        self.assertEqual(loaded["EnvironmentVariables"], {"PATH": "/usr/bin:/bin"})
        self.assertIs(loaded["RunAtLoad"], False)
        self.assertEqual(loaded["StandardOutPath"], "/r/logs/out.log")
        self.assertEqual(loaded["StandardErrorPath"], "/r/logs/err.log")

    def test_hour_and_minute_are_both_present_even_at_zero(self) -> None:  # criterion 4, mutation: omit Minute
        for hour, minute in ((0, 0), (2, 0), (23, 59)):
            with self.subTest(hour=hour, minute=minute):
                slot = plistlib.loads(render_plist(make_job(hour=hour, minute=minute)))["StartCalendarInterval"]
                self.assertEqual(slot, {"Hour": hour, "Minute": minute})

    def test_round_trip(self) -> None:  # I3
        job = make_job(hour=4, minute=45)
        self.assertEqual(parse_plist(render_plist(job)), job)

    def test_awkward_characters_survive(self) -> None:  # I4
        job = make_job(
            argv=(f"{AWKWARD}/python", "-m", "nightloop", "run", "--repo", AWKWARD),
            workdir=PurePosixPath(AWKWARD),
            path_env=f"{AWKWARD}/bin:/usr/bin",
            out_log=PurePosixPath(f"{AWKWARD}/logs/o.log"),
            err_log=PurePosixPath(f"{AWKWARD}/logs/e.log"),
        )
        data = render_plist(job)
        self.assertIn(b"&amp;", data)
        self.assertEqual(parse_plist(data), job)
        loaded = plistlib.loads(data)
        self.assertEqual(loaded["WorkingDirectory"], AWKWARD)
        self.assertEqual(loaded["ProgramArguments"][-1], AWKWARD)


class ParseTest(unittest.TestCase):
    def mutated(self, change) -> bytes:
        loaded = plistlib.loads(render_plist(make_job()))
        change(loaded)
        return plistlib.dumps(loaded)

    def test_rejects_what_nightloop_did_not_write(self) -> None:
        cases = {
            "not a plist": b"not xml at all",
            "array root": plistlib.dumps(["x"]),
            "extra key": self.mutated(lambda d: d.update(KeepAlive=True)),
            "missing minute": self.mutated(lambda d: d["StartCalendarInterval"].pop("Minute")),
            "extra env": self.mutated(lambda d: d["EnvironmentVariables"].update(HOME="/x")),
            "argv not array": self.mutated(lambda d: d.update(ProgramArguments="/bin/x")),
            "run at load": self.mutated(lambda d: d.update(RunAtLoad=True)),
            "bool hour": self.mutated(lambda d: d["StartCalendarInterval"].update(Hour=True)),
            "relative workdir": self.mutated(lambda d: d.update(WorkingDirectory="rel")),
            "workdir not str": self.mutated(lambda d: d.update(WorkingDirectory=5)),
        }
        for name, data in cases.items():
            with self.subTest(case=name), self.assertRaises(ScheduleError):
                parse_plist(data)


if __name__ == "__main__":
    unittest.main()
