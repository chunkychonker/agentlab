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

A README whose only documented transcript cannot be reproduced offline - a
billed live-API run, say - opts out with a directive on the line above the
marker, and extraction raises `TranscriptOptOut` instead of handing back a
block that was never verifiable:

    <!-- transcript-check: skip - billed live-API run, not reproducible offline -->
    Expected output (verified during this build):

This is Go's rule again: an example with no `// Output:` comment is compiled but
not run. "Nothing to verify here, and here is why" is a legitimate third answer,
distinct from both "matches" and "no transcript at all".

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

# An HTML comment on the line above the marker declares the block unverifiable.
# HTML comment, so it renders as nothing on GitHub - the README is unchanged for
# a human reader and machine-readable for this checker.
OPT_OUT_DIRECTIVE_PREFIX = "<!-- transcript-check: skip"

# Closing an HTML comment. A directive that does not close on its own line is a
# malformed directive, not a licence to guess where the reason ends.
_HTML_COMMENT_CLOSE = "-->"

# Punctuation a human would naturally put between "skip" and the reason:
# a colon, a hyphen, an em dash (—, spelled as an escape to keep this file
# ASCII), whitespace. Stripped so the stored reason is the sentence itself,
# whichever separator the author chose.
_REASON_LEAD_CHARS = ":-\u2014 \t"

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

    ``count`` is how many were found, so a caller reporting on many READMEs at
    once can say how bad it is without re-deriving the rule and disagreeing.
    It is never below 2: one block is not ambiguous, so the constructor rejects
    it rather than letting an impossible count be reported.
    """

    def __init__(self, message: str, *, count: int) -> None:
        if count < 2:
            raise ValueError(f"ambiguity needs at least 2 blocks, got {count}")
        super().__init__(message)
        self.count = count


class TranscriptOptOut(Exception):
    """The README's one marked block declares itself unverifiable, and says why.

    Raised when the line above the marker carries the
    ``<!-- transcript-check: skip ... -->`` directive. Distinct from
    ``TranscriptNotFound``: the block exists and is documentation the author
    stands behind, it just cannot be reproduced by running a command here (a
    billed live-API run, output that depends on ambient auth, and so on).
    Treating it as a check that passed would be a lie; treating it as a failure
    would be a false accusation. It is its own answer.

    ``reason`` is the author's explanation, and is never empty: a directive
    that skips a check without saying why is exactly the thing that later
    becomes unexplained. The constructor raises ``ValueError`` on a blank one.
    """

    def __init__(self, reason: str) -> None:
        if not reason.strip():
            raise ValueError(
                f"a {OPT_OUT_DIRECTIVE_PREFIX!r} directive must carry a reason, "
                f"e.g. '{OPT_OUT_DIRECTIVE_PREFIX} - billed live-API run "
                f"{_HTML_COMMENT_CLOSE}'"
            )
        super().__init__(f"transcript check skipped: {reason}")
        self.reason = reason


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


def _nearest_non_blank_above(lines: Sequence[str], index: int) -> int:
    """The index of the nearest non-blank line strictly above ``index``, or -1.

    Pure, total. The one place this repo's "a line applies to the thing under
    it" convention is implemented, so the marker rule and the opt-out rule
    cannot drift apart.
    """
    above = index - 1
    while above >= 0 and not lines[above].strip():
        above -= 1
    return above


def _opt_out_reason(lines: Sequence[str], fence_index: int) -> str | None:
    """The reason from a skip directive above the marker line, or ``None``.

    Layout, tightest first: the fence, its marker line, and the directive above
    that. Putting the directive above the marker rather than inside it keeps
    this check orthogonal to marker matching - a README with no directive takes
    exactly the path it took before this existed.

    Pure. Failure mode: ``ValueError`` if a directive line does not close its
    HTML comment on the same line, since the end of the reason would otherwise
    be a guess.
    """
    marker_index = _nearest_non_blank_above(lines, fence_index)
    if marker_index < 0:
        return None

    directive_index = _nearest_non_blank_above(lines, marker_index)
    if directive_index < 0:
        return None

    directive = lines[directive_index].strip()
    if not directive.startswith(OPT_OUT_DIRECTIVE_PREFIX):
        return None
    if not directive.endswith(_HTML_COMMENT_CLOSE):
        raise ValueError(
            f"the {OPT_OUT_DIRECTIVE_PREFIX!r} directive on line "
            f"{directive_index + 1} does not close with "
            f"{_HTML_COMMENT_CLOSE!r} on the same line"
        )

    body = directive[len(OPT_OUT_DIRECTIVE_PREFIX) : -len(_HTML_COMMENT_CLOSE)]
    return body.strip().lstrip(_REASON_LEAD_CHARS).strip()


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

        preceding = _nearest_non_blank_above(lines, index)
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
    if more than one marked block exists; ``TranscriptOptOut`` if the one marked
    block carries the skip directive above its marker line - checked last, so a
    README that is ambiguous *and* opted out is still reported as ambiguous
    rather than quietly excused; ``ValueError`` for a malformed directive (no
    closing ``-->``, or no reason). Never returns "" for a missing block - ""
    is returned only for a block that genuinely documents empty output.
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
            f"is the reproducible transcript",
            count=len(bounds),
        )

    start, end = bounds[0]

    # `start` is the first content line, so `start - 1` is the opening fence.
    reason = _opt_out_reason(lines, start - 1)
    if reason is not None:
        raise TranscriptOptOut(reason)

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
    / ``AmbiguousTranscript`` / ``TranscriptOptOut`` from extraction, all raised
    before the command runs; ``OSError`` (e.g. the README or the command binary
    does not exist) and ``subprocess.TimeoutExpired`` propagate untouched - a
    check that could not run is not a finding about the README, so it must not
    be dressed up as one.
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
    no single verifiable transcript (none, two, or one that opts out), 70 the
    check itself could not run.
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
    except TranscriptOptOut as exc:
        # Not a verdict and not a fault: the README said, in advance, that this
        # block is not reproducible here. Exit 65 with the author's reason
        # rather than 0, which would claim a check that never happened.
        print(f"OPT-OUT  {subject}\n  {exc.reason}", file=sys.stderr)
        return EXIT_INPUT_ERROR
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
