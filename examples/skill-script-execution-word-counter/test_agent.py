"""Offline self-test for the skill-script-execution example. No API key, no network.

Run:
    python test_agent.py

Covers the acceptance criteria from the research note's Build proposal:
  1-4. count_words.py's `count()` and its subprocess behavior: correct counts,
       and every documented failure mode (missing file, directory, unreadable
       file) reported as {"error": ...} + a distinct exit code, never a
       traceback on stdout/stderr.
  5.   agent.py's run_word_counter() (subprocess path) agrees with count()
       (direct in-process path) on the same file.
  6.   The context-boundary assertion: run_word_counter()'s return value never
       contains the script's own source text — only stdout crosses into the
       tool result.
  7.   The manual loop, driven by a scripted fake client, actually executes
       the real script mid-conversation and relays its real output as the
       tool_result — not a mocked stand-in.
"""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import agent

_SCRIPT_PATH = Path(__file__).parent / "skills" / "word-counter" / "scripts" / "count_words.py"


def _load_count_words():
    """Import count_words.py by path — it's a standalone script, not a package
    importable via a normal `import`, matching how a Skill's own script is
    never part of the caller's package graph."""
    spec = importlib.util.spec_from_file_location("count_words", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


count_words = _load_count_words()


def _run_script(path: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_SCRIPT_PATH), path],
        capture_output=True,
        text=True,
    )


# --------------------------------------------------------------------------- #
# 1. Correct counts on real content
# --------------------------------------------------------------------------- #

def test_count_returns_correct_counts() -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write("one two three\nfour five\n")
        path = f.name
    try:
        result = count_words.count(path)
        assert result == {"words": 5, "lines": 2, "chars": 24}, result
    finally:
        os.unlink(path)
    print("ok  count() returns correct words/lines/chars for real content")


# --------------------------------------------------------------------------- #
# 2. Failure modes: missing file, directory, unreadable file - never a traceback
# --------------------------------------------------------------------------- #

def test_missing_file_is_a_clean_error_not_a_traceback() -> None:
    missing_path = "/tmp/agentlab-skill-script-execution-does-not-exist.txt"
    proc = _run_script(missing_path)
    assert proc.returncode == 1, proc
    assert proc.stderr == "", f"expected no stderr/traceback, got: {proc.stderr!r}"
    parsed = json.loads(proc.stdout)
    assert "error" in parsed and missing_path in parsed["error"], parsed
    print("ok  missing file -> {\"error\": ...}, exit 1, no traceback")


def test_directory_path_is_a_clean_error_not_a_traceback() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        proc = _run_script(tmpdir)
        assert proc.returncode == 2, proc
        assert proc.stderr == "", f"expected no stderr/traceback, got: {proc.stderr!r}"
        parsed = json.loads(proc.stdout)
        assert "error" in parsed and tmpdir in parsed["error"], parsed
    print("ok  directory path -> {\"error\": ...}, exit 2, no traceback")


def test_unreadable_file_is_a_clean_error_not_a_traceback() -> None:
    if os.geteuid() == 0:
        print("skip unreadable-file test: running as root, permissions unenforceable")
        return
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write("secret")
        path = f.name
    try:
        os.chmod(path, 0)
        proc = _run_script(path)
        assert proc.returncode == 3, proc
        assert proc.stderr == "", f"expected no stderr/traceback, got: {proc.stderr!r}"
        parsed = json.loads(proc.stdout)
        assert "error" in parsed and path in parsed["error"], parsed
    finally:
        os.chmod(path, stat.S_IWUSR | stat.S_IRUSR)
        os.unlink(path)
    print("ok  unreadable file -> {\"error\": ...}, exit 3, no traceback")


# --------------------------------------------------------------------------- #
# 3. Empty file is not an error
# --------------------------------------------------------------------------- #

def test_empty_file_is_all_zero_not_an_error() -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        path = f.name
    try:
        proc = _run_script(path)
        assert proc.returncode == 0, proc
        parsed = json.loads(proc.stdout)
        assert parsed == {"words": 0, "lines": 0, "chars": 0}, parsed
    finally:
        os.unlink(path)
    print("ok  empty file -> all-zero counts, exit 0 (not an error)")


# --------------------------------------------------------------------------- #
# 4. run_word_counter (subprocess path) agrees with count() (direct path)
# --------------------------------------------------------------------------- #

def test_run_word_counter_agrees_with_direct_count() -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write("agentlab skills demo\nsecond line\n")
        path = f.name
    try:
        direct = count_words.count(path)
        via_tool = json.loads(agent.run_word_counter(path))
        assert via_tool == direct, (via_tool, direct)
    finally:
        os.unlink(path)
    print("ok  run_word_counter() (subprocess) agrees with count() (direct call)")


# --------------------------------------------------------------------------- #
# 5. The context-boundary assertion: source never crosses into the tool result
# --------------------------------------------------------------------------- #

def test_tool_result_never_contains_script_source() -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write("does the source leak into the tool result\n")
        path = f.name
    try:
        tool_result = agent.run_word_counter(path)
        source = _SCRIPT_PATH.read_text()
        # A distinctive substring that only exists in the script's own source,
        # never in a JSON counts/error object.
        assert "def count(" not in tool_result
        assert "solve, don't defer" not in tool_result
        # Sanity: the source really does contain what we're checking for, so
        # this test would fail loudly if the script's source ever leaked.
        assert "def count(" in source
    finally:
        os.unlink(path)
    print("ok  tool result contains only stdout - the script's own source never crosses in")


# --------------------------------------------------------------------------- #
# 6. Loop test with a fake client - proves the REAL script ran mid-conversation
# --------------------------------------------------------------------------- #

def _block(**kwargs):
    """A message content block with attribute access, like the SDK's blocks."""
    return SimpleNamespace(**kwargs)


class FakeMessages:
    """Returns a scripted sequence of responses, one per create() call, and
    records the messages it was handed so the test can inspect them."""

    def __init__(self, scripted):
        self._scripted = list(scripted)
        self.calls = []  # each entry is the messages list passed to create()

    def create(self, **kwargs):
        self.calls.append(list(kwargs["messages"]))
        return self._scripted.pop(0)


class FakeClient:
    def __init__(self, scripted):
        self.messages = FakeMessages(scripted)


def test_loop_executes_real_script_and_relays_its_real_output() -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write("real script real output\n")
        path = f.name
    try:
        # Computed independently of the loop, so the assertion below proves
        # the loop's tool_result matches an actual run of the real script -
        # not a value we scripted into the fake client ourselves.
        expected_stdout = agent.run_word_counter(path)

        tool_use_id = "toolu_wc_001"
        scripted = [
            SimpleNamespace(
                stop_reason="tool_use",
                content=[
                    _block(type="text", text="Let me count that file."),
                    _block(
                        type="tool_use",
                        id=tool_use_id,
                        name="run_word_counter",
                        input={"path": path},
                    ),
                ],
            ),
            SimpleNamespace(
                stop_reason="end_turn",
                content=[_block(type="text", text="Here are the counts.")],
            ),
        ]
        client = FakeClient(scripted)

        answer = agent.run_agent(client, f"Count the words in {path}")

        assert answer == "Here are the counts."
        assert len(client.messages.calls) == 2

        second_call_messages = client.messages.calls[1]
        tool_result_turn = second_call_messages[-1]
        assert tool_result_turn["role"] == "user"
        result_block = tool_result_turn["content"][0]
        assert result_block["type"] == "tool_result"
        assert result_block["tool_use_id"] == tool_use_id
        # The load-bearing assertion: the loop's tool_result is exactly what
        # a real subprocess run of the real script produced - not a fake.
        assert result_block["content"] == expected_stdout
        parsed = json.loads(result_block["content"])
        assert parsed == {"words": 4, "lines": 1, "chars": 24}, parsed
    finally:
        os.unlink(path)
    print("ok  manual loop executes the real script mid-conversation and relays its real stdout")


# --------------------------------------------------------------------------- #

def main() -> int:
    tests = [
        test_count_returns_correct_counts,
        test_missing_file_is_a_clean_error_not_a_traceback,
        test_directory_path_is_a_clean_error_not_a_traceback,
        test_unreadable_file_is_a_clean_error_not_a_traceback,
        test_empty_file_is_all_zero_not_an_error,
        test_run_word_counter_agrees_with_direct_count,
        test_tool_result_never_contains_script_source,
        test_loop_executes_real_script_and_relays_its_real_output,
    ]
    for t in tests:
        t()
    print(f"\nAll {len(tests)} self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
