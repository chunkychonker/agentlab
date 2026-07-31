"""Offline self-test for the reference-file selective-loading example. No API
key, no network.

Run:
    python test_agent.py

Covers:
  1. read_reference() unit tests: correct content on a legitimate filename,
     path-traversal rejection (relative and absolute) that never leaks
     out-of-bounds content, a nonexistent-but-plausible sibling filename, and
     the resolved-path-is-a-directory case.
  2. The manual loop mechanics, driven by scripted fake clients: single-read,
     multi-read (in order), and no-read transcripts, each asserting which
     reference files' content did/did not appear anywhere in the messages
     sent to the client — the checkable form of "the sibling files remain on
     the filesystem, consuming zero context tokens until needed."
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import agent


# --------------------------------------------------------------------------- #
# 1. read_reference() unit tests
# --------------------------------------------------------------------------- #


def test_reads_a_legitimate_reference_file() -> None:
    on_disk_path = os.path.join(agent.REFERENCE_DIR, "finance.md")
    with open(on_disk_path, "r", encoding="utf-8") as f:
        expected = f.read()

    assert agent.read_reference("finance.md") == expected
    print("ok  read_reference returns exact on-disk content for finance.md")


def test_rejects_relative_traversal_outside_reference_dir() -> None:
    real_skill_md = os.path.join(agent.SKILL_DIR, "SKILL.md")
    with open(real_skill_md, "r", encoding="utf-8") as f:
        real_skill_md_content = f.read()

    out = agent.read_reference("../SKILL.md")
    assert out.startswith("Error:"), out
    assert out != real_skill_md_content
    print("ok  '../SKILL.md' traversal is rejected, real SKILL.md content never returned")

    out2 = agent.read_reference("../../../../etc/passwd")
    assert out2.startswith("Error:"), out2
    assert "root:" not in out2
    print("ok  deep '../' traversal toward /etc/passwd is rejected, no passwd content leaked")


def test_rejects_absolute_path_outside_reference_dir() -> None:
    out = agent.read_reference("/etc/hosts")
    assert out.startswith("Error:"), out
    # Must not equal (or contain) the real /etc/hosts content.
    with open("/etc/hosts", "r", encoding="utf-8") as f:
        real_hosts = f.read()
    assert out != real_hosts
    assert real_hosts.strip() == "" or real_hosts.strip() not in out
    print("ok  absolute path '/etc/hosts' is rejected, real content never returned")

    out2 = agent.read_reference("/etc/passwd")
    assert out2.startswith("Error:"), out2
    assert "root:" not in out2
    print("ok  absolute path '/etc/passwd' is rejected, no passwd content leaked")


def test_nonexistent_sibling_filename_errors_cleanly() -> None:
    out = agent.read_reference("marketing.md")
    assert out.startswith("Error:"), out
    assert "marketing.md" in out
    print("ok  nonexistent sibling 'marketing.md' returns a clean named error, not a crash")


def test_directory_target_errors_cleanly() -> None:
    out = agent.read_reference("reference")
    assert out.startswith("Error:"), out
    assert "directory" in out
    print("ok  a directory target returns a clean error, not a traceback or empty content")


# --------------------------------------------------------------------------- #
# 2. Loop tests with scripted fake clients
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


def _tool_use_response(tool_use_id: str, filename: str):
    return SimpleNamespace(
        stop_reason="tool_use",
        content=[
            _block(
                type="tool_use",
                id=tool_use_id,
                name="read_reference",
                input={"filename": filename},
            )
        ],
    )


def _end_turn_response(text: str):
    return SimpleNamespace(stop_reason="end_turn", content=[_block(type="text", text=text)])


def _transcript_text(calls) -> str:
    """Flatten every message sent to the fake client into one string, for
    substring-absence assertions."""
    chunks = []
    for call in calls:
        for msg in call:
            content = msg["content"]
            if isinstance(content, str):
                chunks.append(content)
            else:
                for block in content:
                    if isinstance(block, dict):
                        chunks.append(str(block.get("content", "")))
                    else:
                        chunks.append(str(getattr(block, "text", "")))
    return "\n".join(chunks)


def test_selective_loading_single_read() -> None:
    scripted = [
        _tool_use_response("toolu_1", "finance.md"),
        _end_turn_response("Q3 bookings count if the order form is dated in-quarter."),
    ]
    client = FakeClient(scripted)

    final_text, filenames_read = agent.run_agent(client, "What's our Q3 revenue policy?")

    assert filenames_read == ["finance.md"], filenames_read
    assert final_text == "Q3 bookings count if the order form is dated in-quarter."

    transcript = _transcript_text(client.messages.calls)
    assert "stage-close-probability" not in transcript  # sales.md-only content
    assert "stickiness ratio" not in transcript  # product.md-only content
    print("ok  single-read transcript loads only finance.md; sales.md/product.md never appear")


def test_selective_loading_multi_read_in_order() -> None:
    scripted = [
        _tool_use_response("toolu_1", "finance.md"),
        _tool_use_response("toolu_2", "sales.md"),
        _end_turn_response("Revenue is ARR-based; pipeline is weighted by stage."),
    ]
    client = FakeClient(scripted)

    final_text, filenames_read = agent.run_agent(client, "Compare revenue and pipeline.")

    assert filenames_read == ["finance.md", "sales.md"], filenames_read

    transcript = _transcript_text(client.messages.calls)
    assert "stickiness ratio" not in transcript  # product.md content, never requested
    print("ok  multi-read transcript loads finance.md then sales.md in order; product.md never appears")


def test_no_tool_call_reads_nothing() -> None:
    scripted = [_end_turn_response("That's a general question, no lookup needed.")]
    client = FakeClient(scripted)

    final_text, filenames_read = agent.run_agent(client, "Hello, what can you help with?")

    assert filenames_read == []
    assert final_text == "That's a general question, no lookup needed."
    print("ok  no tool call at all -> filenames_read is empty (zero context cost until accessed)")


def test_loop_raises_when_it_never_finishes() -> None:
    def endless():
        n = 0
        while True:
            n += 1
            yield _tool_use_response(f"toolu_{n}", "finance.md")

    gen = endless()

    class EndlessMessages:
        def create(self, **kwargs):
            return next(gen)

    client = SimpleNamespace(messages=EndlessMessages())

    try:
        agent.run_agent(client, "loop forever", max_turns=3)
    except RuntimeError as exc:
        assert "did not finish" in str(exc)
        print("ok  loop enforces max_turns and raises when exceeded")
    else:
        raise AssertionError("expected RuntimeError when max_turns is exceeded")


# --------------------------------------------------------------------------- #

def main() -> int:
    tests = [
        test_reads_a_legitimate_reference_file,
        test_rejects_relative_traversal_outside_reference_dir,
        test_rejects_absolute_path_outside_reference_dir,
        test_nonexistent_sibling_filename_errors_cleanly,
        test_directory_target_errors_cleanly,
        test_selective_loading_single_read,
        test_selective_loading_multi_read_in_order,
        test_no_tool_call_reads_nothing,
        test_loop_raises_when_it_never_finishes,
    ]
    for t in tests:
        t()
    print(f"\nAll {len(tests)} self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
