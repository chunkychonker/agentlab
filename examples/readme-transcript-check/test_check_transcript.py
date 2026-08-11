"""Offline self-test for the README transcript checker. Stdlib only, no network.

Run:
    python3 test_check_transcript.py

Each test is one acceptance criterion from the research note's behavioral spec
(research/2026-08-11-readme-transcript-drift.md), asserted against the public
surface - `extract_transcript`, `compare`, `check`, `exit_code` - never against
internals.

The load-bearing one is `test_end_to_end_minimal_agent_loop_matches`: it points
the checker at a real README in this repo with no fixtures at all. If the
checker only ever worked on strings this file made up, it would prove nothing.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import check_transcript as ct

EXAMPLES_DIR = Path(__file__).resolve().parent.parent

# A stand-in for this repo's README shape: prose, an unmarked bash block, then
# the marked transcript.
FIXTURE_TRANSCRIPT = "ok  alpha\nok  beta\nok  gamma\n\nAll 3 self-tests passed.\n"

FIXTURE_README = """# Fixture example

Some prose about the example.

```bash
cd examples/fixture
python3 test_agent.py
```

Expected output:

```
ok  alpha
ok  beta
ok  gamma

All 3 self-tests passed.
```

Trailing prose that mentions nothing in particular.
"""

# The real drift this checker was built for: the block documented in
# examples/typed-tool-registry/README.md against what the suite really printed.
DOCUMENTED_FOUR = (
    "ok  each tool's schema (name/description/type/required) matches its type hints\n"
    "ok  TOOL_REGISTRY has exactly 3 entries, keyed by name with true identity\n"
    "ok  each tool's .call(...) returns the correct value for a known input\n"
    "ok  each tool's .call(...) raises ValueError on wrong-typed or missing arguments\n"
    "\n"
    "All 4 self-tests passed.\n"
)

ACTUAL_SIX = (
    "ok  each tool's schema (name/description/type/required) matches its type hints\n"
    "ok  TOOL_REGISTRY has exactly 3 entries, keyed by name with true identity\n"
    "ok  each tool's .call(...) returns the correct value for a known input\n"
    "ok  each tool's .call(...) raises ValueError on wrong-typed or missing arguments\n"
    "ok  run_agent joins the final text blocks on a clean finish\n"
    "ok  run_agent raises RuntimeError instead of returning '' on max_iterations\n"
    "\n"
    "All 6 self-tests passed.\n"
)


# --------------------------------------------------------------------------- #
# 1-3. Extraction (pure)
# --------------------------------------------------------------------------- #


def test_extract_returns_the_exact_block() -> None:
    extracted = ct.extract_transcript(FIXTURE_README)

    assert extracted == FIXTURE_TRANSCRIPT, repr(extracted)
    # Spelled out, because "close enough" is the failure mode being designed out:
    assert extracted.count("\n") == 5, "five lines, blank line included"
    assert extracted.endswith("passed.\n"), "trailing newline is part of the block"
    # The unmarked ```bash block above it must not be picked up.
    assert "cd examples/fixture" not in extracted

    print("ok  extractor returns the marked block exactly, trailing newline included")


def test_extract_raises_when_there_is_nothing_to_verify() -> None:
    no_marker = "# Fixture\n\nProse.\n\n```\nok  alpha\n```\n"
    try:
        ct.extract_transcript(no_marker)
    except ct.TranscriptNotFound as exc:
        assert "Expected output" in str(exc), str(exc)
    else:
        raise AssertionError("expected TranscriptNotFound when the marker is absent")

    marker_without_block = "# Fixture\n\nExpected output:\n\nBut no fenced block.\n"
    try:
        ct.extract_transcript(marker_without_block)
    except ct.TranscriptNotFound:
        pass
    else:
        raise AssertionError("expected TranscriptNotFound when no block follows")

    print("ok  extractor raises TranscriptNotFound instead of returning ''")


def test_extract_refuses_two_marked_blocks() -> None:
    two_blocks = FIXTURE_README + "\nExpected output:\n\n```\nok  delta\n```\n"
    try:
        ct.extract_transcript(two_blocks)
    except ct.AmbiguousTranscript as exc:
        assert "2" in str(exc), f"the count must be named: {exc}"
    else:
        raise AssertionError("expected AmbiguousTranscript for two marked blocks")

    print("ok  extractor refuses to guess between two marked blocks")


# --------------------------------------------------------------------------- #
# 4-7. Comparison (pure)
# --------------------------------------------------------------------------- #


def test_compare_matches_identical_text() -> None:
    assert ct.compare(ACTUAL_SIX, ACTUAL_SIX) == ct.Match()
    assert ct.compare("", "") == ct.Match()
    print("ok  compare returns Match for byte-identical transcripts")


def test_compare_catches_the_typed_tool_registry_regression() -> None:
    verdict = ct.compare(DOCUMENTED_FOUR, ACTUAL_SIX)

    assert isinstance(verdict, ct.Drift), verdict
    assert verdict.expected == DOCUMENTED_FOUR
    assert verdict.actual == ACTUAL_SIX
    assert "All 4 self-tests passed." in verdict.diff
    assert "All 6 self-tests passed." in verdict.diff

    print("ok  compare catches the real 'All 4' vs 'All 6' drift, with a diff")


def test_compare_catches_missing_extra_and_reordered_lines() -> None:
    lines = ["ok  alpha\n", "ok  beta\n", "ok  gamma\n"]
    expected = "".join(lines)

    missing = "".join([lines[0], lines[2]])
    extra = "".join([*lines, "ok  delta\n"])
    reordered = "".join([lines[1], lines[0], lines[2]])

    for label, actual in (("missing", missing), ("extra", extra), ("reordered", reordered)):
        verdict = ct.compare(expected, actual)
        assert isinstance(verdict, ct.Drift), f"{label}: {verdict}"
        assert verdict.diff, f"{label}: diff must not be empty"

    print("ok  compare catches a missing line, an extra line, and a reordering")


def test_compare_catches_a_trailing_newline_only() -> None:
    with_newline = "All 3 self-tests passed.\n"
    without = "All 3 self-tests passed."

    verdict = ct.compare(with_newline, without)

    assert isinstance(verdict, ct.Drift), verdict
    assert "No newline at end of file" in verdict.diff, verdict.diff

    print("ok  compare catches a difference of only the trailing newline")


# --------------------------------------------------------------------------- #
# 8. The runner separates Unrunnable from Drift
# --------------------------------------------------------------------------- #


def test_nonzero_exit_is_unrunnable_not_drift() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        readme = directory / ct.README_FILENAME
        readme.write_text(FIXTURE_README, encoding="utf-8")

        verdict = ct.check(
            readme,
            [sys.executable, "-c", "import sys; sys.stderr.write('boom\\n'); sys.exit(3)"],
            directory,
        )

    # stdout was empty and therefore differs from the documented block - but the
    # command failed, so calling this drift would be a false accusation.
    assert isinstance(verdict, ct.Unrunnable), verdict
    assert verdict.exit_code == 3, verdict
    assert "boom" in verdict.stderr_tail, verdict
    assert ct.exit_code(verdict) == ct.EXIT_UNRUNNABLE

    print("ok  a non-zero exit is Unrunnable, never Drift")


# --------------------------------------------------------------------------- #
# 9. End to end against a real README in this repo, no fixtures
# --------------------------------------------------------------------------- #


def test_end_to_end_minimal_agent_loop_matches() -> None:
    target = EXAMPLES_DIR / "minimal-agent-loop"
    readme = target / ct.README_FILENAME
    assert readme.is_file(), f"expected a real README at {readme}"

    before = readme.read_bytes()
    verdict = ct.check(readme, [sys.executable, "test_agent.py"], target)

    if isinstance(verdict, ct.Drift):
        raise AssertionError(
            "minimal-agent-loop's documented transcript no longer matches:\n"
            + verdict.diff
        )
    if isinstance(verdict, ct.Unrunnable):
        raise AssertionError(
            f"could not run minimal-agent-loop's self-test (exit "
            f"{verdict.exit_code}):\n{verdict.stderr_tail}"
        )
    assert isinstance(verdict, ct.Match), verdict

    # Checking is read-only: there is no update mode, by design.
    assert readme.read_bytes() == before, "the checker must never rewrite a README"

    print("ok  end to end: minimal-agent-loop's real README matches its real output")


# --------------------------------------------------------------------------- #
# 10. The CLI contract
# --------------------------------------------------------------------------- #


def test_exit_codes_follow_the_documented_table() -> None:
    assert ct.exit_code(ct.Match()) == 0
    assert ct.exit_code(ct.Drift(expected="a\n", actual="b\n", diff="...")) == 1
    assert ct.exit_code(ct.Unrunnable(exit_code=127, stderr_tail="")) == 2

    try:
        ct.exit_code("Match")  # type: ignore[arg-type]
    except TypeError:
        pass
    else:
        raise AssertionError("expected TypeError rather than a defaulted exit code")

    # And the CLI refuses a command line it cannot make sense of, rather than
    # inventing a verdict for it.
    assert ct.main([]) == ct.EXIT_USAGE
    assert ct.main(["some-dir"]) == ct.EXIT_USAGE
    assert ct.main(["some-dir", "--"]) == ct.EXIT_USAGE

    print("ok  exit codes follow the table and bad usage never yields a verdict")


# --------------------------------------------------------------------------- #


def main() -> int:
    tests = [
        test_extract_returns_the_exact_block,
        test_extract_raises_when_there_is_nothing_to_verify,
        test_extract_refuses_two_marked_blocks,
        test_compare_matches_identical_text,
        test_compare_catches_the_typed_tool_registry_regression,
        test_compare_catches_missing_extra_and_reordered_lines,
        test_compare_catches_a_trailing_newline_only,
        test_nonzero_exit_is_unrunnable_not_drift,
        test_end_to_end_minimal_agent_loop_matches,
        test_exit_codes_follow_the_documented_table,
    ]
    for test in tests:
        test()
    print(f"\nAll {len(tests)} self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
