"""Milestone 10, criteria 1, 6 and 7: ``nightloop schedule-gha`` through ``main``.

Every test runs against a temp repo or reads the checked-in example; none
talks to GitHub or a model.
"""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from nightloop.cli import ExitCode, main

from .cli_support import tree_digest, write_config
from .gha_support import GOLDEN, GOLDEN_FLAGS, PLUGIN_CONFIG, PROJECT


def call(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(["schedule-gha", *argv])
    return code, out.getvalue(), err.getvalue()


class DryRunTest(unittest.TestCase):
    def test_dry_run_prints_the_golden_file_on_stdout(self) -> None:  # criterion 1 through the CLI
        code, out, err = call("--dry-run", "--repo", str(PROJECT), "--config", str(PLUGIN_CONFIG), *GOLDEN_FLAGS)
        self.assertEqual(code, ExitCode.OK, err)
        self.assertEqual(out, GOLDEN.read_text(encoding="utf-8"))
        self.assertIn("never been run on GitHub", err)
        self.assertIn("CLAUDE_CODE_OAUTH_TOKEN", err)


class TempRepoCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.repo = self.root / "my repo & co"
        self.repo.mkdir()
        write_config(self.repo / "nightloop.toml", start=1, end=6)
        self.workflow = self.repo / ".github" / "workflows" / "nightloop.yml"

    def flags(self, **overrides: str) -> list[str]:
        values = dict(zip(GOLDEN_FLAGS[::2], GOLDEN_FLAGS[1::2]))
        values.update({f"--{k.replace('_', '-')}": v for k, v in overrides.items()})
        return ["--repo", str(self.repo), *[x for pair in values.items() for x in pair]]


class WriteTest(TempRepoCase):
    def test_dry_run_writes_nothing(self) -> None:  # I10
        before = tree_digest(self.root)
        code, out, _ = call("--dry-run", *self.flags())
        self.assertEqual(code, ExitCode.OK)
        self.assertIn("    - cron: \"17 1 * * *\"", out)
        self.assertEqual(tree_digest(self.root), before)

    def test_install_then_reinstall_is_a_no_op(self) -> None:  # I9 through main
        code, out, _ = call(*self.flags())
        self.assertEqual(code, ExitCode.OK)
        self.assertIn(f"wrote {self.workflow}", out)
        _, dry_out, _ = call("--dry-run", *self.flags())
        self.assertEqual(self.workflow.read_text(encoding="utf-8"), dry_out)
        before = tree_digest(self.root)
        code, out, _ = call(*self.flags())
        self.assertEqual(code, ExitCode.OK)
        self.assertIn("nothing to do", out)
        self.assertEqual(tree_digest(self.root), before)

    def test_workflow_name_picks_the_file(self) -> None:
        code, _, err = call(*self.flags(workflow_name="night-b"))
        self.assertEqual(code, ExitCode.OK, err)
        self.assertTrue((self.repo / ".github" / "workflows" / "night-b.yml").is_file())
        self.assertFalse(self.workflow.exists())

    def test_an_explicit_hour_and_job_timeout_reach_the_file(self) -> None:
        code, out, err = call("--dry-run", *self.flags(hour="4", job_timeout_minutes="300", timeout_s="1800"))
        self.assertEqual(code, ExitCode.OK, err)
        self.assertIn('    - cron: "17 4 * * *"', out)
        self.assertIn("    timeout-minutes: 300", out)
        self.assertIn("--timeout-s 1800", out)


class RefusalTest(TempRepoCase):
    """Every refusal is exit 2, one line naming the problem, and nothing written."""

    def assert_refused(self, *argv: str, mentions: str) -> None:
        before = tree_digest(self.root)
        code, out, err = call(*argv)
        self.assertEqual(code, ExitCode.USAGE, out + err)
        self.assertIn(mentions, err)
        self.assertEqual(out, "")
        self.assertEqual(tree_digest(self.root), before)

    def test_hour_outside_the_window(self) -> None:
        self.assert_refused(*self.flags(hour="6"), mentions="outside the run window")

    def test_a_night_that_does_not_fit(self) -> None:
        write_config(self.repo / "nightloop.toml", cycles=2)
        self.assert_refused(*self.flags(), mentions="job_timeout_minutes")

    def test_job_timeout_over_the_hosted_limit(self) -> None:
        self.assert_refused(*self.flags(job_timeout_minutes="361"), mentions="361")

    def test_unknown_zone(self) -> None:
        self.assert_refused(*self.flags(timezone="America/Chicag"), mentions="tz database")

    def test_hostile_values(self) -> None:
        for flag, value in (("nightloop_repo", "a/b\nrun: x"), ("timezone", "UTC # x"),
                            ("nightloop_sha", "$(id)"), ("claude_version", "latest"),
                            ("workflow_name", "../x")):
            with self.subTest(flag=flag):
                self.assert_refused(*self.flags(**{flag: value}), mentions=flag.replace("workflow_name", "workflow_stem"))

    def test_minute_zero(self) -> None:
        self.assert_refused(*self.flags(minute="0"), mentions="minute")

    def test_config_outside_the_repo(self) -> None:
        outside = write_config(self.root / "elsewhere.toml")
        self.assert_refused(*self.flags(), "--config", str(outside), mentions="must be inside --repo")

    def test_config_that_does_not_load(self) -> None:
        (self.repo / "nightloop.toml").write_text("schema_version = 9\n", encoding="utf-8")
        self.assert_refused(*self.flags(), mentions="config")

    def test_a_missing_required_flag_is_a_usage_error(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["schedule-gha", "--repo", str(self.repo), "--minute", "5"]), ExitCode.USAGE)
        self.assertFalse((self.repo / ".github").exists())


class ImportIsolationTest(unittest.TestCase):
    def probe(self, code: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, "-I", "-c", code, str(PROJECT)],
                              capture_output=True, text=True, timeout=60)

    def test_run_does_not_load_the_generator(self) -> None:
        done = self.probe(
            "import sys; sys.path.insert(0, sys.argv[1]);"
            "from nightloop.cli.main import _parser;"
            "_parser().parse_args(['run', '--dry-run']);"
            "loaded = sorted(m for m in sys.modules if m.startswith('nightloop.schedule_gha') or m.endswith('gha_cmd'));"
            "print(loaded); sys.exit(1 if loaded else 0)"
        )
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_the_generator_loads_neither_plistlib_nor_the_launchd_package(self) -> None:
        # Homebrew 3.12.14 cannot import plistlib; the generator must not need it.
        done = self.probe(
            "import sys; sys.path.insert(0, sys.argv[1]);"
            "import nightloop.cli.gha_cmd, nightloop.schedule_gha;"
            "loaded = sorted(m for m in sys.modules if m == 'plistlib' or m == 'nightloop.schedule' "
            "or m.startswith('nightloop.schedule.'));"
            "print(loaded); sys.exit(1 if loaded else 0)"
        )
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


class TimezoneReachesTheWindowCheckTest(unittest.TestCase):
    """Finding 2: ``TZ`` (which the workflow sets) decides the hour ``nightloop run`` checks.

    Proven on this machine's libc; the runner's glibc is expected, not verified, to agree.
    """

    def local_hour_at_epoch(self, zone: str) -> str:
        env = {k: v for k, v in os.environ.items() if k != "TZ"}
        env["TZ"] = zone
        done = subprocess.run(
            [sys.executable, "-I", "-c",
             "import sys; sys.path.insert(0, sys.argv[1]);"
             "from nightloop.cli.main import _local_time; print(_local_time(0).tm_hour)",
             str(PROJECT)],
            capture_output=True, text=True, timeout=60, env=env,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout.strip()

    def test_the_run_reads_the_hour_in_tz(self) -> None:
        self.assertEqual(self.local_hour_at_epoch("UTC"), "0")
        self.assertEqual(self.local_hour_at_epoch("Pacific/Honolulu"), "14")  # UTC-10, Dec 31 1969
        self.assertEqual(self.local_hour_at_epoch("Asia/Tokyo"), "9")


if __name__ == "__main__":
    unittest.main()
