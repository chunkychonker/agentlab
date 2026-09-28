"""Offline tests for experiment.check_experiment_design. No key, no network, stdlib only.

The shipped fixture must pass, and every way the canary could leak must be
rejected. Otherwise a LOADED verdict from the live run proves nothing.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from experiment import CANARY, PROMPT, SKILL_DIR_NAME, SKILL_MD_FILENAME, ExperimentDesignError, check_experiment_design
from frontmatter import SkillMd, parse_skill_md

SKILL_MD = Path(__file__).resolve().parent / "skills" / SKILL_DIR_NAME / SKILL_MD_FILENAME


class ShippedDesign(unittest.TestCase):
    def test_shipped_skill_and_prompt_are_sound(self) -> None:
        check_experiment_design(parse_skill_md(SKILL_MD.read_text(encoding="utf-8")), PROMPT, CANARY)


class LeaksRejected(unittest.TestCase):
    def assertLeak(self, skill: SkillMd, prompt: str, fragment: str) -> None:
        with self.assertRaises(ExperimentDesignError) as ctx:
            check_experiment_design(skill, prompt, CANARY)
        self.assertIn(fragment, str(ctx.exception))

    def test_canary_missing_from_body(self) -> None:
        self.assertLeak(SkillMd("s", "d", "no codeword"), PROMPT, "not in the SKILL.md body")

    def test_canary_in_description(self) -> None:
        self.assertLeak(SkillMd("s", f"Say {CANARY}", CANARY), PROMPT, "leaks into the description")

    def test_canary_in_name(self) -> None:
        # A valid name is lowercase, so use a lowercase canary to reach the name check.
        with self.assertRaises(ExperimentDesignError) as ctx:
            check_experiment_design(SkillMd("otter-7", "d", "otter-7"), PROMPT, "otter-7")
        self.assertIn("leaks into the name", str(ctx.exception))

    def test_canary_in_prompt(self) -> None:
        self.assertLeak(SkillMd("s", "d", CANARY), f"Reply with {CANARY}", "leaks into the prompt")

    def test_empty_canary(self) -> None:
        with self.assertRaises(ExperimentDesignError):
            check_experiment_design(SkillMd("s", "d", "x"), PROMPT, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
