"""Offline tests for load_evidence.load_verdict / skill_was_loaded. No key, no network, stdlib only.

The three fixtures in fixtures/ are SYNTHETIC. They are hand-built from the
anthropic SDK 1.8.0 block types, not recorded, because this build had no API
key. The mutation cases below each change one thing in `loaded.json` so every
verdict and every loud-failure path gets exercised.
"""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from experiment import CANARY, SKILL_MD_FILENAME
from load_evidence import TranscriptError, Verdict, load_verdict, skill_was_loaded

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _verdict(response: dict) -> Verdict:
    return load_verdict(response, SKILL_MD_FILENAME, CANARY)


class FixtureVerdicts(unittest.TestCase):
    def test_loaded(self) -> None:
        response = _load("loaded.json")
        self.assertIs(_verdict(response), Verdict.LOADED)
        self.assertTrue(skill_was_loaded(response, SKILL_MD_FILENAME, CANARY))

    def test_answer_without_any_code_execution_is_not_loaded(self) -> None:
        response = _load("no_tool_use.json")
        self.assertIs(_verdict(response), Verdict.NO_SKILL_READ)
        self.assertFalse(skill_was_loaded(response, SKILL_MD_FILENAME, CANARY))

    def test_tool_use_alone_is_not_enough(self) -> None:
        response = _load("tool_use_no_canary.json")
        self.assertIs(_verdict(response), Verdict.CANARY_NOT_IN_REPLY)
        self.assertFalse(skill_was_loaded(response, SKILL_MD_FILENAME, CANARY))


class OneFieldMutations(unittest.TestCase):
    """Each case changes one thing in loaded.json; indices: 0 text, 1 use, 2 result, 3 text."""

    def setUp(self) -> None:
        self.r = copy.deepcopy(_load("loaded.json"))

    def test_canary_only_before_the_read_is_not_loaded(self) -> None:
        self.r["content"][0]["text"] = CANARY
        self.r["content"][3]["text"] = "Hello Ada!"
        self.assertIs(_verdict(self.r), Verdict.CANARY_NOT_IN_REPLY)

    def test_nonzero_return_code_is_not_a_read(self) -> None:
        self.r["content"][2]["content"]["return_code"] = 1
        self.assertIs(_verdict(self.r), Verdict.NO_SKILL_READ)

    def test_error_result_is_not_a_read(self) -> None:
        self.r["content"][2]["content"] = {"type": "bash_code_execution_tool_result_error", "error_code": "unavailable"}
        self.assertIs(_verdict(self.r), Verdict.NO_SKILL_READ)

    def test_command_not_naming_skill_md_is_not_a_read(self) -> None:
        self.r["content"][1]["input"] = {"command": "cat /tmp/notes.txt"}
        self.assertIs(_verdict(self.r), Verdict.NO_SKILL_READ)

    def test_read_output_without_canary_is_not_a_read(self) -> None:
        self.r["content"][2]["content"]["stdout"] = "---\nname: hello-skill\n---\n(truncated)"
        self.assertIs(_verdict(self.r), Verdict.NO_SKILL_READ)

    def test_text_editor_view_counts_as_a_read(self) -> None:
        self.r["content"][1] = {
            "type": "server_tool_use", "id": "srvtoolu_01", "name": "text_editor_code_execution",
            "input": {"command": "view", "path": "/skills/hello-skill/SKILL.md"},
        }
        self.r["content"][2] = {
            "type": "text_editor_code_execution_tool_result", "tool_use_id": "srvtoolu_01",
            "content": {"type": "text_editor_code_execution_view_result", "file_type": "text",
                        "content": f"...{CANARY}..."},
        }
        self.assertIs(_verdict(self.r), Verdict.LOADED)


class MalformedIsLoud(unittest.TestCase):
    def setUp(self) -> None:
        self.r = copy.deepcopy(_load("loaded.json"))

    def assertMalformed(self, fragment: str) -> None:
        with self.assertRaises(TranscriptError) as ctx:
            _verdict(self.r)
        self.assertIn(fragment, str(ctx.exception))

    def test_missing_content(self) -> None:
        del self.r["content"]
        self.assertMalformed("no 'content' list")

    def test_block_without_type(self) -> None:
        self.r["content"][0] = {"text": "hi"}
        self.assertMalformed("content[0] is not a block")

    def test_orphan_result(self) -> None:
        self.r["content"][2]["tool_use_id"] = "srvtoolu_unknown"
        self.assertMalformed("no earlier server_tool_use")

    def test_result_content_not_a_dict(self) -> None:
        self.r["content"][2]["content"] = "oops"
        self.assertMalformed("content[2].content is not a dict")


if __name__ == "__main__":
    unittest.main(verbosity=2)
