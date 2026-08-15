"""Pure classifier for what a headless `claude` transcript says happened to a
skill's bundled-script Bash call, plus the verdict that reconciles it with the
script's real on-disk side effect.

Built against two real transcripts captured during this example's build (see
`fixtures/`), not against guessed event shapes -- the docs describe a denied
headless tool call only behaviourally ("the action doesn't run"). The observed
shapes are:

  denied   -- `tool_result` block with `is_error: true` and content
              "This command requires approval", AND an entry in the final
              `result` event's `permission_denials` array carrying the same
              `tool_use_id`.
  allowed  -- `tool_result` block with `is_error: false`, and
              `permission_denials: []`.

`find_bash_call()`, `judge()` and `session_permission_mode()` are pure
functions of their inputs -- no I/O, no clock, no env. `main()` is the only
impure part: it reads the transcript from stdin, stats the sentinel path once,
and prints one line. That split is what lets `test_assert_transcript.py`
exercise every outcome offline without a billed `claude` call.

Failure modes:
  - A line of the transcript that is not valid JSON, or an event that is not a
    JSON object: raises (JSONDecodeError / TranscriptError). A truncated or
    corrupted transcript is a caller bug, not one of the outcomes.
  - A matching Bash call whose result is an error that is *not* a permission
    denial (e.g. the script itself exited non-zero), or a call that is denied
    and non-error at the same time, or a call with no result at all: raises
    TranscriptError. Silently folding those into DENIED would report a broken
    script as a permission finding, which is the one mistake this example
    exists to avoid.
"""

import enum
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BASH_TOOL_NAME = "Bash"

# The only permission mode under which the deny/allow comparison means
# anything: `auto` or `acceptEdits`/`bypassPermissions` would grant the Bash
# call for reasons unrelated to `allowed-tools`. Asserted as a precondition,
# never assumed.
REQUIRED_PERMISSION_MODE = "default"

# Verbatim from the real denied transcript (fixtures/deny_transcript.jsonl),
# claude 2.1.221. Used only as corroboration of `permission_denials`, never as
# the sole denial signal.
DENIAL_MESSAGE = "requires approval"


class TranscriptError(Exception):
    """The transcript cannot be classified: malformed, truncated, or
    internally contradictory. Never raised for a legitimate outcome."""


class Outcome(enum.Enum):
    """What the transcript says happened to the bundled script's Bash call."""

    NOT_ATTEMPTED = "NOT_ATTEMPTED"
    DENIED = "DENIED"
    SUCCEEDED = "SUCCEEDED"


class Expect(enum.Enum):
    """Which of the two test skills a transcript came from, expressed as its
    expected outcome rather than its name, so the judge cannot be handed a
    skill it has no rule for."""

    RUNS = "runs"  # verify-allow: allowed-tools matches the body command
    BLOCKED = "blocked"  # verify-deny: no allowed-tools at all


class VerdictKind(enum.Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    # The transcript and the filesystem disagree. Reportable finding, never
    # resolved by picking a signal to trust (acceptance criterion 3).
    CONTRADICTION = "CONTRADICTION"


@dataclass(frozen=True)
class Verdict:
    kind: VerdictKind
    reason: str


def _require_dict(event: Any, index: int) -> dict[str, Any]:
    if not isinstance(event, dict):
        raise TranscriptError(f"event {index} is {type(event).__name__}, not a JSON object")
    return event


def session_permission_mode(events: list[dict[str, Any]]) -> str | None:
    """The permission mode the session actually started in, from the
    `system`/`init` event. None if there is no init event."""
    for event in events:
        if event.get("type") == "system" and event.get("subtype") == "init":
            return event.get("permissionMode")
    return None


def _matching_tool_uses(
    events: list[dict[str, Any]], command_substring: str
) -> list[dict[str, Any]]:
    """Every `tool_use` block naming Bash whose command contains
    `command_substring`, in transcript order."""
    matches = []
    for event in events:
        if event.get("type") != "assistant":
            continue
        for block in event.get("message", {}).get("content", []):
            if block.get("type") != "tool_use" or block.get("name") != BASH_TOOL_NAME:
                continue
            command = block.get("input", {}).get("command", "")
            if isinstance(command, str) and command_substring in command:
                matches.append(block)
    return matches


def _denied_tool_use_ids(events: list[dict[str, Any]]) -> set[str]:
    """Tool-use ids the CLI itself reported as permission-denied, from the
    final `result` event's `permission_denials` array."""
    denied: set[str] = set()
    for event in events:
        if event.get("type") != "result":
            continue
        for denial in event.get("permission_denials", []) or []:
            tool_use_id = denial.get("tool_use_id")
            if isinstance(tool_use_id, str):
                denied.add(tool_use_id)
    return denied


def _find_tool_result_block(
    events: list[dict[str, Any]], tool_use_id: str
) -> dict[str, Any] | None:
    for event in events:
        if event.get("type") != "user":
            continue
        for block in event.get("message", {}).get("content", []):
            if block.get("type") == "tool_result" and block.get("tool_use_id") == tool_use_id:
                return block
    return None


def _classify_one(
    events: list[dict[str, Any]], tool_use: dict[str, Any], denied_ids: set[str]
) -> Outcome:
    """Classify a single matching Bash tool_use as DENIED or SUCCEEDED.

    Raises TranscriptError if the two independent signals (the CLI's
    `permission_denials` list and the tool_result's own `is_error` flag)
    disagree, or if the call has no result at all -- both mean the transcript
    cannot be trusted to answer this example's question.
    """
    tool_use_id = tool_use.get("id")
    if not isinstance(tool_use_id, str):
        raise TranscriptError(f"Bash tool_use block has no string id: {tool_use!r}")

    result_block = _find_tool_result_block(events, tool_use_id)
    if result_block is None:
        raise TranscriptError(
            f"Bash tool_use {tool_use_id!r} has no tool_result in the transcript "
            "(truncated or interrupted run -- outcome genuinely unknown)"
        )

    is_error = bool(result_block.get("is_error"))
    is_denied = tool_use_id in denied_ids

    if is_denied and not is_error:
        raise TranscriptError(
            f"Bash tool_use {tool_use_id!r} is listed in permission_denials but its "
            "tool_result is not an error -- contradictory transcript"
        )
    if is_denied:
        return Outcome.DENIED
    if is_error:
        content = result_block.get("content")
        raise TranscriptError(
            f"Bash tool_use {tool_use_id!r} errored without being permission-denied "
            f"(content={content!r}) -- this is a broken script or environment, not a "
            "permission finding"
        )
    return Outcome.SUCCEEDED


def find_bash_call(events: list[dict[str, Any]], command_substring: str) -> Outcome:
    """Classify what happened to the bundled script's Bash call.

    NOT_ATTEMPTED if no Bash tool_use names a command containing
    `command_substring`; SUCCEEDED if any such call ran without error; DENIED
    if every such call was permission-denied.

    Raises TranscriptError per `_classify_one`.
    """
    tool_uses = _matching_tool_uses(events, command_substring)
    if not tool_uses:
        return Outcome.NOT_ATTEMPTED

    denied_ids = _denied_tool_use_ids(events)
    outcomes = [_classify_one(events, tool_use, denied_ids) for tool_use in tool_uses]
    if Outcome.SUCCEEDED in outcomes:
        return Outcome.SUCCEEDED
    return Outcome.DENIED


def judge(expect: Expect, outcome: Outcome, sentinel_exists: bool) -> Verdict:
    """Reconcile the transcript-parsed outcome with the sentinel file's real
    existence, then against what the skill under test was expected to do.

    The consistency rule is checked first and is independent of `expect`:
    SUCCEEDED must mean the sentinel exists, and anything else must mean it
    does not. A mismatch is CONTRADICTION -- reported, never resolved by
    trusting one signal over the other (acceptance criterion 3).
    """
    ran = outcome is Outcome.SUCCEEDED
    if ran != sentinel_exists:
        return Verdict(
            VerdictKind.CONTRADICTION,
            f"transcript says {outcome.value} but sentinel file "
            f"{'exists' if sentinel_exists else 'does not exist'} -- the two signals "
            "disagree; neither is trusted",
        )

    if expect is Expect.RUNS:
        if ran:
            return Verdict(
                VerdictKind.PASS,
                "allowed-tools rule matched the body command: Bash call ran with no "
                "approval available, and the script's sentinel file exists",
            )
        return Verdict(
            VerdictKind.FAIL,
            f"expected the bundled script to run, got {outcome.value} and no sentinel file"
            + (
                " -- the allowed-tools rule did NOT suppress the permission prompt"
                if outcome is Outcome.DENIED
                else " -- the model never issued the command (tool-choice noise, retryable)"
            ),
        )

    if ran:
        return Verdict(
            VerdictKind.FAIL,
            "control case leaked: the script ran with no allowed-tools rule at all, so a "
            "pass in the allow case would not be attributable to allowed-tools",
        )
    if outcome is Outcome.DENIED:
        return Verdict(
            VerdictKind.PASS,
            "control case denied as expected: without allowed-tools the identical command "
            "was permission-denied and no sentinel file was written",
        )
    return Verdict(
        VerdictKind.PASS,
        "control case did not run the script, but it was never attempted either -- weak "
        "evidence (no denial observed), still consistent with no sentinel file",
    )


def parse_events(lines: list[str]) -> list[dict[str, Any]]:
    """Parse JSONL, failing loudly on a malformed line rather than skipping it."""
    events = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        events.append(_require_dict(json.loads(stripped), index))
    return events


_EXIT_PASS = 0
_EXIT_FAIL = 1
_EXIT_USAGE = 2
_EXIT_CONTRADICTION = 3
_EXIT_INVALID = 4

_EXIT_BY_KIND = {
    VerdictKind.PASS: _EXIT_PASS,
    VerdictKind.FAIL: _EXIT_FAIL,
    VerdictKind.CONTRADICTION: _EXIT_CONTRADICTION,
}


def main(argv: list[str]) -> int:
    """Entry point (impure). Usage:

        assert_transcript.py <runs|blocked> <sentinel-path> <command-substring>

    with the transcript JSONL on stdin. Exit codes: 0 pass, 1 fail (retryable),
    2 usage error, 3 transcript/filesystem contradiction (do not retry -- it is
    a finding), 4 the run itself was invalid (wrong permission mode, malformed
    or unclassifiable transcript).
    """
    if len(argv) != 4:
        print(
            "usage: assert_transcript.py <runs|blocked> <sentinel-path> "
            "<command-substring>  (transcript JSONL on stdin)",
            file=sys.stderr,
        )
        return _EXIT_USAGE
    try:
        expect = Expect(argv[1])
    except ValueError:
        print(f"unknown expectation {argv[1]!r}: use 'runs' or 'blocked'", file=sys.stderr)
        return _EXIT_USAGE
    sentinel_path, command_substring = Path(argv[2]), argv[3]

    try:
        events = parse_events(sys.stdin.readlines())
    except (json.JSONDecodeError, TranscriptError) as exc:
        print(f"INVALID: unparseable transcript: {exc}")
        return _EXIT_INVALID

    mode = session_permission_mode(events)
    if mode != REQUIRED_PERMISSION_MODE:
        print(
            f"INVALID: session permission mode is {mode!r}, expected "
            f"{REQUIRED_PERMISSION_MODE!r} -- the allow/deny comparison would be meaningless"
        )
        return _EXIT_INVALID

    try:
        outcome = find_bash_call(events, command_substring)
    except TranscriptError as exc:
        print(f"INVALID: {exc}")
        return _EXIT_INVALID

    verdict = judge(expect, outcome, sentinel_path.exists())
    print(f"{verdict.kind.value} [{outcome.value}]: {verdict.reason}")
    return _EXIT_BY_KIND[verdict.kind]


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
