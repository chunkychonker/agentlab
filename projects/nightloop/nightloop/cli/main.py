"""The ``nightloop`` command: ``run``, ``run --dry-run`` and ``report``.

The imperative shell. This is the only nightloop module that reads argv,
the clock or the working directory, and the only one that builds the
production runner, workspace and shipper. Everything it decides with is
pure (``window``, ``plan``, ``summary``, ``night_text``). Every effect it
uses arrives as a keyword argument with a production default, so tests drive
``main`` with fakes and no model, git remote or ``gh`` is ever called.
"""

from __future__ import annotations

import argparse
import enum
import sys
import time
from pathlib import Path
from typing import Callable, Sequence

from ..config import ConfigError, load_config
from ..cycle.night import run_night
from ..cycle.ports import Shipper, Workspace
from ..cycle.git_workspace import GitWorkspace
from ..cycle.run_id import RunId
from ..gates.dispatch import evaluate
from ..phase.execute import Gate
from ..phase.runner import Runner
from ..phase.subprocess_runner import SubprocessRunner
from ..ship.gh_shipper import GhShipper
from .metrics_file import MetricsFileError, append_sink, read_lines
from .night_text import render_night
from .plan import plan_night, render_plan
from .summary import render_report, summarise
from .window import Refuse, window_disposition

PROG = "nightloop"
CONFIG_NAME = "nightloop.toml"
METRICS_RELATIVE = Path("logs") / "metrics.jsonl"
# The review verdict file: what clear_handoff deletes, the review gate reads,
# and the shipper quotes. GhShipper's documented production wiring uses it.
VERDICT_PATH = Path("logs") / "last-review.md"
# Each phase's wall-clock limit unless --timeout-s says otherwise. The CLI's
# policy (decision 2026-11-11 left it here); bash has no phase timeout.
DEFAULT_TIMEOUT_S = 3600
# Nights the report covers by default, as in agentlab's metrics_report.sh.
DEFAULT_NIGHTS = 7
_RUN_ID_FORMAT = "%Y-%m-%d_%H%M%S"


class ExitCode(enum.IntEnum):
    """What ``main`` returns. argparse's own usage errors also exit 2."""

    OK = 0  # the night ran (shipped or not; the metrics say how), or the plan/report printed
    ENVIRONMENT = 1  # a program the night needs (claude, git, gh) could not be started
    USAGE = 2  # bad arguments, bad config, or --repo is not a checkout's top level
    REFUSED = 3  # outside the run window, or the clock could not be read
    USAGE_LIMITED = 4  # the night stopped early on a Claude usage limit
    NO_METRICS = 5  # report: the metrics file is missing or holds no usable record


RunnerFactory = Callable[[], Runner]
WorkspaceFactory = Callable[[Path], Workspace]
ShipperFactory = Callable[[Path, Path], Shipper]


def _git_workspace(repo: Path) -> Workspace:
    return GitWorkspace(repo, now=time.time)


def _gh_shipper(repo: Path, verdict_path: Path) -> Shipper:
    return GhShipper(repo, verdict_path, sleep=time.sleep)


def main(
    argv: Sequence[str] | None = None,
    *,
    clock: Callable[[], float] = time.time,
    runner_factory: RunnerFactory = SubprocessRunner,
    workspace_factory: WorkspaceFactory = _git_workspace,
    shipper_factory: ShipperFactory = _gh_shipper,
    gate: Gate = evaluate,
) -> int:
    """Run the ``nightloop`` command line ``argv`` (default ``sys.argv[1:]``) and return an ``ExitCode``.

    ``run`` loads the config and reads ``clock`` once, for both the window
    check and the run id. Outside the window it prints the refusal to stderr
    and returns ``REFUSED`` without building a runner, workspace or shipper.
    ``run --dry-run`` prints the plan and returns ``OK`` whatever the hour.
    It builds no runner, workspace or shipper and writes nothing.
    ``report`` reads the metrics file and prints the summary; it writes
    nothing.

    Failure modes: bad arguments or config, or a ``--repo`` the workspace or
    shipper rejects, print one line and return ``USAGE``. ``OSError`` from
    the night (``claude``/``git``/``gh`` not on PATH) prints the error and
    returns ``ENVIRONMENT``; recovery has already run for the cycle it hit.
    Any other exception from the night (for example ``RecoveryError``)
    propagates with its traceback.
    """
    parser = _parser()
    try:
        args = parser.parse_args(sys.argv[1:] if argv is None else list(argv))
    except SystemExit as stop:  # argparse exits on --help and on usage errors
        return stop.code if isinstance(stop.code, int) else ExitCode.USAGE
    repo = args.repo.resolve()
    metrics = args.metrics if args.metrics is not None else repo / METRICS_RELATIVE
    if args.command == "report":
        return _report(metrics, args.nights)
    config = args.config if args.config is not None else repo / CONFIG_NAME
    return _run(
        repo=repo,
        config=config,
        metrics=metrics,
        timeout_s=args.timeout_s,
        dry_run=args.dry_run,
        ignore_window=args.ignore_window,
        clock=clock,
        runner_factory=runner_factory,
        workspace_factory=workspace_factory,
        shipper_factory=shipper_factory,
        gate=gate,
    )


def _run(
    *,
    repo: Path,
    config: Path,
    metrics: Path,
    timeout_s: int,
    dry_run: bool,
    ignore_window: bool,
    clock: Callable[[], float],
    runner_factory: RunnerFactory,
    workspace_factory: WorkspaceFactory,
    shipper_factory: ShipperFactory,
    gate: Gate,
) -> int:
    try:
        cfg = load_config(config)
    except ConfigError as exc:
        _error(f"config {config}: {exc}")
        return ExitCode.USAGE
    started = _local_time(clock())
    hour = None if started is None else started.tm_hour
    disposition = window_disposition(hour, cfg.window, ignore_window=ignore_window)
    if dry_run:
        plan = plan_night(cfg, repo=repo, verdict_path=VERDICT_PATH, timeout_s=timeout_s)
        print(render_plan(plan, disposition))
        return ExitCode.OK
    if isinstance(disposition, Refuse) or started is None:
        _error(f"refusing to start: {disposition.reason}")
        return ExitCode.REFUSED
    try:
        workspace = workspace_factory(repo)
        shipper = shipper_factory(repo, VERDICT_PATH)
    except ValueError as exc:
        _error(f"--repo {repo}: {exc}")
        return ExitCode.USAGE
    try:
        night = run_night(
            cfg,
            run_id=RunId(time.strftime(_RUN_ID_FORMAT, started)),
            repo=repo,
            verdict_path=VERDICT_PATH,
            timeout_s=timeout_s,
            runner=runner_factory(),
            gate=gate,
            workspace=workspace,
            shipper=shipper,
            now=clock,
            metrics_sink=append_sink(metrics),
        )
    except OSError as exc:
        _error(f"environment error, night stopped: {exc}")
        return ExitCode.ENVIRONMENT
    print(render_night(night))
    return ExitCode.USAGE_LIMITED if night.usage_limited else ExitCode.OK


def _report(metrics: Path, nights: int) -> int:
    try:
        lines = read_lines(metrics)
    except MetricsFileError as exc:
        _error(str(exc))
        return ExitCode.NO_METRICS
    report = summarise(lines, nights)
    if report.nights_found == 0:
        _error(
            f"no usable metrics records in {metrics} "
            f"({report.skipped_lines} unreadable line(s) skipped)"
        )
        return ExitCode.NO_METRICS
    print(render_report(report))
    return ExitCode.OK


def _local_time(seconds: float) -> time.struct_time | None:
    """Local time for ``seconds``, or ``None`` if the clock gave something that is not a time."""
    try:
        return time.localtime(seconds)
    except (OverflowError, OSError, ValueError, TypeError):
        return None


def _error(message: str) -> None:
    print(f"{PROG}: {message}", file=sys.stderr)


def _positive_int(text: str) -> int:
    try:
        value = int(text, 10)
    except ValueError:
        raise argparse.ArgumentTypeError(f"must be a positive integer, got {text!r}") from None
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be a positive integer, got {text!r}")
    return value


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--repo", type=Path, default=Path("."),
                        help="the checkout's top level (default: the current directory)")
    common.add_argument("--metrics", type=Path, default=None,
                        help=f"metrics file (default: <repo>/{METRICS_RELATIVE})")

    parser = argparse.ArgumentParser(prog=PROG, description="A gated overnight loop for a git repo.")
    commands = parser.add_subparsers(dest="command", required=True, metavar="{run,report}")

    run = commands.add_parser("run", parents=[common], help="run one night, if the clock is in the window")
    run.add_argument("--config", type=Path, default=None,
                     help=f"config file (default: <repo>/{CONFIG_NAME})")
    run.add_argument("--timeout-s", type=_positive_int, default=DEFAULT_TIMEOUT_S,
                     help=f"each phase's wall-clock limit in seconds (default: {DEFAULT_TIMEOUT_S})")
    run.add_argument("--dry-run", action="store_true",
                     help="print the night's plan; call no model and touch no git")
    run.add_argument("--ignore-window", action="store_true",
                     help="run even outside the configured window")

    report = commands.add_parser("report", parents=[common], help="summarise the metrics file")
    report.add_argument("--nights", type=_positive_int, default=DEFAULT_NIGHTS,
                        help=f"how many of the latest nights to cover (default: {DEFAULT_NIGHTS})")
    return parser
