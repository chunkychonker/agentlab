"""Verify that a README's documented "Expected output" block is still what the
command actually prints.

The problem this exists for: every example README in this repo hand-copies its
self-test transcript into a fenced block. Nothing binds that copy to the
program, so it rots silently - `typed-tool-registry`'s block claimed "All 4
self-tests passed" for nine days while the suite emitted "All 6".

The design is Go's testable examples (`// Output:` compared against captured
stdout), reduced to the smallest thing that works here: extract the block, run
the command, compare with `==`. No dependency, no regex, no fuzz.

The verdict has three states, not two:

    Match                     stdout is byte-identical to the documented block
    Drift(expected, actual)   the command ran fine and printed something else
    Unrunnable(code, stderr)  the command failed; the README was NOT judged

The third state is the whole point. A missing dependency makes the command exit
non-zero with empty stdout; calling that "drift" is a false accusation about a
README that may be perfectly correct.

    Pure core (no I/O):   extract_transcript, compare, exit_code, format_verdict
    Imperative shell:     check, main

See the research note this came from:
    research/2026-08-11-readme-transcript-drift.md

Run it:
    python3 check_transcript.py <example-dir> -- <command> [args...]

Run the offline self-test (stdlib only, no key, no network):
    python3 test_check_transcript.py
"""

from __future__ import annotations

import dataclasses
import difflib
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

# The heading that introduces a documented transcript in this repo's READMEs.
DEFAULT_MARKER = "Expected output"

# The README filename the CLI looks for inside the example directory.
README_FILENAME = "README.md"

# How much of a failed command's stderr to carry back in an Unrunnable verdict.
STDERR_TAIL_LINES = 10

# A self-test that has not finished in this long is a hung check, not a verdict.
DEFAULT_TIMEOUT_SECONDS = 120

# Exit codes. The first three are the verdict table; the last two are the CLI's
# own failure states, kept distinct so a caller can never confuse "your README
# is wrong" with "I could not run the check".
EXIT_MATCH = 0
EXIT_DRIFT = 1
EXIT_UNRUNNABLE = 2
EXIT_USAGE = 64  # sysexits EX_USAGE
EXIT_INPUT_ERROR = 65  # sysexits EX_DATAERR: no README, no block, or two blocks
EXIT_CHECK_ERROR = 70  # sysexits EX_SOFTWARE: the check itself could not run

_DIFF_FROM = "README (documented)"
_DIFF_TO = "actual stdout"
_NO_NEWLINE_NOTE = "\\ No newline at end of file"

USAGE = (
    "usage: python3 check_transcript.py <example-dir> -- <command> [args...]\n"
    "example: python3 check_transcript.py ../minimal-agent-loop "
    "-- python3 test_agent.py"
)


class TranscriptNotFound(Exception):
    """The README documents no transcript this checker can verify.

    Either the marker is absent, or it is not followed by a fenced block, or the
    fence is never closed. Raised rather than returning "" so that "documents
    nothing" can never be mistaken for "documents empty output".
    """


class AmbiguousTranscript(Exception):
    """The README has more than one marked transcript block.

    Raised rather than silently taking the first: `mcp-connect-claude-code` has
    two, one of which is a billed live run that cannot be reproduced offline.
    Guessing which one the caller meant is how a checker starts lying.
    """


class UsageError(Exception):
    """The command line did not have the shape `<dir> -- <command...>`."""


# --------------------------------------------------------------------------- #
# Verdicts
# --------------------------------------------------------------------------- #


@dataclasses.dataclass(frozen=True)
class Match:
    """The command exited 0 and its stdout equals the documented block exactly."""


@dataclasses.dataclass(frozen=True)
class Drift:
    """The command exited 0 and printed something other than the documented block."""

    expected: str
    actual: str
    diff: str


@dataclasses.dataclass(frozen=True)
class Unrunnable:
    """The command exited non-zero, so the README was not judged at all.

    Carries the exit code and the tail of stderr so the caller can see *why*
    without the checker having to guess whether the doc is wrong.
    """

    exit_code: int
    stderr_tail: str


# A non-zero exit is never reported as Drift, even when stdout also differs -
# so these three are mutually exclusive by construction, not by convention.
Verdict = Match | Drift | Unrunnable


# --------------------------------------------------------------------------- #
# Pure core - no filesystem, no subprocess, no clock
# --------------------------------------------------------------------------- #


def _is_fence(line: str) -> bool:
    return line.lstrip().startswith("```")


def _is_closing_fence(line: str) -> bool:
    return line.rstrip() == "```"


def _marked_block_bounds(lines: Sequence[str], marker: str) -> list[tuple[int, int]]:
    """Index pairs (first content line, closing-fence line) for every block whose
    nearest preceding non-blank line contains ``marker``.

    Defining a marked block by its *immediately preceding* line - rather than by
    every occurrence of the marker anywhere in the file - is what lets a README
    discuss the marker in prose without the checker counting that as a block.

    Raises ``TranscriptNotFound`` if a marked fence is never closed.
    """
    bounds: list[tuple[int, int]] = []
    for index, line in enumerate(lines):
        if not _is_fence(line):
            continue

        preceding = index - 1
        while preceding >= 0 and not lines[preceding].strip():
            preceding -= 1
        if preceding < 0 or marker not in lines[preceding]:
            continue

        for close in range(index + 1, len(lines)):
            if _is_closing_fence(lines[close]):
                bounds.append((index + 1, close))
                break
        else:
            raise TranscriptNotFound(
                f"the {marker!r} block opening on line {index + 1} is never closed"
            )
    return bounds


def extract_transcript(readme_text: str, *, marker: str = DEFAULT_MARKER) -> str:
    """Return the one transcript documented in ``readme_text``.

    A transcript is the body of a fenced block whose nearest preceding non-blank
    line contains ``marker``. The returned text is newline-terminated, one "\\n"
    per line, so it can be compared directly against captured stdout. Line
    endings are the single normalization applied, and only because the README is
    read from a checkout that may use CRLF while stdout will not; nothing else
    is stripped, trimmed, or reordered.

    Pure: a function of the text alone. Failure modes: ``TranscriptNotFound`` if
    no marked block exists or a marked fence is unclosed; ``AmbiguousTranscript``
    if more than one marked block exists. Never returns "" for a missing block -
    "" is returned only for a block that genuinely documents empty output.
    """
    lines = readme_text.splitlines()
    bounds = _marked_block_bounds(lines, marker)

    if not bounds:
        raise TranscriptNotFound(
            f"no fenced block introduced by {marker!r} was found in the README"
        )
    if len(bounds) > 1:
        starts = ", ".join(str(start) for start, _ in bounds)
        raise AmbiguousTranscript(
            f"found {len(bounds)} blocks introduced by {marker!r} "
            f"(content starting at lines {starts}); refusing to guess which one "
            f"is the reproducible transcript"
        )

    start, end = bounds[0]
    return "".join(line + "\n" for line in lines[start:end])


def unified_diff(expected: str, actual: str) -> str:
    """A unified diff of two transcripts, annotating a missing final newline.

    Pure. Returns "" when the two are equal.
    """
    chunks: list[str] = []
    for line in difflib.unified_diff(
        expected.splitlines(keepends=True),
        actual.splitlines(keepends=True),
        fromfile=_DIFF_FROM,
        tofile=_DIFF_TO,
    ):
        chunks.append(line)
        if not line.endswith("\n"):
            # Otherwise a trailing-newline-only drift produces a diff that looks
            # like no drift at all.
            chunks.append(f"\n{_NO_NEWLINE_NOTE}\n")
    return "".join(chunks)


def compare(expected: str, actual: str) -> Verdict:
    """Compare a documented transcript against captured stdout.

    Exact string equality, deliberately: blank lines, trailing whitespace and the
    final newline all count. Go offers `// Unordered output:` as an explicit
    opt-in escape hatch rather than a fuzzy default, and a fuzzy default would
    stop catching the thing this exists to catch.

    Pure. Returns ``Match`` or ``Drift``; never ``Unrunnable``, which is a fact
    about the process rather than about the text. Cannot fail.
    """
    if expected == actual:
        return Match()
    return Drift(expected=expected, actual=actual, diff=unified_diff(expected, actual))


def tail(text: str, max_lines: int) -> str:
    """The last ``max_lines`` lines of ``text``. Pure."""
    if max_lines < 1:
        raise ValueError(f"max_lines must be >= 1, got {max_lines}")
    lines = text.splitlines()
    return "\n".join(lines[-max_lines:])


def exit_code(verdict: Verdict) -> int:
    """The process exit code for a verdict: 0 match, 1 drift, 2 unrunnable.

    Pure, total over ``Verdict``. Raises ``TypeError`` on anything else rather
    than defaulting, so a new verdict type cannot silently exit 0.
    """
    if isinstance(verdict, Match):
        return EXIT_MATCH
    if isinstance(verdict, Drift):
        return EXIT_DRIFT
    if isinstance(verdict, Unrunnable):
        return EXIT_UNRUNNABLE
    raise TypeError(f"not a Verdict: {verdict!r}")


def format_verdict(verdict: Verdict, *, subject: str) -> str:
    """Render a verdict for a terminal. Pure; no trailing newline.

    Raises ``TypeError`` on a non-verdict, same reasoning as ``exit_code``.
    """
    if isinstance(verdict, Match):
        return f"MATCH  {subject}\n  documented transcript is byte-identical to stdout"
    if isinstance(verdict, Drift):
        return (
            f"DRIFT  {subject}\n"
            f"  the command ran fine but printed something else\n\n"
            f"{verdict.diff}"
        )
    if isinstance(verdict, Unrunnable):
        stderr = verdict.stderr_tail or "(stderr was empty)"
        indented = "\n".join(f"    {line}" for line in stderr.splitlines())
        return (
            f"UNRUNNABLE  {subject}\n"
            f"  the command exited {verdict.exit_code}; the README was NOT judged\n"
            f"  last {STDERR_TAIL_LINES} lines of stderr:\n{indented}"
        )
    raise TypeError(f"not a Verdict: {verdict!r}")


# --------------------------------------------------------------------------- #
# Imperative shell - reads the README, runs the command
# --------------------------------------------------------------------------- #


def check(
    readme_path: Path,
    command: Sequence[str],
    cwd: Path,
    *,
    marker: str = DEFAULT_MARKER,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Verdict:
    """Run ``command`` in ``cwd`` and judge ``readme_path``'s transcript against it.

    Never writes to ``readme_path``. There is deliberately no update mode: a
    checker that can rewrite the documentation it disagrees with would let a
    genuine regression re-document itself as correct.

    Failure modes: ``ValueError`` if ``command`` is empty; ``TranscriptNotFound``
    / ``AmbiguousTranscript`` from extraction; ``OSError`` (e.g. the README or
    the command binary does not exist) and ``subprocess.TimeoutExpired``
    propagate untouched - a check that could not run is not a finding about the
    README, so it must not be dressed up as one.
    """
    if not command:
        raise ValueError("command must have at least one element")

    expected = extract_transcript(readme_path.read_text(encoding="utf-8"), marker=marker)

    completed = subprocess.run(  # noqa: S603 - the command is the caller's own
        list(command),
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )

    if completed.returncode != 0:
        return Unrunnable(
            exit_code=completed.returncode,
            stderr_tail=tail(completed.stderr, STDERR_TAIL_LINES),
        )

    return compare(expected, completed.stdout)


def parse_args(argv: Sequence[str]) -> tuple[Path, list[str]]:
    """Split ``<example-dir> -- <command...>``. Pure apart from Path construction.

    Failure mode: ``UsageError`` if the separator is missing or either side is
    empty. Positional coupling is kept to exactly one position plus a separator
    on purpose.
    """
    args = list(argv)
    if not args:
        raise UsageError("no arguments given")
    if "--" not in args:
        raise UsageError("missing the '--' separator before the command")

    separator = args.index("--")
    head, command = args[:separator], args[separator + 1 :]

    if len(head) != 1:
        raise UsageError(
            f"expected exactly one example directory before '--', got {len(head)}"
        )
    if not command:
        raise UsageError("no command given after '--'")

    return Path(head[0]), command


def main(argv: Sequence[str]) -> int:
    """Check one README and return the exit code from the verdict table.

    Exit codes: 0 match, 1 drift, 2 unrunnable, 64 bad usage, 65 the README has
    no single verifiable transcript, 70 the check itself could not run.
    """
    try:
        example_dir, command = parse_args(argv)
    except UsageError as exc:
        print(f"error: {exc}\n\n{USAGE}", file=sys.stderr)
        return EXIT_USAGE

    readme_path = example_dir / README_FILENAME
    subject = f"{readme_path} vs `{' '.join(command)}`"

    if not example_dir.is_dir():
        print(f"error: {example_dir} is not a directory", file=sys.stderr)
        return EXIT_INPUT_ERROR
    if not readme_path.is_file():
        print(f"error: {readme_path} does not exist", file=sys.stderr)
        return EXIT_INPUT_ERROR

    try:
        verdict = check(readme_path, command, example_dir)
    except (TranscriptNotFound, AmbiguousTranscript) as exc:
        print(f"UNVERIFIABLE  {subject}\n  {exc}", file=sys.stderr)
        return EXIT_INPUT_ERROR
    except (OSError, subprocess.SubprocessError) as exc:
        # Distinct from every verdict: this says nothing about the README.
        print(
            f"CHECK FAILED  {subject}\n  {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return EXIT_CHECK_ERROR

    print(format_verdict(verdict, subject=subject))
    return exit_code(verdict)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
