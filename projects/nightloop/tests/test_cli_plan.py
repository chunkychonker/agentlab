"""Milestone 7, D1-D2 (pure half): ``plan_night`` and ``render_plan``.

The ``main``-level halves (no runner built, nothing written, run and plan
argv equal) are in ``test_cli_main.py``.
"""

from __future__ import annotations

import shlex
import unittest
from dataclasses import replace
from pathlib import Path

from nightloop.cli.plan import plan_night, render_plan
from nightloop.cli.window import Refuse, Run
from nightloop.cycle.cycle import PHASE_ORDER
from nightloop.cycle.prompts import phase_prompt
from nightloop.phase.argv import build_argv
from nightloop.phase.request import PhaseRequest

from .phase_support import example_config

REPO = Path("/srv/checkout")
VERDICT = Path("logs/last-review.md")


class PlanTest(unittest.TestCase):
    def test_one_entry_per_phase_per_cycle_in_run_order(self) -> None:
        cfg = replace(example_config(), cycles=2)
        plan = plan_night(cfg, repo=REPO, verdict_path=VERDICT, timeout_s=900)
        self.assertEqual([(p.cycle, p.name) for p in plan],
                         [(c, n) for c in (1, 2) for n in PHASE_ORDER])

    def test_each_entry_carries_the_configured_values_and_build_argv(self) -> None:
        cfg = example_config()
        for step in plan_night(cfg, repo=REPO, verdict_path=VERDICT, timeout_s=900):
            phase = getattr(cfg, step.name)
            with self.subTest(phase=step.name):
                self.assertEqual(step.model, phase.model)
                self.assertEqual(step.gate, phase.postcondition.value)
                self.assertEqual((step.timeout_s, step.cwd), (900, REPO))
                expected = build_argv(PhaseRequest(
                    name=step.name, phase=phase,
                    prompt=phase_prompt(step.name, repo=REPO, verdict_path=VERDICT),
                    cwd=REPO, timeout_s=900,
                ))
                self.assertEqual(step.argv, expected)

    def test_bad_inputs_fail_as_run_night_would(self) -> None:
        cfg = example_config()
        with self.assertRaises(ValueError):
            plan_night(cfg, repo=Path("relative"), verdict_path=VERDICT, timeout_s=900)
        with self.assertRaises(ValueError):
            plan_night(cfg, repo=REPO, verdict_path=VERDICT, timeout_s=0)
        with self.assertRaises(TypeError):
            plan_night(cfg, repo=REPO, verdict_path="logs/last-review.md", timeout_s=900)

    def test_the_rendered_plan_shows_every_field_and_says_what_it_would_do(self) -> None:
        plan = plan_night(example_config(), repo=REPO, verdict_path=VERDICT, timeout_s=900)
        text = render_plan(plan, Run("hour 02 is inside the run window"))
        self.assertTrue(text.startswith("dry run: would run: hour 02"))
        for step in plan:
            with self.subTest(phase=step.name):
                self.assertIn(f"cycle 1 / {step.name}", text)
                self.assertIn(f"model:   {step.model}", text)
                self.assertIn(f"gate:    {step.gate}", text)
                self.assertIn("timeout: 900s", text)
                self.assertIn(f"cwd:     {REPO}", text)
                self.assertIn(f"argv:    {shlex.join(step.argv)}", text)
        refused = render_plan(plan, Refuse("hour 14 is outside"))
        self.assertTrue(refused.startswith("dry run: would refuse: hour 14 is outside"))

    def test_the_rendered_argv_splits_back_to_the_argv(self) -> None:
        plan = plan_night(example_config(), repo=REPO, verdict_path=VERDICT, timeout_s=900)
        argv_lines = [l.split("argv:    ", 1)[1] for l in render_plan(plan, Run("x")).splitlines()
                      if "argv:    " in l]
        self.assertEqual([tuple(shlex.split(l)) for l in argv_lines], [p.argv for p in plan])


if __name__ == "__main__":
    unittest.main()
