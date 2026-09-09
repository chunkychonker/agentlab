"""Check every example README's documented transcript in one pass.

`check_transcript.py` judges one README against one command you supply. That is
the right shape for a human at a terminal and the wrong shape for the nightly
health check, which has nineteen examples to look at and no business guessing
nineteen commands. This driver supplies both halves:

    which examples have a transcript   -> plan_target, via extract_transcript
    what command produces it           -> command_script
    an interpreter that can run it     -> build_interpreter (scratch venv)

The command rule is narrow on purpose: **the last whitespace-delimited `*.py`
token in the fenced block immediately preceding the marked transcript block.**
The READMEs' run blocks are not uniform - `python` vs `python3`, an inline
`pip install`, an inline `python3 -m venv .venv` - but every one of them ends on
the script, so the sweep takes the script and brings its own interpreter rather
than trying to execute the README's shell.

Seven outcomes per example, and only four of them are findings:

    Match / Drift / Unrunnable   check_transcript's verdicts       d,u are findings
    OptOut(reason)               the README declared it unverifiable
    NoTranscript()               the README documents no transcript
    Ambiguous(count)             two or more marked blocks          finding
    CommandMissing()             no *.py token above the transcript finding

`OptOut` and `NoTranscript` are not failures, and the report says so in words:
an example with nothing to check is not an example that is broken.

    Pure core (no I/O):   command_script, plan_target, summarize
    Imperative shell:     build_interpreter, run_sweep, main

See the research note this came from:
    research/2026-09-09-transcript-check-sweep.md

Run it (from this directory), sweeping all of examples/:
    python3 sweep.py

Restrict it, and keep the scratch venvs for a second run:
    python3 sweep.py --only minimal-agent-loop,typed-tool-registry --scratch /tmp/sweep

Run the offline self-test (stdlib only, no key, no network):
    python3 test_sweep.py
"""

from __future__ import annotations

import argparse
import dataclasses
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

import check_transcript as ct

# The opt-out directive lives in check_transcript, which is what actually
# detects it; named here so a reader of the sweep can see the whole vocabulary.
DIRECTIVE_PREFIX = ct.OPT_OUT_DIRECTIVE_PREFIX

# A markdown fence. Opening fences may carry an info string (```bash); a closing
# fence is the bare three characters.
FENCE = "```"

# What makes a token in a run block look like the thing to execute.
SCRIPT_SUFFIX = ".py"

# The interpreter used for examples with no requirements.txt, and to build the
# virtualenvs of the ones that have it.
BARE_INTERPRETER = "python3"

REQUIREMENTS_FILENAME = "requirements.txt"

# Written inside a scratch venv once its dependencies are installed, holding the
# requirements text it was built from. Its presence *and* its contents are the
# reuse key: a venv whose build died halfway has no marker and is rebuilt, and
# so is one whose example has since changed its requirements.
READY_MARKER = ".transcript-sweep-ready"

# Building a virtualenv means a network round-trip to PyPI; a self-test does not.
VENV_BUILD_TIMEOUT_SECONDS = 600

# `.pipeline/health.sh` lifts lines starting with this out of the health report
# and files them as backlog items, so the prefix is a contract, not formatting.
FAIL_PREFIX = "- FAIL  "

# An em dash, spelled as an escape to keep this file ASCII. Matches the
# `- FAIL  examples/<name>/ - <reason>` shape the health report already uses.
FAIL_SEPARATOR = " \u2014 "

# Buckets in the order the counts line names them.
BUCKET_MATCH = "match"
BUCKET_DRIFT = "drift"
BUCKET_UNRUNNABLE = "unrunnable"
BUCKET_OPT_OUT = "opt-out"
BUCKET_NO_TRANSCRIPT = "no-transcript"
BUCKET_ERROR = "error"
BUCKET_ORDER = (
    BUCKET_MATCH,
    BUCKET_DRIFT,
    BUCKET_UNRUNNABLE,
    BUCKET_OPT_OUT,
    BUCKET_NO_TRANSCRIPT,
    BUCKET_ERROR,
)

# The buckets that mean "someone has to look at this".
FINDING_BUCKETS = frozenset({BUCKET_DRIFT, BUCKET_UNRUNNABLE, BUCKET_ERROR})

# Exit codes. 0/1 are the sweep's own answer; 64 and 70 are borrowed from
# check_transcript so "I could not run the sweep" can never read as "clean" and
# never collides with "your README is wrong".
EXIT_CLEAN = 0
EXIT_FINDINGS = 1
EXIT_USAGE = ct.EXIT_USAGE
EXIT_SWEEP_ERROR = ct.EXIT_CHECK_ERROR

# Column widths for the human lines. Presentation only; nothing parses these.
_LABEL_WIDTH = 14
_NAME_WIDTH = 36
_DETAIL_MAX_CHARS = 72

# Directory-name prefixes under examples/ that are not examples.
_NON_EXAMPLE_PREFIXES = (".", "__")


class CommandNotFound(Exception):
    """No self-test command could be read out of the README.

    Either no fenced block precedes the documented transcript, or the one that
    does contains no ``*.py`` token (``mcp-connect-claude-code``'s block is
    `cd ...` + `./run_e2e.sh`). Raised rather than falling back to a guessed
    convention such as "test_<dirname>.py": running the wrong script and
    comparing its output would produce a confident, wrong drift report.
    """


class SelectionError(ValueError):
    """The sweep was pointed at an example directory that does not exist.

    A ValueError, because that is what it is; its own type, so the CLI can turn
    a misspelled ``--only`` into a usage error without also swallowing the
    ValueError a malformed opt-out directive raises from inside a README.
    """


class EnvironmentBuildError(Exception):
    """A scratch virtualenv could not be created, or its dependencies installed.

    Deliberately not a verdict. `Unrunnable` means "this example's own self-test
    exited non-zero", which is a fact about the example; a venv that could not
    be built is a fact about this machine (no network, no ensurepip) and must
    not be filed against the README.
    """


# --------------------------------------------------------------------------- #
# Targets and outcomes
#
# The four non-Checkable targets are outcomes too: once you know a README opts
# out, that *is* the result for it - there is nothing left to run. So run_sweep
# passes them straight through, and only `Checkable` continues to a subprocess.
# --------------------------------------------------------------------------- #


@dataclasses.dataclass(frozen=True)
class Checkable:
    """The README documents a transcript and names the script that produces it."""

    script: str


@dataclasses.dataclass(frozen=True)
class OptOut:
    """The README's transcript carries the skip directive, and says why."""

    reason: str


@dataclasses.dataclass(frozen=True)
class NoTranscript:
    """The README documents no transcript. Not a finding: nothing was claimed."""


@dataclasses.dataclass(frozen=True)
class Ambiguous:
    """The README has more than one marked block; the checker refuses to guess.

    ``count`` comes from `check_transcript.AmbiguousTranscript`, never from a
    second count taken here - two implementations of one rule is how a report
    ends up claiming an incoherent "1 ambiguous block".
    """

    count: int


@dataclasses.dataclass(frozen=True)
class CommandMissing:
    """A transcript is documented but no `*.py` command could be read for it."""


Target = Checkable | OptOut | NoTranscript | Ambiguous | CommandMissing

# `Checkable` is the one target that is not an answer, and `Verdict` is what
# running it produces - so the two unions differ by exactly that swap.
Outcome = ct.Verdict | OptOut | NoTranscript | Ambiguous | CommandMissing


# --------------------------------------------------------------------------- #
# Pure core - no filesystem, no subprocess, no clock
# --------------------------------------------------------------------------- #


def _nearest_non_blank_above(lines: Sequence[str], index: int) -> int:
    """Index of the nearest non-blank line strictly above ``index``, or -1. Pure.

    Deliberately a local copy of check_transcript's rule rather than an import
    of its private helper: this module reads the README to find the *command*,
    which is its own question. `check_transcript` stays the single authority on
    what the transcript is, and `plan_target` always asks it first.
    """
    above = index - 1
    while above >= 0 and not lines[above].strip():
        above -= 1
    return above


def _fenced_blocks(lines: Sequence[str]) -> list[tuple[int, int]]:
    """(opening fence, closing fence) index pairs for every fenced block. Pure.

    A line whose first non-space characters are ``` opens a block; the next line
    that is exactly ``` closes it. A trailing unclosed fence yields no block -
    whether that is an error is check_transcript's call, not this scanner's.
    """
    blocks: list[tuple[int, int]] = []
    opening: int | None = None
    for index, line in enumerate(lines):
        if opening is None:
            if line.lstrip().startswith(FENCE):
                opening = index
        elif line.rstrip() == FENCE:
            blocks.append((opening, index))
            opening = None
    return blocks


def _marked_blocks(
    lines: Sequence[str], blocks: Sequence[tuple[int, int]]
) -> list[tuple[int, int]]:
    """The blocks whose nearest preceding non-blank line contains the marker. Pure."""
    return [
        block
        for block in blocks
        if (above := _nearest_non_blank_above(lines, block[0])) >= 0
        and ct.DEFAULT_MARKER in lines[above]
    ]


def command_script(readme_text: str) -> str:
    """The self-test script whose output the README documents.

    The rule, verified by hand against all fifteen transcript-bearing READMEs in
    this repo: the last whitespace-delimited ``*.py`` token in the fenced block
    immediately preceding the marked transcript block. That token is the script;
    everything before it (``python``, ``python3``, ``.venv/bin/python``) is an
    interpreter the sweep replaces with one it built itself.

    Pure: a function of the text alone. Failure mode: ``CommandNotFound`` when
    the README does not mark exactly one transcript, when no fenced block
    precedes it, or when that block holds no ``*.py`` token.
    """
    lines = readme_text.splitlines()
    blocks = _fenced_blocks(lines)
    marked = _marked_blocks(lines, blocks)

    if len(marked) != 1:
        raise CommandNotFound(
            f"expected exactly one block marked {ct.DEFAULT_MARKER!r}, found "
            f"{len(marked)}; there is no single transcript to find a command for"
        )

    transcript_open = marked[0][0]
    preceding = [block for block in blocks if block[1] < transcript_open]
    if not preceding:
        raise CommandNotFound(
            "no fenced block precedes the documented transcript, so the README "
            "never shows the command that produces it"
        )

    opening, closing = preceding[-1]
    tokens = " ".join(lines[opening + 1 : closing]).split()
    scripts = [token for token in tokens if token.endswith(SCRIPT_SUFFIX)]
    if not scripts:
        raise CommandNotFound(
            f"the block above the transcript (lines {opening + 1}-{closing + 1}) "
            f"contains no {SCRIPT_SUFFIX} token"
        )
    return scripts[-1]


def plan_target(readme_text: str) -> Target:
    """Decide what, if anything, this README asks to be checked.

    `check_transcript` answers first and its answer is final: no block, two
    blocks, or an opt-out are all decided there, so the sweep can never check
    something the single-README checker would have refused.

    Pure. Failure mode: ``ValueError`` from a malformed opt-out directive
    propagates - a directive nobody can parse is an authoring error to fix, not
    an outcome to file against the example.
    """
    try:
        ct.extract_transcript(readme_text)
    except ct.TranscriptNotFound:
        return NoTranscript()
    except ct.AmbiguousTranscript as exc:
        return Ambiguous(count=exc.count)
    except ct.TranscriptOptOut as exc:
        return OptOut(reason=exc.reason)

    try:
        return Checkable(script=command_script(readme_text))
    except CommandNotFound:
        return CommandMissing()


def _short(text: str) -> str:
    """A single quoted line, truncated for a report column. Pure."""
    collapsed = " ".join(text.split())
    if len(collapsed) > _DETAIL_MAX_CHARS:
        collapsed = collapsed[: _DETAIL_MAX_CHARS - 3] + "..."
    return repr(collapsed)


def _drift_detail(drift: ct.Drift) -> str:
    """The first line on which the README and the output disagree. Pure.

    The full diff stays in `check_transcript`: a sweep of nineteen examples that
    printed nineteen diffs would bury the counts line that matters.
    """
    documented = drift.expected.splitlines()
    actual = drift.actual.splitlines()
    for index in range(max(len(documented), len(actual))):
        left = documented[index] if index < len(documented) else "(no line)"
        right = actual[index] if index < len(actual) else "(no line)"
        if left != right:
            return (
                f"line {index + 1}: README has {_short(left)}, "
                f"output has {_short(right)}"
            )
    return "the two differ only in the trailing newline"


def bucket(outcome: Outcome) -> str:
    """Which counts-line bucket an outcome falls in.

    Pure and total over ``Outcome``. Raises ``TypeError`` on anything else, so a
    new outcome type cannot be silently counted as a pass.
    """
    if isinstance(outcome, ct.Match):
        return BUCKET_MATCH
    if isinstance(outcome, ct.Drift):
        return BUCKET_DRIFT
    if isinstance(outcome, ct.Unrunnable):
        return BUCKET_UNRUNNABLE
    if isinstance(outcome, OptOut):
        return BUCKET_OPT_OUT
    if isinstance(outcome, NoTranscript):
        return BUCKET_NO_TRANSCRIPT
    if isinstance(outcome, (Ambiguous, CommandMissing)):
        return BUCKET_ERROR
    raise TypeError(f"not an Outcome: {outcome!r}")


def is_finding(outcome: Outcome) -> bool:
    """Whether an outcome needs a human. Pure; total via ``bucket``."""
    return bucket(outcome) in FINDING_BUCKETS


def _label(outcome: Outcome) -> str:
    """The human line's leading word for an outcome. Pure, total."""
    return {
        BUCKET_MATCH: "MATCH",
        BUCKET_DRIFT: "DRIFT",
        BUCKET_UNRUNNABLE: "UNRUNNABLE",
        BUCKET_OPT_OUT: "OPT-OUT",
        BUCKET_NO_TRANSCRIPT: "NO TRANSCRIPT",
        BUCKET_ERROR: "UNVERIFIABLE",
    }[bucket(outcome)]


def _detail(outcome: Outcome) -> str:
    """One line saying what happened, or "" when the label says it all. Pure, total."""
    if isinstance(outcome, ct.Drift):
        return _drift_detail(outcome)
    if isinstance(outcome, ct.Unrunnable):
        last = outcome.stderr_tail.strip().splitlines()
        tail = _short(last[-1]) if last else "(stderr was empty)"
        return f"the self-test exited {outcome.exit_code}; last stderr line: {tail}"
    if isinstance(outcome, OptOut):
        return outcome.reason
    if isinstance(outcome, Ambiguous):
        return (
            f"{outcome.count} blocks marked {ct.DEFAULT_MARKER!r}; "
            f"the checker refuses to guess which is reproducible"
        )
    if isinstance(outcome, CommandMissing):
        return (
            f"a transcript is documented but the block above it names no "
            f"{SCRIPT_SUFFIX} script to run"
        )
    bucket(outcome)  # Match, or a TypeError for anything that is not an Outcome.
    return ""


def _finding_line(name: str, outcome: Outcome) -> str:
    """The health-report line for a finding. Pure. Caller must check ``is_finding``.

    Self-contained on purpose: the human line above it carries a label column,
    but this line gets lifted out of the report on its own and has to still say
    what kind of finding it is.
    """
    kind = {
        BUCKET_DRIFT: "README transcript drift",
        BUCKET_UNRUNNABLE: "README self-test could not run",
        BUCKET_ERROR: "README transcript unverifiable",
    }[bucket(outcome)]
    return (
        f"{FAIL_PREFIX}examples/{name}/{FAIL_SEPARATOR}{kind}: {_detail(outcome)}"
    )


def summarize(results: Sequence[tuple[str, Outcome]]) -> str:
    """Render a whole sweep: counts, one line per example, then the findings.

    The findings are emitted in `.pipeline/health.sh`'s `- FAIL  ` shape so the
    health agent can lift them verbatim into `## Example results` rather than
    re-wording them, which is where detail goes missing.

    Pure; no trailing newline. Failure mode: ``TypeError`` if ``results``
    contains something that is not an ``Outcome``.
    """
    counts = {name: 0 for name in BUCKET_ORDER}
    for _, outcome in results:
        counts[bucket(outcome)] += 1

    tally = " / ".join(f"{counts[name]} {name}" for name in BUCKET_ORDER)
    lines = [f"Transcripts: {tally} of {len(results)}", ""]

    for name, outcome in results:
        row = f"{_label(outcome):<{_LABEL_WIDTH}}{name:<{_NAME_WIDTH}}{_detail(outcome)}"
        lines.append(row.rstrip())

    findings = [
        _finding_line(name, outcome) for name, outcome in results if is_finding(outcome)
    ]
    lines.append("")
    if findings:
        lines.append(
            f"Findings ({len(findings)}) - copy verbatim into '## Example results':"
        )
        lines.extend(findings)
    else:
        lines.append(
            "No findings: every transcript that could be checked matched its output."
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Imperative shell - builds virtualenvs, runs self-tests
# --------------------------------------------------------------------------- #


def _venv_python(example_dir: Path, scratch_root: Path) -> Path:
    """Where this example's scratch interpreter lives. POSIX layout only."""
    return scratch_root / example_dir.name / "bin" / "python"


def _venv_is_ready(venv_dir: Path, requirements_text: str) -> bool:
    """Whether ``venv_dir`` was already built from exactly these requirements.

    Reads one small file. False for a venv that was never built, one whose build
    died before the marker was written, and one built from different
    requirements - so a stale scratch directory is repaired rather than trusted.
    """
    marker = venv_dir / READY_MARKER
    if not marker.is_file():
        return False
    return marker.read_text(encoding="utf-8") == requirements_text


def _run_or_raise(command: Sequence[str], *, cwd: Path, what: str) -> None:
    """Run a build step, or raise ``EnvironmentBuildError`` with its stderr tail."""
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        list(command),
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=VENV_BUILD_TIMEOUT_SECONDS,
        check=False,
    )
    if completed.returncode != 0:
        raise EnvironmentBuildError(
            f"{what} failed (exit {completed.returncode}):\n"
            f"{ct.tail(completed.stderr, ct.STDERR_TAIL_LINES)}"
        )


def build_interpreter(example_dir: Path, scratch_root: Path) -> list[str]:
    """An argv prefix that can run ``example_dir``'s self-test.

    ``["python3"]`` when the example has no ``requirements.txt``; otherwise the
    python of a virtualenv under ``scratch_root``, created and populated on
    first use. The venv is built outside the repo on purpose - the health check
    must not leave a ``.venv/`` inside an example directory.

    Idempotent per ``(example_dir, scratch_root)``: a venv already built from
    the same requirements is reused as-is, so one sweep pays for one venv per
    example, and a caller who passes ``--scratch`` gets a warm cache on the next
    run. A half-built or stale venv is deleted and rebuilt rather than reused.

    Failure modes: ``EnvironmentBuildError`` if ``python3 -m venv`` or the
    ``pip install`` fails (no network, no ensurepip) - never mapped to a verdict,
    because that would blame the README for this machine.
    ``subprocess.TimeoutExpired`` after ten minutes on either step.
    """
    requirements = example_dir / REQUIREMENTS_FILENAME
    if not requirements.is_file():
        return [BARE_INTERPRETER]

    requirements_text = requirements.read_text(encoding="utf-8")
    python = _venv_python(example_dir, scratch_root)
    venv_dir = python.parent.parent

    if _venv_is_ready(venv_dir, requirements_text):
        return [str(python)]

    if venv_dir.exists():
        shutil.rmtree(venv_dir)
    venv_dir.parent.mkdir(parents=True, exist_ok=True)

    _run_or_raise(
        [BARE_INTERPRETER, "-m", "venv", str(venv_dir)],
        cwd=example_dir,
        what=f"creating a virtualenv for {example_dir.name}",
    )
    _run_or_raise(
        [str(python), "-m", "pip", "install", "-q", "-r", REQUIREMENTS_FILENAME],
        cwd=example_dir,
        what=f"installing {example_dir.name}'s requirements",
    )

    # Last, and only on success: this is what makes the reuse check honest.
    (venv_dir / READY_MARKER).write_text(requirements_text, encoding="utf-8")
    return [str(python)]


def example_dirs(examples_root: Path, only: Sequence[str] | None = None) -> list[Path]:
    """The example directories to sweep, sorted by name.

    Failure modes: ``NotADirectoryError`` if ``examples_root`` is not a
    directory; ``SelectionError`` naming every entry of ``only`` that does not
    exist, rather than sweeping the subset that does and reporting green.
    """
    if not examples_root.is_dir():
        raise NotADirectoryError(f"{examples_root} is not a directory")

    found = sorted(
        path
        for path in examples_root.iterdir()
        if path.is_dir() and not path.name.startswith(_NON_EXAMPLE_PREFIXES)
    )
    if only is None:
        return found

    by_name = {path.name: path for path in found}
    missing = [name for name in only if name not in by_name]
    if missing:
        raise SelectionError(
            f"no such example director{'y' if len(missing) == 1 else 'ies'} under "
            f"{examples_root}: {', '.join(missing)}"
        )
    return [by_name[name] for name in sorted(only)]


def run_sweep(
    examples_root: Path,
    scratch_root: Path,
    *,
    only: Sequence[str] | None = None,
) -> list[tuple[str, Outcome]]:
    """Check every example's documented transcript, in directory-sorted order.

    Writes no README - it only ever calls `check_transcript.check`, which has no
    update mode. Builds virtualenvs under ``scratch_root`` and nowhere else.

    Failure modes: ``FileNotFoundError`` if a directory under ``examples_root``
    has no ``README.md`` (silently skipping it would be a sweep that reports
    green on an example it never looked at); ``EnvironmentBuildError`` and
    ``subprocess.TimeoutExpired`` propagate, as does the ``ValueError`` from a
    malformed opt-out directive. Ambiguity, a missing command, and a failing
    self-test are *reported*, not raised: they are results about a README.
    """
    results: list[tuple[str, Outcome]] = []

    for example_dir in example_dirs(examples_root, only):
        readme = example_dir / ct.README_FILENAME
        try:
            target = plan_target(readme.read_text(encoding="utf-8"))
        except ValueError as exc:
            # A malformed opt-out directive. Loud, and now says where.
            raise ValueError(f"{readme}: {exc}") from exc

        if isinstance(target, Checkable):
            interpreter = build_interpreter(example_dir, scratch_root)
            outcome: Outcome = ct.check(
                readme, [*interpreter, target.script], example_dir
            )
        else:
            # Every other target is already the answer for this example.
            outcome = target

        results.append((example_dir.name, outcome))

    return results


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    """Parse the sweep's flags. Exits 2 via argparse on a bad command line."""
    parser = argparse.ArgumentParser(
        prog="sweep.py",
        description="Check every example README's documented transcript.",
    )
    parser.add_argument(
        "--examples-root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="directory holding the example directories (default: ../)",
    )
    parser.add_argument(
        "--scratch",
        type=Path,
        default=None,
        help=(
            "where to build virtualenvs; kept after the run so a second sweep "
            "is fast (default: a temporary directory, deleted on exit)"
        ),
    )
    parser.add_argument(
        "--only",
        default=None,
        help="comma-separated example directory names to sweep instead of all",
    )
    return parser.parse_args(list(argv))


def main(argv: Sequence[str]) -> int:
    """Sweep, print the report, and return 0 when it holds no findings.

    Exit codes: 0 no findings, 1 at least one `- FAIL  ` line, 64 the flags name
    something that does not exist, 70 the sweep itself could not run (a
    virtualenv could not be built). A traceback means the sweep did not finish
    and no report should be trusted. 64 and 70 are kept off 1 so a caller can
    never read "I could not run" as "your README is wrong".
    """
    args = _parse_args(argv)
    only = args.only.split(",") if args.only else None

    scratch_is_ours = args.scratch is None
    scratch = (
        Path(tempfile.mkdtemp(prefix="transcript-sweep-"))
        if scratch_is_ours
        else args.scratch
    )

    try:
        results = run_sweep(args.examples_root, scratch, only=only)
    except (SelectionError, NotADirectoryError) as exc:
        # A misspelled --only or --examples-root. Sweeping the subset that does
        # exist and reporting green would be the worst possible answer.
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except EnvironmentBuildError as exc:
        print(f"SWEEP FAILED  {exc}", file=sys.stderr)
        return EXIT_SWEEP_ERROR
    finally:
        if scratch_is_ours:
            shutil.rmtree(scratch, ignore_errors=True)

    print(summarize(results))
    return EXIT_FINDINGS if any(is_finding(o) for _, o in results) else EXIT_CLEAN


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
