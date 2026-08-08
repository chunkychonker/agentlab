"""Pure verification of a `claude --output-format stream-json --verbose`
transcript against the four acceptance criteria from
research/2026-08-08-mcp-connect-claude-code.md:

  1. The `system`/`init` event's `mcp_servers` lists SERVER_NAME as connected.
  2. At least one `assistant` event contains a `tool_use` block named TOOL_NAME.
  3. The matching tool result is not an error and its numeric result equals
     the independently-computed expected word count.
  4. The final `result` event has `is_error: false`.

`check()` is a pure function of its inputs (event list, expected count) --
no I/O, no network, no clock. `main()` is the only impure part: it reads
stdin and prints one line before exiting 0 (PASS) or 1 (FAIL). This split is
what makes `test_assert_stream.py` able to exercise every failure mode
offline, without a live, billed `claude` call.

Failure modes returned as `Result(passed=False, reason=...)`, never raised,
except when the transcript itself is not well-formed JSON per line, or a
required top-level shape (event must be a dict) is violated -- those are
caller bugs (a corrupted/truncated transcript), not one of the four
acceptance checks, so they fail loudly via ValueError/JSONDecodeError rather
than being folded into a False verdict.
"""

import json
import sys
from dataclasses import dataclass
from typing import Any

SERVER_NAME = "hello-world"
TOOL_NAME = "mcp__hello-world__count_words"


@dataclass(frozen=True)
class Result:
    passed: bool
    reason: str


def _find_init_event(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for event in events:
        if event.get("type") == "system" and event.get("subtype") == "init":
            return event
    return None


def _check_server_connected(events: list[dict[str, Any]]) -> Result | None:
    """Returns a failing Result, or None if this check passed."""
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


def _find_tool_use(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Returns the first tool_use content block named TOOL_NAME, or None."""
    for event in events:
        if event.get("type") != "assistant":
            continue
        for block in event.get("message", {}).get("content", []):
            if block.get("type") == "tool_use" and block.get("name") == TOOL_NAME:
                return block
    return None


def _find_tool_result_event(events: list[dict[str, Any]], tool_use_id: str) -> dict[str, Any] | None:
    """Returns the `user` event carrying the tool_result for `tool_use_id`."""
    for event in events:
        if event.get("type") != "user":
            continue
        for block in event.get("message", {}).get("content", []):
            if block.get("type") == "tool_result" and block.get("tool_use_id") == tool_use_id:
                return event
    return None


def _extract_numeric_result(result_event: dict[str, Any]) -> int | None:
    """Best-effort extraction of the integer `count_words` returned.

    Prefers the Claude-Code-decorated `tool_use_result.structuredContent`
    field (observed directly against a live run, see fixtures/); falls back
    to parsing the tool_result content block as JSON if that field is
    absent. Returns None if no integer result can be found by either route.
    """
    structured = result_event.get("tool_use_result", {}).get("structuredContent")
    if isinstance(structured, dict) and isinstance(structured.get("result"), int):
        return structured["result"]

    for block in result_event.get("message", {}).get("content", []):
        if block.get("type") != "tool_result":
            continue
        content = block.get("content")
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and isinstance(parsed.get("result"), int):
                return parsed["result"]
    return None


def _tool_result_is_error(result_event: dict[str, Any], tool_use_id: str) -> bool:
    """Checks both places an error flag has been observed/documented to live:
    the Claude-Code-decorated `tool_use_result` and the raw content block
    itself (per the Anthropic tool_result schema, `is_error`). Neither has
    been exercised by a live error transcript in this build -- see README's
    'Explicitly out of scope' section -- so both spellings are checked
    defensively rather than assumed absent.
    """
    tool_use_result = result_event.get("tool_use_result", {})
    if tool_use_result.get("is_error") or tool_use_result.get("isError"):
        return True
    for block in result_event.get("message", {}).get("content", []):
        if block.get("type") == "tool_result" and block.get("tool_use_id") == tool_use_id:
            if block.get("is_error") or block.get("isError"):
                return True
    return False


def _find_final_result(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for event in reversed(events):
        if event.get("type") == "result":
            return event
    return None


def check(events: list[dict[str, Any]], expected_word_count: int) -> Result:
    """Applies the four acceptance checks in order, short-circuiting on the
    first failure so the reported reason always points at the true first
    cause rather than a downstream symptom.
    """
    server_check = _check_server_connected(events)
    if server_check is not None:
        return server_check

    tool_use = _find_tool_use(events)
    if tool_use is None:
        return Result(
            False,
            f"model never called {TOOL_NAME!r} -- mcp server connected but tool was not "
            "invoked (a lucky guess would not be caught by checking the final text alone)",
        )

    tool_use_id = tool_use.get("id")
    result_event = _find_tool_result_event(events, tool_use_id)
    if result_event is None:
        return Result(False, f"no tool_result found for tool_use id {tool_use_id!r}")

    if _tool_result_is_error(result_event, tool_use_id):
        return Result(False, f"tool_result for {tool_use_id!r} reported an error")

    actual = _extract_numeric_result(result_event)
    if actual is None:
        return Result(False, f"could not extract an integer result from tool_result {tool_use_id!r}")
    if actual != expected_word_count:
        return Result(False, f"tool returned {actual}, expected {expected_word_count}")

    final_result = _find_final_result(events)
    if final_result is None:
        return Result(False, "no final 'result' event found in transcript")
    if final_result.get("is_error") is not False:
        return Result(
            False,
            f"final result event has is_error={final_result.get('is_error')!r}, expected False",
        )

    return Result(True, "mcp server connected, tool invoked, result correct, final result success")


def _parse_events(lines: list[str]) -> list[dict[str, Any]]:
    """Fails loudly (JSONDecodeError) on a malformed line rather than
    skipping it -- a truncated/corrupted transcript is a bug in the caller,
    not a thing this function should paper over.
    """
    events = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        events.append(json.loads(line))
    return events


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: assert_stream.py <expected_word_count>  (transcript JSONL on stdin)", file=sys.stderr)
        return 2
    expected_word_count = int(argv[1])

    events = _parse_events(sys.stdin.readlines())
    result = check(events, expected_word_count)

    print(f"{'PASS' if result.passed else 'FAIL'}: {result.reason}")
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
