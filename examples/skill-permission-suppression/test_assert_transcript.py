"""Offline self-test for assert_transcript.py -- no subprocess, no live
`claude` call, no network, no billed API usage.

Two kinds of evidence, matching examples/mcp-connect-claude-code's convention:

  1. The two real transcripts captured during this example's build
     (fixtures/allow_transcript.jsonl, fixtures/deny_transcript.jsonl) parse to
     the outcomes actually observed on disk at capture time -- SUCCEEDED with
     the sentinel present for verify-allow, DENIED with no sentinel for
     verify-deny. These are the primary cases: real bytes from a real `claude`
     process, not shapes hand-typed to match the code.
  2. Mutations one field away from those real shapes prove each Outcome, each
     VerdictKind (including CONTRADICTION), and each loud TranscriptError is
     produced by the condition it claims to detect.

Run: python3 test_assert_transcript.py
"""

import copy
import json
from pathlib import Path

from assert_transcript import (
    Expect,
    Outcome,
    TranscriptError,
    VerdictKind,
    find_bash_call,
    judge,
    parse_events,
    session_permission_mode,
)

FIXTURES = Path(__file__).parent / "fixtures"
COMMAND_SUBSTRING = "mark.sh"

# Ground truth recorded at capture time by `ls -l` on the sentinel path, not
# inferred from the transcripts these tests are checking.
ALLOW_SENTINEL_EXISTED = True
DENY_SENTINEL_EXISTED = False


def _load(name: str) -> list[dict]:
    with (FIXTURES / name).open() as f:
        return parse_events(f.readlines())


def _allow_events() -> list[dict]:
    return _load("allow_transcript.jsonl")


def _deny_events() -> list[dict]:
    return _load("deny_transcript.jsonl")


def test_real_allow_transcript_is_succeeded() -> None:
    assert find_bash_call(_allow_events(), COMMAND_SUBSTRING) is Outcome.SUCCEEDED
    print("ok  real verify-allow transcript -> SUCCEEDED")


def test_real_deny_transcript_is_denied() -> None:
    assert find_bash_call(_deny_events(), COMMAND_SUBSTRING) is Outcome.DENIED
    print("ok  real verify-deny transcript -> DENIED")


def test_real_transcripts_ran_in_default_permission_mode() -> None:
    assert session_permission_mode(_allow_events()) == "default"
    assert session_permission_mode(_deny_events()) == "default"
    print("ok  both real transcripts started in permissionMode 'default'")


def test_real_allow_case_passes_end_to_end() -> None:
    verdict = judge(Expect.RUNS, Outcome.SUCCEEDED, ALLOW_SENTINEL_EXISTED)
    assert verdict.kind is VerdictKind.PASS, verdict.reason
    print("ok  verify-allow: SUCCEEDED + sentinel on disk -> PASS")


def test_real_deny_case_passes_end_to_end() -> None:
    verdict = judge(Expect.BLOCKED, Outcome.DENIED, DENY_SENTINEL_EXISTED)
    assert verdict.kind is VerdictKind.PASS, verdict.reason
    print("ok  verify-deny: DENIED + no sentinel -> PASS")


def test_no_matching_bash_call_is_not_attempted() -> None:
    """Mutation: the model issues a Bash call that isn't the bundled script."""
    events = copy.deepcopy(_allow_events())
    for event in events:
        for block in event.get("message", {}).get("content", []):
            if block.get("type") == "tool_use":
                block["input"]["command"] = "echo hello"
    assert find_bash_call(events, COMMAND_SUBSTRING) is Outcome.NOT_ATTEMPTED
    print("ok  no Bash call containing 'mark.sh' -> NOT_ATTEMPTED")


def test_other_tool_with_matching_command_is_not_a_bash_call() -> None:
    """A non-Bash tool naming the same path must not count as an execution --
    a Write call to the sentinel would otherwise look like the script ran."""
    events = [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_x",
                        "name": "Write",
                        "input": {"command": "/skill/scripts/mark.sh /tmp/s.txt"},
                    }
                ]
            },
        }
    ]
    assert find_bash_call(events, COMMAND_SUBSTRING) is Outcome.NOT_ATTEMPTED
    print("ok  matching command on a non-Bash tool_use -> NOT_ATTEMPTED")


def test_denial_requires_permission_denials_entry() -> None:
    """Mutation: drop the `permission_denials` entry but keep the errored
    tool_result. The two denial signals now disagree, so the transcript is
    unclassifiable and must raise rather than be guessed as DENIED."""
    events = copy.deepcopy(_deny_events())
    for event in events:
        if event.get("type") == "result":
            event["permission_denials"] = []
    try:
        find_bash_call(events, COMMAND_SUBSTRING)
    except TranscriptError as exc:
        assert "without being permission-denied" in str(exc), str(exc)
        print("ok  errored tool_result with no permission_denials entry -> raises loudly")
        return
    raise AssertionError("expected TranscriptError")


def test_denied_but_non_error_result_raises() -> None:
    """Mutation: keep the denial entry but flip the tool_result to success."""
    events = copy.deepcopy(_deny_events())
    for event in events:
        for block in event.get("message", {}).get("content", []):
            if block.get("type") == "tool_result":
                block["is_error"] = False
    try:
        find_bash_call(events, COMMAND_SUBSTRING)
    except TranscriptError as exc:
        assert "contradictory transcript" in str(exc), str(exc)
        print("ok  permission_denials entry with a non-error result -> raises loudly")
        return
    raise AssertionError("expected TranscriptError")


def test_missing_tool_result_raises() -> None:
    """Mutation: truncate the transcript after the tool_use."""
    events = [e for e in _allow_events() if e.get("type") != "user"]
    try:
        find_bash_call(events, COMMAND_SUBSTRING)
    except TranscriptError as exc:
        assert "no tool_result" in str(exc), str(exc)
        print("ok  tool_use with no tool_result -> raises loudly")
        return
    raise AssertionError("expected TranscriptError")


def test_transcript_says_ran_but_sentinel_absent_is_contradiction() -> None:
    verdict = judge(Expect.RUNS, Outcome.SUCCEEDED, sentinel_exists=False)
    assert verdict.kind is VerdictKind.CONTRADICTION, verdict.reason
    assert "disagree" in verdict.reason
    print("ok  SUCCEEDED but no sentinel file -> CONTRADICTION, not PASS")


def test_transcript_says_denied_but_sentinel_present_is_contradiction() -> None:
    verdict = judge(Expect.BLOCKED, Outcome.DENIED, sentinel_exists=True)
    assert verdict.kind is VerdictKind.CONTRADICTION, verdict.reason
    print("ok  DENIED but sentinel file exists -> CONTRADICTION, not PASS")


def test_contradiction_check_is_expectation_independent() -> None:
    """The consistency rule must not depend on which skill produced the
    transcript -- otherwise one skill's contradiction could be read as a
    legitimate result."""
    for expect in Expect:
        assert judge(expect, Outcome.SUCCEEDED, False).kind is VerdictKind.CONTRADICTION
        assert judge(expect, Outcome.NOT_ATTEMPTED, True).kind is VerdictKind.CONTRADICTION
    print("ok  consistency rule holds for both Expect values")


def test_allow_case_denied_is_the_headline_failure() -> None:
    """If a future claude version stops honouring the rule, this is the shape
    the failure takes: the allow case denied, no sentinel, signals agreeing."""
    verdict = judge(Expect.RUNS, Outcome.DENIED, sentinel_exists=False)
    assert verdict.kind is VerdictKind.FAIL, verdict.reason
    assert "did NOT suppress the permission prompt" in verdict.reason
    print("ok  verify-allow denied -> FAIL naming the suppression claim")


def test_allow_case_not_attempted_is_retryable_failure() -> None:
    verdict = judge(Expect.RUNS, Outcome.NOT_ATTEMPTED, sentinel_exists=False)
    assert verdict.kind is VerdictKind.FAIL, verdict.reason
    assert "retryable" in verdict.reason
    print("ok  verify-allow never attempted -> FAIL marked retryable")


def test_deny_case_running_is_a_leaked_control() -> None:
    """The control leaking (script runs with no allowed-tools at all) makes the
    whole comparison meaningless, so it must fail rather than pass quietly."""
    verdict = judge(Expect.BLOCKED, Outcome.SUCCEEDED, sentinel_exists=True)
    assert verdict.kind is VerdictKind.FAIL, verdict.reason
    assert "control case leaked" in verdict.reason
    print("ok  verify-deny succeeding -> FAIL (control leaked)")


def test_deny_case_not_attempted_passes_as_weak_evidence() -> None:
    verdict = judge(Expect.BLOCKED, Outcome.NOT_ATTEMPTED, sentinel_exists=False)
    assert verdict.kind is VerdictKind.PASS, verdict.reason
    assert "weak" in verdict.reason
    print("ok  verify-deny never attempted -> PASS, flagged as weak evidence")


def test_malformed_transcript_line_raises() -> None:
    try:
        parse_events(['{"type": "system"}', "not json"])
    except json.JSONDecodeError:
        print("ok  malformed JSONL line -> raises instead of being skipped")
        return
    raise AssertionError("expected JSONDecodeError")


def test_non_object_event_raises() -> None:
    try:
        parse_events(["[1, 2, 3]"])
    except TranscriptError as exc:
        assert "not a JSON object" in str(exc)
        print("ok  JSON array where an event object belongs -> raises loudly")
        return
    raise AssertionError("expected TranscriptError")


TESTS = [
    test_real_allow_transcript_is_succeeded,
    test_real_deny_transcript_is_denied,
    test_real_transcripts_ran_in_default_permission_mode,
    test_real_allow_case_passes_end_to_end,
    test_real_deny_case_passes_end_to_end,
    test_no_matching_bash_call_is_not_attempted,
    test_other_tool_with_matching_command_is_not_a_bash_call,
    test_denial_requires_permission_denials_entry,
    test_denied_but_non_error_result_raises,
    test_missing_tool_result_raises,
    test_transcript_says_ran_but_sentinel_absent_is_contradiction,
    test_transcript_says_denied_but_sentinel_present_is_contradiction,
    test_contradiction_check_is_expectation_independent,
    test_allow_case_denied_is_the_headline_failure,
    test_allow_case_not_attempted_is_retryable_failure,
    test_deny_case_running_is_a_leaked_control,
    test_deny_case_not_attempted_passes_as_weak_evidence,
    test_malformed_transcript_line_raises,
    test_non_object_event_raises,
]


def main() -> int:
    for test in TESTS:
        test()
    print(f"\nAll {len(TESTS)} self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
