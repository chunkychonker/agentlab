"""Milestone 11, criterion 12 (opt-in, billed): one real review trial each of a seeded and a clean fixture.

Skipped unless ``NIGHTLOOP_LIVE`` is set and ``claude`` is on PATH. It makes
two Opus review calls through ``SubprocessRunner``, exactly as ``nightloop
bench --trials 1`` would on these two fixtures. It asserts only that each
trial was graded: that answers the open question of whether ``claude -p
--agent reviewer`` resolves the temp repo's ``.claude/agents/reviewer.md``
and finds ``run_silent`` on the inherited PATH. Whether the reviewer caught
the bug is the benchmark's result, not a test's.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from nightloop.bench.classify import Outcome
from nightloop.bench.load import load_fixtures
from nightloop.bench.run import run_bench
from nightloop.bench.workspace import TempGitWorkspace
from nightloop.cli.bench_args import PLUGIN_BIN
from nightloop.cli.main import DEFAULT_TIMEOUT_S, VERDICT_PATH
from nightloop.gates.dispatch import evaluate
from nightloop.phase.subprocess_runner import SubprocessRunner

from .bench_support import REVIEWER_TEMPLATE, SHIPPED_FIXTURES, plugin_config

LIVE_ENV = "NIGHTLOOP_LIVE"
LIVE_FIXTURES = ("path-containment-prefix", "slugify-ascii")


@unittest.skipUnless(os.environ.get(LIVE_ENV) and shutil.which("claude"),
                     f"set {LIVE_ENV}=1 with claude on PATH to run (billed: 2 Opus review calls)")
class LiveBenchTest(unittest.TestCase):
    def test_one_seeded_and_one_clean_trial_are_graded(self) -> None:
        fixtures = [f for f in load_fixtures(SHIPPED_FIXTURES) if f.slug in LIVE_FIXTURES]
        self.assertEqual(sorted(f.slug for f in fixtures), sorted(LIVE_FIXTURES))
        path = os.pathsep.join([str(PLUGIN_BIN), os.environ.get("PATH", "")])
        with tempfile.TemporaryDirectory() as parent, mock.patch.dict(os.environ, {"PATH": path}):
            results = run_bench(
                fixtures, plugin_config(),
                runner=SubprocessRunner(),
                workspace=TempGitWorkspace(REVIEWER_TEMPLATE.read_bytes(), "reviewer",
                                           parent=Path(parent).resolve()),
                trials=1, timeout_s=DEFAULT_TIMEOUT_S, verdict_path=VERDICT_PATH,
                now=time.time, gate=evaluate, on_trial=lambda r: print(r),
            )
        for result in results:
            with self.subTest(fixture=result.slug):
                self.assertIsNot(result.outcome, Outcome.UNGRADED, result.detail)


if __name__ == "__main__":
    unittest.main()
