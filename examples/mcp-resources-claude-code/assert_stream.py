"""Pure verification of a `claude --output-format stream-json --verbose`
transcript against the six acceptance criteria from
research/2026-09-05-mcp-resources-claude-code.md:

  1. The `system`/`init` event's `mcp_servers` lists SERVER_NAME as connected.
  2. Some `assistant` event contains a `tool_use` block whose name matches one
     of LIST_TOOL_NAMES (the host's resource-*listing* tool).
  3. That tool's result is not an error and its text contains RESOURCE_URI --
     i.e. `resources/list` ran and the static resource reached the model.
  4. Some `assistant` event contains a `tool_use` block whose name matches one
     of READ_TOOL_NAMES *and* whose input references RESOURCE_URI.
  5. That tool's result is not an error and its text contains EXPECTED_MARKER
     -- i.e. `resources/read` actually ran and the handler's output came back.
     This is the mechanical test of the 2025-10 "Claude Code lists resources
     but never reads them" claim: the marker can only appear if the read
     happened.
  6. The final `result` event has `is_error: false`.

`check()` is a pure function of its input event list -- no I/O, no network,
no clock, no env. `main()` is the only impure part: it reads stdin and prints
one line before exiting 0 (PASS) or 1 (FAIL). That split is what lets
`test_assert_stream.py` exercise every failure mode offline, with no key and
no billed `claude` call.

Failure modes are returned as `Result(passed=False, reason=...)`, never
raised, except when the transcript itself is not well-formed JSON per line or
a top-level event is not a dict -- a corrupted/truncated capture is a caller
bug, not one of the six acceptance checks, so it fails loudly via
`json.JSONDecodeError` / `ValueError` rather than being folded into a False
verdict.
"""

import json
import sys
from dataclasses import dataclass
from typing import Any

SERVER_NAME = "notes"
RESOURCE_URI = "notes://index"

# The title of the note seeded in examples/mcp-resources-vs-tools/server.py
# (`Note(id="1", title="Welcome", ...)`). It is returned only by the body of
# `list_notes_index()`, which per that server's own offline test runs on
# `resources/read` and never on `resources/list` -- so this string appearing
# in a read tool's result is positive proof the read RPC executed. Coupled to
# server.py by value; README.md states the coupling.
EXPECTED_MARKER = "Welcome"

# The on-wire `name` of Claude Code's built-in resource tools is not published
# in any Anthropic doc (the research note could only confirm the spelling from
# a GitHub issue title and two source analyses of the bundle). Both tuples are
# therefore ordered candidate lists, and `check()`'s success reason reports
# which spelling actually matched, so a transcript pins reality rather than a
# guess. The first entry of each is the spelling observed live on `claude`
# 2.1.252 during this example's build (see fixtures/read_transcript.jsonl and
# README.md); the `mcp__`-prefixed alternates are the plausible-synthesis
# spellings the research note flagged as the competing hypothesis, kept so an
# older or newer host that names them that way is matched rather than silently
# reported as "never called".
LIST_TOOL_NAMES: tuple[str, ...] = ("ListMcpResourcesTool", "mcp__list_mcp_resources")
READ_TOOL_NAMES: tuple[str, ...] = ("ReadMcpResourceTool", "mcp__read_mcp_resource")


@dataclass(frozen=True)
class Result:
    passed: bool
    reason: str


def _find_init_event(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for event in events:
        if event.get("type") == "system" and event.get("subtype") == "init":
            return event
    return None


def _server_connected(events: list[dict[str, Any]]) -> Result | None:
    """Returns a failing Result, or None if this check passed.

    Failure modes: no `system`/`init` event at all; SERVER_NAME absent from
    that event's `mcp_servers`; SERVER_NAME present with a status other than
    `connected`.
    """
    init_event = _find_init_event(events)
    if init_event is None:
        return Result(False, "no system/init event found in transcript")

    servers = {s.get("name"): s.get("status") for s in init_event.get("mcp_servers", [])}
    if SERVER_NAME not in servers:
        return Result(
            False,
            f"server {SERVER_NAME!r} missing from init event's mcp_servers: {servers}",
        )
    status = servers[SERVER_NAME]
    if status != "connected":
        return Result(False, f"server {SERVER_NAME!r} status is {status!r}, not 'connected'")
    return None


def _iter_tool_uses(events: list[dict[str, Any]], names: tuple[str, ...]):
    """Yields every `tool_use` content block on an `assistant` event whose
    `name` is one of `names`, in transcript order."""
    for event in events:
        if event.get("type") != "assistant":
            continue
        for block in event.get("message", {}).get("content", []):
            if block.get("type") == "tool_use" and block.get("name") in names:
                yield block


def _find_tool_use(
    events: list[dict[str, Any]], names: tuple[str, ...]
) -> dict[str, Any] | None:
    """The first `tool_use` block matching any spelling in `names`, or None."""
    return next(_iter_tool_uses(events, names), None)


def _input_references_uri(tool_use: dict[str, Any], uri: str) -> bool:
    """True if any string value in the tool_use's `input` contains `uri`.

    Schema-agnostic on purpose: the input shape of the host's read tool is
    undocumented (`{uri}` vs `{server, uri}` vs something else), so this
    matches on the URI appearing as a value anywhere in the flat input dict
    rather than asserting a key name that would break on the next host
    release.
    """
    for value in tool_use.get("input", {}).values():
        if isinstance(value, str) and uri in value:
            return True
    return False


def _find_tool_use_for_uri(
    events: list[dict[str, Any]], names: tuple[str, ...], uri: str
) -> dict[str, Any] | None:
    """The first `tool_use` block matching `names` whose input references
    `uri`, or None. Skips matching calls aimed at some other resource so a
    read of an unrelated URI cannot satisfy this check."""
    for tool_use in _iter_tool_uses(events, names):
        if _input_references_uri(tool_use, uri):
            return tool_use
    return None


def _find_tool_result_event(
    events: list[dict[str, Any]], tool_use_id: str
) -> dict[str, Any] | None:
    """The `user` event carrying the `tool_result` for `tool_use_id`, or None."""
    for event in events:
        if event.get("type") != "user":
            continue
        for block in event.get("message", {}).get("content", []):
            if block.get("type") == "tool_result" and block.get("tool_use_id") == tool_use_id:
                return event
    return None


def _tool_result_is_error(result_event: dict[str, Any], tool_use_id: str) -> bool:
    """True if the tool result is flagged as an error in any of the places an
    error flag has been observed or documented: the Claude-Code-specific
    `tool_use_result` decoration on the `user` event, or the raw
    `tool_result` content block (per the Anthropic tool_result schema,
    `is_error`). Both `is_error` and `isError` spellings are checked
    defensively, as in examples/mcp-connect-claude-code/assert_stream.py.
    """
    tool_use_result = result_event.get("tool_use_result")
    if isinstance(tool_use_result, dict) and (
        tool_use_result.get("is_error") or tool_use_result.get("isError")
    ):
        return True
    for block in result_event.get("message", {}).get("content", []):
        if block.get("type") == "tool_result" and block.get("tool_use_id") == tool_use_id:
            if block.get("is_error") or block.get("isError"):
                return True
    return False


def _block_text(content: Any) -> list[str]:
    """Every string carried by a `tool_result` block's `content`, which the
    host writes either as a bare string or as a list of `{type, text}` parts."""
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        return [part["text"] for part in content if isinstance(part, dict) and isinstance(part.get("text"), str)]
    return []


def _tool_result_text(result_event: dict[str, Any], tool_use_id: str) -> str:
    """All text the host attached to this tool result, joined, for substring
    checks.

    Reads both the raw `tool_result` content block and the Claude-Code
    `tool_use_result` decoration, because the two do not always carry the
    same payload (the sibling example found the decoration held structured
    data the content block did not). An empty string means the result
    genuinely carried no text -- a real observation the caller reports as a
    failed substring check, not a swallowed error; the "no result at all"
    case is already caught upstream by `_find_tool_result_event`.
    """
    texts: list[str] = []
    for block in result_event.get("message", {}).get("content", []):
        if block.get("type") == "tool_result" and block.get("tool_use_id") == tool_use_id:
            texts.extend(_block_text(block.get("content")))

    tool_use_result = result_event.get("tool_use_result")
    if isinstance(tool_use_result, dict):
        texts.extend(_block_text(tool_use_result.get("content")))
    elif isinstance(tool_use_result, (str, list)):
        texts.extend(_block_text(tool_use_result))

    return "\n".join(texts)


def _find_final_result(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for event in reversed(events):
        if event.get("type") == "result":
            return event
    return None


def _check_tool_round_trip(
    events: list[dict[str, Any]], tool_use: dict[str, Any], expected_substring: str, label: str
) -> Result | None:
    """Shared criterion 3/5 body: the result for `tool_use` exists, is not an
    error, and its text contains `expected_substring`. Returns a failing
    Result, or None if it passed."""
    tool_use_id = tool_use.get("id")
    result_event = _find_tool_result_event(events, tool_use_id)
    if result_event is None:
        return Result(False, f"no tool_result found for {label} tool_use id {tool_use_id!r}")
    if _tool_result_is_error(result_event, tool_use_id):
        return Result(
            False,
            f"{label} tool_result for {tool_use_id!r} reported an error: "
            f"{_tool_result_text(result_event, tool_use_id)!r}",
        )
    text = _tool_result_text(result_event, tool_use_id)
    if expected_substring not in text:
        return Result(
            False,
            f"{label} tool_result for {tool_use_id!r} does not contain "
            f"{expected_substring!r}; got {text!r}",
        )
    return None


def check(events: list[dict[str, Any]]) -> Result:
    """Applies the six acceptance criteria in order, short-circuiting on the
    first failure so the reported reason always names the true first cause
    rather than a downstream symptom.

    Order: server connected -> list tool called -> list result surfaces
    RESOURCE_URI -> read tool called for that URI -> read result is not an
    error and contains EXPECTED_MARKER -> final result `is_error` is False.

    Never raises: every acceptance failure is a `Result(False, reason)`. A
    malformed transcript raises earlier, in `_parse_events`. On success,
    `reason` names the list- and read-tool spellings that matched, so the
    transcript -- not this file's guess -- is what pins the host's on-wire
    tool names.
    """
    server_check = _server_connected(events)
    if server_check is not None:
        return server_check

    list_tool_use = _find_tool_use(events, LIST_TOOL_NAMES)
    if list_tool_use is None:
        return Result(
            False,
            "model never called a resource-listing tool "
            f"(tried {list(LIST_TOOL_NAMES)}) -- server connected but resources "
            "were never enumerated",
        )

    list_failure = _check_tool_round_trip(events, list_tool_use, RESOURCE_URI, "list")
    if list_failure is not None:
        return list_failure

    read_tool_use = _find_tool_use_for_uri(events, READ_TOOL_NAMES, RESOURCE_URI)
    if read_tool_use is None:
        return Result(
            False,
            f"model never called a resource-reading tool for {RESOURCE_URI!r} "
            f"(tried {list(READ_TOOL_NAMES)}) -- discovery happened but no read "
            "was attempted",
        )

    read_failure = _check_tool_round_trip(events, read_tool_use, EXPECTED_MARKER, "read")
    if read_failure is not None:
        return read_failure

    final_result = _find_final_result(events)
    if final_result is None:
        return Result(False, "no final 'result' event found in transcript")
    if final_result.get("is_error") is not False:
        return Result(
            False,
            f"final result event has is_error={final_result.get('is_error')!r}, expected False",
        )

    return Result(
        True,
        f"{SERVER_NAME!r} connected; {list_tool_use['name']} surfaced {RESOURCE_URI}; "
        f"{read_tool_use['name']} read it and returned {EXPECTED_MARKER!r} "
        f"(input keys: {sorted(read_tool_use.get('input', {}))}); final result success",
    )


def _parse_events(lines: list[str]) -> list[dict[str, Any]]:
    """Parses one JSON event per non-blank line.

    Failure modes, both raised rather than returned: a line that is not valid
    JSON (`json.JSONDecodeError`) and a line whose top-level value is not an
    object (`ValueError`). A truncated or corrupted transcript is a bug in
    whatever produced it, not an acceptance-criteria failure, so it must not
    be silently skipped or folded into a False verdict.
    """
    events: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        event = json.loads(line)
        if not isinstance(event, dict):
            raise ValueError(f"transcript line {index + 1} is not a JSON object: {event!r}")
        events.append(event)
    return events


def main(argv: list[str]) -> int:
    """Reads the transcript from stdin, prints one PASS/FAIL line, returns
    0 (pass), 1 (fail), or 2 (wrong usage). Raises on a malformed transcript.
    """
    if len(argv) != 1:
        print("usage: assert_stream.py   (transcript JSONL on stdin)", file=sys.stderr)
        return 2

    events = _parse_events(sys.stdin.readlines())
    result = check(events)

    print(f"{'PASS' if result.passed else 'FAIL'}: {result.reason}")
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
