"""Offline self-test for assert_stream.py -- no subprocess, no live `claude`
call, no network, no billed API usage.

Two kinds of evidence:
  1. A real, once-recorded transcript (fixtures/real_transcript.jsonl,
     captured from an actual `claude --mcp-config ... -p ...` run against
     examples/mcp-hello-world/server.py during this example's own build)
     proves `check()` accepts a real pass case, not just synthetic ones.
  2. Minimal synthetic event lists, one deliberate mutation away from that
     real shape, prove each of the four acceptance checks actually catches
     the failure it claims to catch -- not just that some string ends up in
     `reason`.

Convention matches examples/mcp-hello-world/test_server.py: plain `test_*`
functions, each printing "ok  <description>" on success, collected and run
from `main() -> int`.

Run: python3 test_assert_stream.py
"""

import copy
import json
from pathlib import Path

from assert_stream import Result, check

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "real_transcript.jsonl"
REAL_EXPECTED_WORD_COUNT = 6  # len("the quick brown fox jumps over".split())


def _load_fixture() -> list[dict]:
    with FIXTURE_PATH.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def _minimal_passing_events() -> list[dict]:
    """A hand-built minimal transcript with the same shapes `check()` reads,
    independent of the fixture file, so mutation tests below don't
    accidentally depend on fixture-specific noise (thinking events, etc.).
    """
    return [
        {
            "type": "system",
            "subtype": "init",
            "mcp_servers": [{"name": "hello-world", "status": "connected"}],
        },
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "mcp__hello-world__count_words",
                        "input": {"text": "the quick brown fox jumps over"},
                    }
                ]
            },
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_1", "content": '{"result":6}'}
                ]
            },
            "tool_use_result": {"content": '{"result":6}', "structuredContent": {"result": 6}},
        },
        {"type": "result", "is_error": False, "result": "6"},
    ]


def test_real_fixture_transcript_passes() -> None:
    events = _load_fixture()
    result = check(events, REAL_EXPECTED_WORD_COUNT)
    assert result.passed, result.reason
    print("ok  real recorded transcript (fixtures/real_transcript.jsonl) -> PASS")


def test_minimal_synthetic_transcript_passes() -> None:
    result = check(_minimal_passing_events(), 6)
    assert result.passed, result.reason
    print("ok  minimal synthetic passing transcript -> PASS")


def test_missing_mcp_server_fails() -> None:
    events = copy.deepcopy(_minimal_passing_events())
    events[0]["mcp_servers"] = []
    result = check(events, 6)
    assert not result.passed
    assert "missing from init event" in result.reason, result.reason
    print("ok  hello-world absent from init's mcp_servers -> FAIL, correct reason")


def test_mcp_server_not_connected_fails() -> None:
    events = copy.deepcopy(_minimal_passing_events())
    events[0]["mcp_servers"] = [{"name": "hello-world", "status": "failed"}]
    result = check(events, 6)
    assert not result.passed
    assert "not 'connected'" in result.reason, result.reason
    print("ok  hello-world status 'failed' in init event -> FAIL, correct reason")


def test_no_tool_use_fails() -> None:
    """A lucky guess (correct final text, tool never called) must still fail --
    this is the specific case the research note calls out as the reason to
    check for tool_use events rather than just the final answer text.
    """
    events = copy.deepcopy(_minimal_passing_events())
    events[1]["message"]["content"] = [{"type": "text", "text": "6"}]
    result = check(events, 6)
    assert not result.passed
    assert "never called" in result.reason, result.reason
    print("ok  model answers '6' without calling the tool -> FAIL (no lucky-guess pass)")


def test_missing_tool_result_fails() -> None:
    events = copy.deepcopy(_minimal_passing_events())
    del events[2]
    result = check(events, 6)
    assert not result.passed
    assert "no tool_result found" in result.reason, result.reason
    print("ok  tool_use with no matching tool_result event -> FAIL, correct reason")


def test_tool_result_error_fails() -> None:
    events = copy.deepcopy(_minimal_passing_events())
    events[2]["tool_use_result"]["is_error"] = True
    result = check(events, 6)
    assert not result.passed
    assert "reported an error" in result.reason, result.reason
    print("ok  tool_use_result.is_error=True -> FAIL, correct reason")


def test_wrong_word_count_fails() -> None:
    events = copy.deepcopy(_minimal_passing_events())
    events[2]["tool_use_result"]["structuredContent"] = {"result": 99}
    result = check(events, 6)
    assert not result.passed
    assert "returned 99, expected 6" in result.reason, result.reason
    print("ok  tool returns numerically wrong count -> FAIL, correct reason")


def test_final_result_is_error_fails() -> None:
    events = copy.deepcopy(_minimal_passing_events())
    events[3]["is_error"] = True
    result = check(events, 6)
    assert not result.passed
    assert "is_error=True" in result.reason, result.reason
    print("ok  final result event is_error=True -> FAIL, correct reason")


def test_missing_final_result_fails() -> None:
    events = copy.deepcopy(_minimal_passing_events())
    del events[3]
    result = check(events, 6)
    assert not result.passed
    assert "no final 'result' event" in result.reason, result.reason
    print("ok  transcript truncated before final result event -> FAIL, correct reason")


def _run_all() -> None:
    test_real_fixture_transcript_passes()
    test_minimal_synthetic_transcript_passes()
    test_missing_mcp_server_fails()
    test_mcp_server_not_connected_fails()
    test_no_tool_use_fails()
    test_missing_tool_result_fails()
    test_tool_result_error_fails()
    test_wrong_word_count_fails()
    test_final_result_is_error_fails()
    test_missing_final_result_fails()


def main() -> int:
    _run_all()
    print("\nAll 10 self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
