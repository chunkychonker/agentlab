"""Milestone 10, criteria 1-3: the golden file, and I1-I4, I6, I7 as line-structure checks (no YAML parser)."""

from __future__ import annotations

import re
import unittest

from nightloop.schedule_gha import Credential
from nightloop.schedule_gha.render import CHECKOUT, SETUP_NODE, SETUP_PYTHON, UPLOAD_ARTIFACT

from .gha_support import GOLDEN, GOLDEN_FIELDS, block, render, step_named, steps

SHA = str(GOLDEN_FIELDS["nightloop_sha"])
SECRET_NAMES = tuple(c.value for c in Credential)


class GoldenTest(unittest.TestCase):  # criterion 1
    def test_the_plugin_config_renders_the_checked_in_file_byte_for_byte(self) -> None:
        self.assertEqual(render().encode("utf-8"), GOLDEN.read_bytes())

    def test_rendering_twice_gives_identical_text(self) -> None:  # I8 determinism
        self.assertEqual(render(), render())

    def test_one_trailing_newline_and_no_tabs_or_trailing_spaces(self) -> None:
        text = render()
        self.assertTrue(text.endswith("\n") and not text.endswith("\n\n"))
        for line in text.split("\n"):
            self.assertNotIn("\t", line)
            self.assertEqual(line, line.rstrip(), repr(line))


class ScheduleTest(unittest.TestCase):  # I1
    def test_cron_and_timezone(self) -> None:
        text = render(hour=3, minute=41, timezone="Europe/Berlin")
        self.assertEqual(block(text, "  schedule:"), ['    - cron: "41 3 * * *"', '      timezone: "Europe/Berlin"'])

    def test_tz_env_equals_the_cron_timezone(self) -> None:
        for zone in ("UTC", "Asia/Kolkata", "America/Argentina/Buenos_Aires"):
            with self.subTest(zone=zone):
                text = render(timezone=zone)
                self.assertIn(f'      TZ: "{zone}"', block(text, "    env:"))
                self.assertIn(f'      timezone: "{zone}"', block(text, "  schedule:"))

    def test_manual_dispatch_is_available(self) -> None:
        self.assertIn("  workflow_dispatch:", block(render(), "on:"))


class NeverIgnoreWindowTest(unittest.TestCase):  # I2
    def test_the_flag_appears_nowhere(self) -> None:
        for credential in Credential:
            for hour in (1, 5):
                with self.subTest(credential=credential, hour=hour):
                    self.assertNotIn("ignore-window", render(credential=credential, hour=hour))

    def test_the_run_step_runs_nightloop_run_with_the_checked_timeout(self) -> None:
        run = step_named(render(timeout_s=1234), "Run one night")
        self.assertIn(
            "        run: nightloop run --repo target --config target/examples/plugin.nightloop.toml "
            "--metrics nightloop-metrics/metrics.jsonl --timeout-s 1234",
            run,
        )


class OneCredentialTest(unittest.TestCase):  # I3
    def test_exactly_one_credential_from_secrets_on_the_run_step_only(self) -> None:
        for credential in Credential:
            with self.subTest(credential=credential):
                text = render(credential=credential)
                other = next(name for name in SECRET_NAMES if name != credential.value)
                self.assertNotIn(other, text)
                hits = [line for line in text.split("\n") if credential.value in line]
                self.assertEqual(hits, [f"          {credential.value}: ${{{{ secrets.{credential.value} }}}}"])
                self.assertIn(hits[0], step_named(text, "Run one night"))

    def test_no_secret_reaches_the_install_steps(self) -> None:
        text = render()
        for step in steps(text):
            if step[0] != "      - name: Run one night":
                self.assertFalse(any("secrets." in line or "github.token" in line for line in step), step)


class PolicyTest(unittest.TestCase):  # I4
    def test_permissions_are_exactly_contents_and_pull_requests_write(self) -> None:
        self.assertEqual(block(render(), "permissions:"), ["  contents: write", "  pull-requests: write"])
        self.assertEqual(render().count("permissions:"), 1)

    def test_concurrency_never_cancels_a_running_night(self) -> None:
        body = block(render(workflow_stem="night-a"), "concurrency:")
        self.assertEqual(body, ["  group: nightloop-night-a", "  cancel-in-progress: false"])
        self.assertNotIn("cancel-in-progress: true", render())


class PinnedAndDeepTest(unittest.TestCase):  # I6
    def test_target_checkout_has_full_history(self) -> None:
        step = step_named(render(), "Check out the repo with full history")
        self.assertIn("          fetch-depth: 0", step)
        self.assertIn("          path: target", step)

    def test_nightloop_is_checked_out_beside_the_target_at_the_pinned_sha(self) -> None:
        step = step_named(render(), "Check out nightloop at a pinned commit")
        self.assertIn(f"          ref: {SHA}", step)
        self.assertIn("          repository: chunkychonker/agentlab", step)
        self.assertIn("          path: nightloop-src", step)  # a sibling: never inside target/
        refs = [line for line in render().split("\n") if line.strip().startswith("ref:")]
        self.assertEqual(refs, [f"          ref: {SHA}"])

    def test_the_cli_version_is_exact_and_autoupdate_is_off(self) -> None:
        text = render(claude_version="9.8.7")
        self.assertIn("          npm install -g @anthropic-ai/claude-code@9.8.7", step_named(text, "Install Claude Code and nightloop"))
        self.assertIn('      DISABLE_AUTOUPDATER: "1"', block(text, "    env:"))

    def test_every_action_is_pinned_to_a_full_sha(self) -> None:
        uses = [line.strip() for line in render().split("\n") if line.strip().startswith("uses:")]
        self.assertEqual(len(uses), 5)
        for line in uses:
            self.assertRegex(line, r"^uses: [a-z-]+/[a-z-]+@[0-9a-f]{40} # v\d+\.\d+\.\d+$")
        pinned = {f"{a[0]}@{a[1]}" for a in (CHECKOUT, SETUP_PYTHON, SETUP_NODE, UPLOAD_ARTIFACT)}
        self.assertEqual({line.split()[1] for line in uses}, pinned)

    def test_metrics_are_written_outside_the_target_tree(self) -> None:
        run = " ".join(step_named(render(), "Run one night"))
        metrics = re.search(r"--metrics (\S+)", run)
        assert metrics is not None
        self.assertFalse(metrics.group(1).startswith("target/"))


class MetricsSurviveTest(unittest.TestCase):  # I7
    def test_upload_runs_always_and_takes_the_metrics_dir(self) -> None:
        text = render()
        upload = step_named(text, "Upload the metrics")
        self.assertIn("        if: always()", upload)
        self.assertIn("          path: nightloop-metrics/", upload)
        self.assertEqual(steps(text)[-1], upload)  # after the night, so it sees its output


if __name__ == "__main__":
    unittest.main()
