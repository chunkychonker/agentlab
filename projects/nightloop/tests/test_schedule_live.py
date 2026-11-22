"""Milestone 9, criterion 10 (opt-in): the real ``plutil`` accepts the plist; the real ``launchctl`` reads state.

Skipped unless ``NIGHTLOOP_LIVE`` is set on macOS. Read-only against
launchd: ``plutil -lint`` on a temp file and ``launchctl print`` for a label
that is never installed. No ``bootstrap``, ``bootout`` or ``enable``.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath

from nightloop.schedule import JobState, SubprocessLaunchctl, domain_for, nightly_job, render_plist

LIVE_ENV = "NIGHTLOOP_LIVE"
NEVER_INSTALLED = "dev.nightloop.live-test.never-installed"


@unittest.skipUnless(os.environ.get(LIVE_ENV) and sys.platform == "darwin",
                     f"set {LIVE_ENV}=1 on macOS to run")
class LiveLaunchdTest(unittest.TestCase):
    def test_plutil_accepts_a_real_job_with_awkward_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = PurePosixPath(tmp) / "a b & <c> 'd'"
            job = nightly_job(label=NEVER_INSTALLED, command=(sys.executable, "-m", "nightloop"),
                              repo=repo, config_path=repo / "nightloop.toml",
                              path_env=os.environ.get("PATH", "/usr/bin:/bin"), hour=2, minute=0)
            path = Path(tmp) / f"{NEVER_INSTALLED}.plist"
            path.write_bytes(render_plist(job))
            SubprocessLaunchctl().lint(PurePosixPath(path))  # raises on a lint failure

    def test_a_never_installed_label_reads_not_loaded(self) -> None:
        state = SubprocessLaunchctl().state(domain_for(os.getuid()), NEVER_INSTALLED)
        self.assertIs(state, JobState.NOT_LOADED)


if __name__ == "__main__":
    unittest.main()
