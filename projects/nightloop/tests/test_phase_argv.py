"""Milestone 3: ``build_argv`` (A1, A2, invariant 4) and ``PhaseRequest`` validation.

Numbered comments refer to research/2026-11-10-nightloop-phase-runner.md.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from nightloop.model import Phase, Postcondition
from nightloop.phase import PhaseRequest
from nightloop.phase.argv import build_argv

from .phase_support import PHASE_NAMES, example_config, request

CWD = Path("/tmp/nightloop-repo")
FORBIDDEN_FLAGS = ("--bare", "--dangerously-skip-permissions")
VARIADIC_FLAGS = ("--allowedTools", "--disallowedTools", "--tools")


class BuildArgvTest(unittest.TestCase):
    # A1. The research phase's exact argv; the prompt is argv[2].
    def test_research_argv_is_exact(self) -> None:
        argv = build_argv(request("research", CWD, prompt="Research tonight."))
        self.assertEqual(
            argv,
            (
                "claude", "-p", "Research tonight.",
                "--model", "sonnet",
                "--output-format", "json",
                "--permission-mode", "dontAsk",
                "--agent", "agentlab-researcher",
                "--allowedTools",
                "WebSearch", "WebFetch", "Read", "Write", "Edit", "Glob", "Grep", "Bash",
                "mcp__hn-search__search_stories", "mcp__hn-search__get_story",
            ),
        )

    # A2 + invariant 4, for every phase of the example config.
    def test_every_phase_has_the_required_flags_and_none_forbidden(self) -> None:
        for name in PHASE_NAMES:
            with self.subTest(phase=name):
                phase = getattr(example_config(), name)
                argv = build_argv(request(name, CWD))
                for flag in FORBIDDEN_FLAGS:
                    self.assertNotIn(flag, argv)
                pairs = set(zip(argv, argv[1:]))
                self.assertIn(("--permission-mode", "dontAsk"), pairs)
                self.assertIn(("--agent", phase.agent), pairs)
                self.assertIn(("--model", phase.model), pairs)
                self.assertIn(("--output-format", "json"), pairs)

    def test_prompt_precedes_every_variadic_flag(self) -> None:
        for name in PHASE_NAMES:
            with self.subTest(phase=name):
                argv = build_argv(request(name, CWD, prompt="the prompt"))
                self.assertEqual(argv[:3], ("claude", "-p", "the prompt"))
                first_variadic = min(argv.index(f) for f in VARIADIC_FLAGS if f in argv)
                self.assertGreater(first_variadic, 2)

    def test_rules_follow_allowed_tools_one_per_element_and_nothing_else(self) -> None:
        phase = Phase(
            agent="a",
            model="opus",
            postcondition=Postcondition.INCREMENT_BUILT,
            allowed_tools=("Read", "Bash(git log *)"),
        )
        argv = build_argv(PhaseRequest("build", phase, "go", CWD, 5))
        self.assertEqual(argv[-3:], ("--allowedTools", "Read", "Bash(git log *)"))

    def test_a_prompt_that_looks_like_a_flag_stays_at_index_2(self) -> None:
        argv = build_argv(request("build", CWD, prompt="Use --allowedTools wisely"))
        self.assertEqual(argv[2], "Use --allowedTools wisely")


class PhaseRequestTest(unittest.TestCase):
    def test_rejects_bad_values(self) -> None:
        research = example_config().research
        cases = {
            "unknown phase name": dict(name="maintain"),
            "empty prompt": dict(prompt=""),
            "blank prompt": dict(prompt="  \n"),
            "prompt read as an option": dict(prompt="--help"),
            "NUL in prompt": dict(prompt="a\x00b"),
            "relative cwd": dict(cwd=Path("repo")),
            "zero timeout": dict(timeout_s=0),
            "negative timeout": dict(timeout_s=-5),
            "postcondition not allowed for the phase": dict(name="review"),
        }
        for label, override in cases.items():
            with self.subTest(case=label):
                fields = dict(name="research", phase=research, prompt="ok", cwd=CWD, timeout_s=5)
                fields.update(override)
                with self.assertRaises(ValueError):
                    PhaseRequest(**fields)

    def test_rejects_wrong_types(self) -> None:
        research = example_config().research
        cases = {
            "bool timeout": dict(timeout_s=True),
            "float timeout": dict(timeout_s=1.5),
            "str cwd": dict(cwd="/tmp"),
            "phase not a Phase": dict(phase={"agent": "x"}),
            "bytes prompt": dict(prompt=b"go"),
        }
        for label, override in cases.items():
            with self.subTest(case=label):
                fields = dict(name="research", phase=research, prompt="ok", cwd=CWD, timeout_s=5)
                fields.update(override)
                with self.assertRaises(TypeError):
                    PhaseRequest(**fields)


if __name__ == "__main__":
    unittest.main()
