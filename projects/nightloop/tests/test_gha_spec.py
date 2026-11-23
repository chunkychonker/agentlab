"""Milestone 10, criteria 2 and 5: ``WorkflowSpec`` refuses unsafe values (I8); the slot stays in the window (I1)."""

from __future__ import annotations

import unittest

from nightloop.model import Window
from nightloop.schedule_gha import Credential, SpecError, ValidSpec, validate_spec

from .gha_support import GOLDEN_FIELDS, make_spec, plugin_config, render, valid

# Characters and shapes that could open a YAML key, a comment, a flow value,
# an expression, or a shell command if placed unquoted.
HOSTILE_FRAGMENTS = (
    "\n", "\r", ": ", "#", "$(", "${{", "`", '"', "'", ";", "&", "|", " ", "{", "}", "[", "]", "*", "\\",
)
STRING_FIELDS = (
    "nightloop_repo", "nightloop_sha", "claude_version", "timezone", "config_path", "workflow_stem",
)


class FieldTest(unittest.TestCase):
    def test_the_golden_fields_are_accepted(self) -> None:
        spec = make_spec()
        self.assertEqual(spec.credential, Credential.OAUTH_TOKEN)

    def test_hostile_text_is_refused_in_every_string_field(self) -> None:  # I8
        for name in STRING_FIELDS:
            base = str(GOLDEN_FIELDS[name])
            for fragment in HOSTILE_FRAGMENTS:
                for value in (base + fragment, fragment + base, base[:3] + fragment + base[3:]):
                    with self.subTest(field=name, value=value), self.assertRaises(SpecError) as caught:
                        make_spec(**{name: value})
                    self.assertEqual(caught.exception.field, name)

    def test_a_leading_dash_is_refused(self) -> None:  # I8: no value can read as a flag
        for name in STRING_FIELDS:
            with self.subTest(field=name), self.assertRaises(SpecError):
                make_spec(**{name: "-" + str(GOLDEN_FIELDS[name])})
        with self.assertRaises(SpecError):
            make_spec(config_path="conf/-x.toml")

    def test_non_strings_are_refused(self) -> None:
        for name in STRING_FIELDS:
            with self.subTest(field=name), self.assertRaises(SpecError):
                make_spec(**{name: 7})

    def test_sha_must_be_full_lowercase_hex(self) -> None:
        sha = str(GOLDEN_FIELDS["nightloop_sha"])
        for value in (sha[:39], sha + "0", sha.upper(), "main", "v1.2.3", "refs/heads/main", "g" * 40):
            with self.subTest(value=value), self.assertRaises(SpecError):
                make_spec(nightloop_sha=value)

    def test_version_must_be_exact(self) -> None:
        for value in ("latest", "2.1", "2.1.x", "^2.1.0", "2.1.330-beta", "02.1.3", ""):
            with self.subTest(value=value), self.assertRaises(SpecError):
                make_spec(claude_version=value)
        self.assertEqual(make_spec(claude_version="0.0.0").claude_version, "0.0.0")

    def test_repo_shape(self) -> None:
        for value in ("agentlab", "a/b/c", "/agentlab", "owner/", "-owner/x", "owner/..x/y"):
            with self.subTest(value=value), self.assertRaises(SpecError):
                make_spec(nightloop_repo=value)
        self.assertEqual(make_spec(nightloop_repo="a-b/c.d_e").nightloop_repo, "a-b/c.d_e")

    def test_timezone_shape(self) -> None:
        for good in ("UTC", "Europe/London", "America/Argentina/Buenos_Aires", "Etc/GMT+5"):
            self.assertEqual(make_spec(timezone=good).timezone, good)
        for bad in ("utc", "Europe", "../etc/passwd", "Europe/", "/London", "EST5EDT x"):
            with self.subTest(value=bad), self.assertRaises(SpecError):
                make_spec(timezone=bad)

    def test_config_path_stays_inside_the_repo(self) -> None:
        for bad in ("", "/etc/n.toml", "../n.toml", "a/../n.toml", "./n.toml", "a//n.toml", "a/", ".."):
            with self.subTest(value=bad), self.assertRaises(SpecError):
                make_spec(config_path=bad)
        self.assertEqual(make_spec(config_path=".github/nightloop.toml").config_path, ".github/nightloop.toml")

    def test_ints(self) -> None:
        bad = {
            "minute": (0, 60, -1, True, "17", 17.0),
            "hour": (-1, 24, True, "1"),
            "timeout_s": (0, -5, True, 1.5),
            "job_timeout_minutes": (0, True, "360"),
        }
        for name, values in bad.items():
            for value in values:
                with self.subTest(field=name, value=value), self.assertRaises(SpecError) as caught:
                    make_spec(**{name: value})
                self.assertEqual(caught.exception.field, name)

    def test_minute_zero_is_refused(self) -> None:  # GitHub names :00 as its high-load minute
        with self.assertRaises(SpecError) as caught:
            make_spec(minute=0)
        self.assertEqual(caught.exception.field, "minute")

    def test_credential_must_be_the_enum(self) -> None:
        with self.assertRaises(SpecError):
            make_spec(credential="CLAUDE_CODE_OAUTH_TOKEN")


class ValidSpecTest(unittest.TestCase):
    def test_cannot_be_built_without_validate_spec(self) -> None:
        with self.assertRaises(TypeError):
            ValidSpec(spec=make_spec(), hour=1, minute=17, cycles=1, phases=3, needed_minutes=1)


class SlotTest(unittest.TestCase):  # I1, criterion 2
    def test_every_hour_in_the_window_becomes_the_cron_hour(self) -> None:
        cfg = plugin_config()
        for hour in range(cfg.window.start_hour, cfg.window.end_hour):
            with self.subTest(hour=hour):
                v = valid(cfg, hour=hour)
                self.assertEqual((v.hour, v.minute), (hour, 17))
                self.assertIn(f'    - cron: "17 {hour} * * *"', render(cfg, hour=hour).split("\n"))

    def test_the_default_hour_is_the_window_start(self) -> None:
        cfg = plugin_config()
        self.assertEqual(valid(cfg).hour, cfg.window.start_hour)

    def test_every_hour_outside_the_window_is_refused(self) -> None:
        cfg = plugin_config()
        for hour in range(24):
            if cfg.window.start_hour <= hour < cfg.window.end_hour:
                continue
            with self.subTest(hour=hour), self.assertRaises(SpecError) as caught:
                validate_spec(cfg, make_spec(hour=hour))
            self.assertEqual(caught.exception.field, "hour")
            self.assertIn("outside the run window", str(caught.exception))

    def test_the_window_end_is_exclusive(self) -> None:
        cfg = plugin_config()
        self.assertEqual(cfg.window, Window(1, 6))
        with self.assertRaises(SpecError):
            validate_spec(cfg, make_spec(hour=6))


if __name__ == "__main__":
    unittest.main()
