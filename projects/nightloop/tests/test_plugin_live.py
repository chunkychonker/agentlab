"""Milestone 8, criterion 6 (opt-in): Claude Code's own validator accepts the plugin.

Skipped unless ``NIGHTLOOP_LIVE`` is set and ``claude`` is on PATH, so the
default run never needs the CLI. ``claude plugin validate`` reads files only;
it makes no model call. The billed check (a ``-p --agent`` run that tries
``git push`` and must be blocked) is a documented human step, not a test.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import unittest

from .plugin_support import PLUGIN

LIVE_ENV = "NIGHTLOOP_LIVE"
VALIDATE_TIMEOUT_S = 120


@unittest.skipUnless(os.environ.get(LIVE_ENV) and shutil.which("claude"),
                     f"set {LIVE_ENV}=1 with claude on PATH to run")
class ClaudeValidateTest(unittest.TestCase):
    def test_strict_validate_passes(self) -> None:
        done = subprocess.run(
            ["claude", "plugin", "validate", str(PLUGIN), "--strict"],
            capture_output=True, text=True, timeout=VALIDATE_TIMEOUT_S,
        )
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        for agent in ("nightloop:researcher", "nightloop:builder", "nightloop:reviewer"):
            self.assertIn(agent, done.stdout)


if __name__ == "__main__":
    unittest.main()
