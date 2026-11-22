"""The ``nightloop`` command: ``run``, ``run --dry-run``, ``report`` and ``schedule``.

The imperative shell. This is the only nightloop module that reads argv,
the clock or the working directory, and the only one that builds the
production runner, workspace and shipper. Everything it decides with is
pure (``window``, ``plan``, ``summary``, ``night_text``). Every effect it
uses arrives as a keyword argument with a production default, so tests drive
``main`` with fakes and no model, git remote or ``gh`` is ever called.
``schedule`` is delegated to ``schedule_cmd``, with the host facts and the
``launchctl`` factory passed in the same way.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Sequence

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
from .exit_codes import ExitCode
from .metrics_file import MetricsFileError, append_sink, read_lines
from .night_text import render_night
from .plan import plan_night, render_plan
from .schedule_args import add_subparser
from .summary import render_report, summarise
from .window import Refuse, window_disposition

if TYPE_CHECKING:
    from .schedule_cmd import Host, LaunchctlFactory

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
    host_factory: Callable[[], Host] | None = None,
    launchctl_factory: LaunchctlFactory | None = None,
) -> int:
    """Run the ``nightloop`` command line ``argv`` (default ``sys.argv[1:]``) and return an ``ExitCode``.

    ``run`` loads the config and reads ``clock`` once, for both the window
    check and the run id. Outside the window it prints the refusal to stderr
    and returns ``REFUSED`` without building a runner, workspace or shipper.
    ``run --dry-run`` prints the plan and returns ``OK`` whatever the hour.
    It builds no runner, workspace or shipper and writes nothing.
    ``report`` reads the metrics file and prints the summary; it writes
    nothing. ``schedule install|uninstall|status`` calls ``host_factory``
    (default ``schedule_cmd.read_host``) once and returns what
    ``run_schedule`` returns (see there); ``launchctl_factory`` defaults to
    ``SubprocessLaunchctl``. The scheduler is imported only then, so ``run``
    and ``report`` never load it or ``plistlib``.

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
    if args.command == "schedule":
        return _schedule(args, host_factory, launchctl_factory)
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


def _schedule(
    args: argparse.Namespace,
    host_factory: Callable[[], Host] | None,
    launchctl_factory: LaunchctlFactory | None,
) -> int:
    # Imported here, not at the top: a Python whose plistlib cannot load
    # (a broken pyexpat) must still be able to run a night.
    from ..schedule.launchctl import SubprocessLaunchctl
    from .schedule_cmd import read_host, run_schedule

    host = (host_factory or read_host)()
    return run_schedule(args, host=host, launchctl_factory=launchctl_factory or SubprocessLaunchctl)


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
    commands = parser.add_subparsers(dest="command", required=True, metavar="{run,report,schedule}")

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
    add_subparser(commands, config_name=CONFIG_NAME)
    return parser
