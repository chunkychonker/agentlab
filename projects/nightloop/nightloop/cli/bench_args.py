"""Declares the ``nightloop bench`` arguments.

Kept apart from ``bench_cmd`` so ``main`` builds its parser without
importing the benchmark (the m9/m10 lazy-import rule). No I/O; argparse
only. The defaults point into nightloop's own source tree, because the
benchmark measures nightloop's plugin reviewer; an installed copy without
that tree must pass the paths explicitly.
"""

from __future__ import annotations

import argparse
from pathlib import Path

COMMAND = "bench"
# projects/nightloop/, two levels above this file's package.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FIXTURES = PROJECT_ROOT / "bench" / "fixtures"
DEFAULT_TEMPLATE = PROJECT_ROOT / "plugin" / "agents" / "reviewer.md"
DEFAULT_CONFIG = PROJECT_ROOT / "examples" / "plugin.nightloop.toml"
DEFAULT_RESULTS = PROJECT_ROOT / "bench" / "last-run.json"
# The plugin's bin/ (run_silent), put first on PATH for the review phase.
PLUGIN_BIN = PROJECT_ROOT / "plugin" / "bin"
# A guess (research note, open questions); the owner should set it.
DEFAULT_TRIALS = 3


def _positive_int(text: str) -> int:
    try:
        value = int(text, 10)
    except ValueError:
        raise argparse.ArgumentTypeError(f"must be a positive integer, got {text!r}") from None
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be a positive integer, got {text!r}")
    return value


def add_bench_subparser(commands: argparse._SubParsersAction, *, default_timeout_s: int) -> None:
    """Add ``bench`` to ``commands``; the parsed namespace has ``command == "bench"``."""
    bench = commands.add_parser(
        COMMAND,
        help="measure the reviewer template's catch rate and false-positive rate (billed)",
        description="Run the review phase on every fixture, --trials times each, in throwaway "
                    "git repos, and report the catch rate beside the false-positive rate. "
                    "Each trial is one real review call; use --dry-run first.",
    )
    bench.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES,
                       help=f"fixtures dir (default: {DEFAULT_FIXTURES})")
    bench.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE,
                       help=f"reviewer agent template under test (default: {DEFAULT_TEMPLATE})")
    bench.add_argument("--config", type=Path, default=DEFAULT_CONFIG,
                       help=f"config whose review phase is run (default: {DEFAULT_CONFIG})")
    bench.add_argument("--results", type=Path, default=DEFAULT_RESULTS,
                       help=f"results JSON written after a fully graded run (default: {DEFAULT_RESULTS})")
    bench.add_argument("--trials", type=_positive_int, default=DEFAULT_TRIALS,
                       help=f"trials per fixture (default: {DEFAULT_TRIALS})")
    bench.add_argument("--timeout-s", type=_positive_int, default=default_timeout_s,
                       help=f"each review call's wall-clock limit in seconds (default: {default_timeout_s})")
    # Validated by the same rule as the config's own model (nightloop.model.Phase),
    # in bench_cmd, so a bad name is a USAGE error with nothing run.
    bench.add_argument("--model", type=str, default=None,
                       help="run the review phase on this model instead of the one --config names "
                            "(e.g. sonnet, opus, claude-opus-5-5); recorded in the results file")
    mode = bench.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="print the matrix and one trial's argv; run nothing, write nothing")
    mode.add_argument("--check-fresh", action="store_true",
                      help="exit 0 only if the results file was recorded for the current template")
