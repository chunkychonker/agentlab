"""Decides from a Messages API response whether Claude read the Skill body in the container.

Pure: takes the response as a JSON-shaped dict (`message.model_dump(mode="json")`)
and returns a verdict. No network, no filesystem.

Evidence required for `Verdict.LOADED`, all from the transcript:

1. A `server_tool_use` block (`bash_code_execution` or
   `text_editor_code_execution`) whose input mentions the SKILL.md filename,
2. paired by `tool_use_id` with a *successful* result block whose output
   contains the canary (bash: `return_code == 0` and canary in `stdout`;
   text editor: a `text_editor_code_execution_view_result` whose `content`
   contains the canary), and
3. a `text` block positioned *after* that result that also contains the canary.

(2) proves the canary came out of the container. (3) proves the reply used
what was read. Ordering matters: a canary in text *before* the read cannot
have been caused by it.

Known strictness: if Claude reads SKILL.md in pieces (e.g. `head` then
`tail`) and the canary line lands only in a read whose command does not name
SKILL.md, this reports NO_SKILL_READ. That's a false negative, never a false
positive, and the saved transcript shows what actually happened.
"""

from __future__ import annotations

from enum import Enum

_READ_TOOL_NAMES = frozenset({"bash_code_execution", "text_editor_code_execution"})
_BASH_RESULT_BLOCK = "bash_code_execution_tool_result"
_EDITOR_RESULT_BLOCK = "text_editor_code_execution_tool_result"
_BASH_SUCCESS = "bash_code_execution_result"
_EDITOR_VIEW_SUCCESS = "text_editor_code_execution_view_result"


class Verdict(Enum):
    LOADED = "loaded"
    NO_SKILL_READ = "no successful SKILL.md read whose output contains the canary"
    CANARY_NOT_IN_REPLY = "SKILL.md was read, but no later text block contains the canary"


class TranscriptError(ValueError):
    """The response dict is not shaped like a Messages API response."""


def load_verdict(response: dict, skill_md_filename: str, canary: str) -> Verdict:
    """Classify whether the Skill body was read and used. See the module docstring.

    Raises:
        TranscriptError: `content` is missing or not a list; a block is not a
            dict with a string `type`; a code-execution result block has no
            string `tool_use_id`, or references an id with no earlier
            `server_tool_use` block; or its `content` is not a dict with a
            string `type`. Malformed input is never read as "not loaded".
    """
    content = response.get("content")
    if not isinstance(content, list):
        raise TranscriptError("response has no 'content' list")

    tool_inputs: dict[str, object] = {}
    read_index: int | None = None
    for index, block in enumerate(content):
        if not isinstance(block, dict) or not isinstance(block.get("type"), str):
            raise TranscriptError(f"content[{index}] is not a block with a string 'type'")
        kind = block["type"]
        if kind == "server_tool_use" and block.get("name") in _READ_TOOL_NAMES:
            tool_inputs[_require_str(block, "id", index)] = block.get("input")
        elif kind in (_BASH_RESULT_BLOCK, _EDITOR_RESULT_BLOCK):
            tool_use_id = _require_str(block, "tool_use_id", index)
            if tool_use_id not in tool_inputs:
                raise TranscriptError(
                    f"content[{index}] references tool_use_id {tool_use_id!r} with no earlier server_tool_use"
                )
            names_file = skill_md_filename in _flatten_strings(tool_inputs[tool_use_id])
            output = _successful_output(block.get("content"), index)
            if read_index is None and names_file and output is not None and canary in output:
                read_index = index

    if read_index is None:
        return Verdict.NO_SKILL_READ
    for block in content[read_index + 1 :]:
        if block["type"] == "text" and canary in str(block.get("text", "")):
            return Verdict.LOADED
    return Verdict.CANARY_NOT_IN_REPLY


def skill_was_loaded(response: dict, skill_md_filename: str, canary: str) -> bool:
    """True iff `load_verdict` returns `Verdict.LOADED`. Raises as `load_verdict` does."""
    return load_verdict(response, skill_md_filename, canary) is Verdict.LOADED


def _require_str(block: dict, key: str, index: int) -> str:
    value = block.get(key)
    if not isinstance(value, str):
        raise TranscriptError(f"content[{index}] has no string '{key}'")
    return value


def _successful_output(result: object, index: int) -> str | None:
    """Return the readable output of a successful result, or None for an error/non-read result."""
    if not isinstance(result, dict) or not isinstance(result.get("type"), str):
        raise TranscriptError(f"content[{index}].content is not a dict with a string 'type'")
    if result["type"] == _BASH_SUCCESS and result.get("return_code") == 0:
        return str(result.get("stdout", ""))
    if result["type"] == _EDITOR_VIEW_SUCCESS:
        return str(result.get("content", ""))
    return None


def _flatten_strings(value: object) -> str:
    """Join every string found anywhere in a nested dict/list tool input."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "\n".join(_flatten_strings(v) for v in value.values())
    if isinstance(value, list):
        return "\n".join(_flatten_strings(v) for v in value)
    return ""
