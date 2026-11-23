"""Milestone 10, criterion 9 (opt-in): ``actionlint`` accepts the golden workflow.

Skipped unless ``NIGHTLOOP_LIVE`` is set and ``actionlint`` is on PATH. It
reads a copy of the checked-in file and calls nothing on GitHub. actionlint
1.7.12 knows the schedule ``timezone:`` key; 1.7.7 rejects it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from .gha_support import GOLDEN

LIVE_ENV = "NIGHTLOOP_LIVE"
ACTIONLINT = shutil.which("actionlint")


@unittest.skipUnless(os.environ.get(LIVE_ENV), f"set {LIVE_ENV}=1 to run")
@unittest.skipUnless(ACTIONLINT, "actionlint is not on PATH")
class ActionlintTest(unittest.TestCase):
    def test_actionlint_accepts_the_golden_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workflows = Path(tmp) / ".github" / "workflows"
            workflows.mkdir(parents=True)
            shutil.copyfile(GOLDEN, workflows / "nightloop.yml")
            done = subprocess.run([str(ACTIONLINT), "-no-color", ".github/workflows/nightloop.yml"],
                                  cwd=tmp, capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


if __name__ == "__main__":
    unittest.main()
