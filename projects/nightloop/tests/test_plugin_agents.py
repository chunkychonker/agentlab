"""Milestone 8, criterion 5: the agent templates agree with the config and with the verdict gate.

The plugin config (``examples/plugin.nightloop.toml``) names each phase's
agent, model and tools; the matching template's frontmatter must say the
same, so the two cannot drift. The reviewer's sample verdict file must pass
the real verdict gate and the shipper's review-record reader.
"""

from __future__ import annotations

import re
import unittest

from nightloop.config import load_config
from nightloop.gates import Verdict, parse_verdict
from nightloop.plugin_check.frontmatter import parse_frontmatter
from nightloop.plugin_check.rules import IGNORED_AGENT_FIELDS
from nightloop.ship.review_section import review_section

from .plugin_support import AGENTS, PLUGIN_CONFIG, load_guard

PHASES = ("research", "build", "review")
TEST_RUNNERS = ("build", "review")
_VERDICT_BLOCK = re.compile(r"^```verdict\n(.*?)^```$", re.MULTILINE | re.DOTALL)
_INLINE_FAIL = re.compile(r"`(VERDICT: FAIL)`")
# The indented (code block) example command line in a body, e.g.
# "       run_silent tests -- python3 ..." under a numbered list item.
_RUN_SILENT_EXAMPLE = re.compile(r"^ {4,}(run_silent \S+ -- .+)$", re.MULTILINE)


def template(phase: str) -> tuple[dict[str, str], str]:
    """(frontmatter, body) of the agent template the plugin config names for ``phase``."""
    agent = getattr(load_config(PLUGIN_CONFIG), phase).agent
    text = (AGENTS / f"{agent}.md").read_text(encoding="utf-8")
    fields = parse_frontmatter(text)
    body = text.split("\n---\n", 1)[1]
    return fields, body


def tools(value: str) -> tuple[str, ...]:
    return tuple(t.strip() for t in value.split(","))


class TemplateMatchesConfigTest(unittest.TestCase):
    def test_the_plugin_has_exactly_the_three_phase_agents(self) -> None:
        cfg = load_config(PLUGIN_CONFIG)
        self.assertEqual(
            sorted(p.name for p in AGENTS.glob("*.md")),
            sorted(f"{getattr(cfg, phase).agent}.md" for phase in PHASES),
        )

    def test_name_model_and_tools_equal_the_config(self) -> None:
        cfg = load_config(PLUGIN_CONFIG)
        for phase in PHASES:
            with self.subTest(phase=phase):
                fields, _ = template(phase)
                expected = getattr(cfg, phase)
                self.assertEqual(fields["name"], expected.agent)
                self.assertEqual(fields["model"], expected.model)
                self.assertEqual(tools(fields["tools"]), expected.allowed_tools)

    def test_no_template_sets_a_field_plugin_agents_ignore(self) -> None:
        for phase in PHASES:
            with self.subTest(phase=phase):
                fields, _ = template(phase)
                self.assertFalse(set(fields) & IGNORED_AGENT_FIELDS, set(fields) & IGNORED_AGENT_FIELDS)


class VerdictContractTest(unittest.TestCase):
    def test_the_reviewers_sample_verdict_passes_the_gate(self) -> None:
        _, body = template("review")
        blocks = _VERDICT_BLOCK.findall(body)
        self.assertEqual(len(blocks), 1)
        self.assertIs(parse_verdict(blocks[0]), Verdict.PASS)

    def test_the_reviewers_fail_form_parses_as_fail(self) -> None:
        _, body = template("review")
        self.assertEqual(_INLINE_FAIL.findall(body), ["VERDICT: FAIL"])
        self.assertIs(parse_verdict(_INLINE_FAIL.findall(body)[0]), Verdict.FAIL)

    def test_the_sample_verdict_makes_a_review_record(self) -> None:
        _, body = template("review")
        section = review_section(_VERDICT_BLOCK.findall(body)[0])
        self.assertIn("**Verdict:** PASS", section)
        self.assertIn("run_silent tests", section)
        self.assertNotIn("(none listed)", section)

    def test_the_template_body_itself_does_not_read_as_a_verdict(self) -> None:
        # Only the fenced sample has a column-0 VERDICT line; prose never does.
        for phase in PHASES:
            with self.subTest(phase=phase):
                _, body = template(phase)
                outside = _VERDICT_BLOCK.sub("", body)
                self.assertFalse([l for l in outside.split("\n") if l.startswith("VERDICT:")])
        for phase in ("research", "build"):
            with self.subTest(phase=phase):
                self.assertEqual(_VERDICT_BLOCK.findall(template(phase)[1]), [])


class RunSilentInstructionTest(unittest.TestCase):
    def test_test_running_phases_show_a_run_silent_command_the_guard_allows(self) -> None:
        guard = load_guard()
        for phase in TEST_RUNNERS:
            with self.subTest(phase=phase):
                _, body = template(phase)
                examples = _RUN_SILENT_EXAMPLE.findall(body)
                self.assertTrue(examples, f"{phase} template shows no run_silent command")
                for command in examples:
                    payload = {"tool_name": "Bash", "tool_input": {"command": command}}
                    self.assertEqual(guard.decide(payload), (True, ""))


if __name__ == "__main__":
    unittest.main()
