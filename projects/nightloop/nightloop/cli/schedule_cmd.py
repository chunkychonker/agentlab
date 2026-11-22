"""The ``nightloop schedule install|uninstall|status`` subcommand: the scheduler's imperative shell.

Reads the installed plist and the job's state, asks the pure planner what
to do, prints it, and hands the plan to ``nightloop.schedule.apply``.
Everything about the host (platform, home, uid, PATH, interpreter) arrives
as one ``Host`` value that ``main`` reads, so tests drive this with a temp
home and a fake ``Launchctl``; no test touches real launchd. ``main``
imports this module only for ``schedule`` (see ``schedule_args``).
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

from ..config import ConfigError, load_config
from ..model import Window
from ..schedule.apply import apply
from ..schedule.errors import ScheduleError
from ..schedule.job import LaunchdJob, default_label, domain_for, nightly_job, plist_path, slot_for
from ..schedule.plan import Step, plan_install, plan_uninstall
from ..schedule.plist import parse_plist, render_plist
from ..schedule.ports import Launchctl
from .exit_codes import ExitCode
from .main import CONFIG_NAME, PROG
from .schedule_text import advice_text, describe_job, describe_plan, describe_window
from .window import Run, window_disposition

MACOS_PLATFORM = "darwin"
# How a scheduled run starts nightloop unless --command names a script.
MODULE_ARGS = ("-m", "nightloop")

LaunchctlFactory = Callable[[], Launchctl]


@dataclass(frozen=True)
class Host:
    """The facts about this machine that the schedule depends on, read once at the entry point."""

    platform: str
    home: Path
    uid: int
    path_env: str | None
    executable: str


def read_host() -> Host:
    """This process's platform, home, uid, ``PATH`` and interpreter. POSIX only (``os.getuid``)."""
    return Host(
        platform=sys.platform,
        home=Path.home(),
        uid=os.getuid(),
        path_env=os.environ.get("PATH"),
        executable=sys.executable,
    )


def run_schedule(args: argparse.Namespace, *, host: Host, launchctl_factory: LaunchctlFactory) -> int:
    """Run one ``schedule`` action and return an ``ExitCode``.

    Refuses with ``USAGE`` on a non-mac before building a ``Launchctl``.
    ``USAGE`` also for a bad config, an unset ``PATH``, an hour outside the
    window or any other value ``LaunchdJob`` rejects. ``JOB_RUNNING`` when
    the job is running (nothing changed). ``ENVIRONMENT`` for a
    ``ScheduleError`` from launchd or a write, and for ``status`` finding a
    broken job. ``--dry-run`` reads the job state but calls no mutating
    ``launchctl`` command and writes nothing. ``OSError`` from starting
    ``launchctl``/``plutil`` propagates, as does an ``OSError`` reading an
    installed plist in ``uninstall`` (it exists but cannot be read).
    """
    if host.platform != MACOS_PLATFORM:
        _error(f"schedule needs macOS launchd; this platform is {host.platform!r}")
        return ExitCode.USAGE
    repo = args.repo.resolve()
    label = args.label if args.label is not None else default_label(PurePosixPath(repo))
    try:
        domain = domain_for(host.uid)
        plist = Path(plist_path(PurePosixPath(host.home), label))
    except ScheduleError as exc:
        _error(str(exc))
        return ExitCode.USAGE
    if args.schedule_action == "uninstall":
        return _uninstall(label=label, plist=plist, domain=domain, dry_run=args.dry_run,
                          launchctl_factory=launchctl_factory)
    config = (args.config if args.config is not None else repo / CONFIG_NAME).resolve()
    try:
        cfg = load_config(config)
    except ConfigError as exc:
        _error(f"config {config}: {exc}")
        return ExitCode.USAGE
    if args.schedule_action == "status":
        return _status(label=label, plist=plist, domain=domain, window=cfg.window,
                       launchctl_factory=launchctl_factory)
    try:
        job = _job(args, host=host, repo=repo, config=config, label=label, window=cfg.window)
    except ScheduleError as exc:
        _error(str(exc))
        return ExitCode.USAGE
    return _install(job=job, plist=plist, domain=domain, dry_run=args.dry_run,
                    launchctl_factory=launchctl_factory)


def _job(args: argparse.Namespace, *, host: Host, repo: Path, config: Path, label: str, window: Window) -> LaunchdJob:
    """The desired job. Raises ``ScheduleError`` for an unset PATH, a relative --command or a bad value."""
    if not host.path_env:
        raise ScheduleError("PATH is not set; the job needs it to find claude, git and gh")
    if args.program is not None:
        if not args.program.is_absolute():
            raise ScheduleError(f"--command: must be an absolute path, got {str(args.program)!r}")
        command: tuple[str, ...] = (str(args.program),)
    else:
        command = (host.executable, *MODULE_ARGS)
    hour, minute = slot_for(window, args.hour, args.minute)
    return nightly_job(label=label, command=command, repo=PurePosixPath(repo),
                       config_path=PurePosixPath(config), path_env=host.path_env,
                       hour=hour, minute=minute)


def _install(*, job: LaunchdJob, plist: Path, domain: str, dry_run: bool,
             launchctl_factory: LaunchctlFactory) -> int:
    desired = render_plist(job)
    launchctl = launchctl_factory()
    try:
        state = launchctl.state(domain, job.label)
    except ScheduleError as exc:
        _error(str(exc))
        return ExitCode.ENVIRONMENT
    steps = plan_install(desired, _read_installed(plist), state)
    print(f"{plist}: {describe_job(job)}")
    print(f"launchd: {state.value}; {describe_plan(steps)}")
    if dry_run:
        print(desired.decode("utf-8"), end="")
        print("dry run: nothing was changed")
        return ExitCode.OK
    if Step.REFUSE_RUNNING in steps:
        _error(f"{job.label} is running right now; rerun after it ends. Nothing was changed.")
        return ExitCode.JOB_RUNNING
    try:
        apply(steps, plist_path=plist, desired=desired, launchctl=launchctl, domain=domain,
              label=job.label, log_dirs=tuple(dict.fromkeys((Path(job.out_log.parent), Path(job.err_log.parent)))))
    except ScheduleError as exc:
        _error(str(exc))
        return ExitCode.ENVIRONMENT
    if steps == (Step.NOTHING,):
        print("nothing to do: the job is already installed and loaded")
    else:
        print(f"installed and loaded {job.label}")
    print(advice_text(job))
    return ExitCode.OK


def _uninstall(*, label: str, plist: Path, domain: str, dry_run: bool,
               launchctl_factory: LaunchctlFactory) -> int:
    launchctl = launchctl_factory()
    try:
        state = launchctl.state(domain, label)
    except ScheduleError as exc:
        _error(str(exc))
        return ExitCode.ENVIRONMENT
    installed = plist.read_bytes() if plist.exists() else None
    steps = plan_uninstall(installed, state)
    print(f"{plist}: {'installed' if installed is not None else 'not installed'}")
    print(f"launchd: {state.value}; {describe_plan(steps)}")
    if dry_run:
        print("dry run: nothing was changed")
        return ExitCode.OK
    if Step.REFUSE_RUNNING in steps:
        _error(f"{label} is running right now; rerun after it ends. Nothing was changed.")
        return ExitCode.JOB_RUNNING
    try:
        apply(steps, plist_path=plist, desired=b"", launchctl=launchctl, domain=domain,
              label=label, log_dirs=())
    except ScheduleError as exc:
        _error(str(exc))
        return ExitCode.ENVIRONMENT
    print("nothing to do: no job installed or loaded" if steps == (Step.NOTHING,) else f"removed {label}")
    return ExitCode.OK


def _status(*, label: str, plist: Path, domain: str, window: Window, launchctl_factory: LaunchctlFactory) -> int:
    launchctl = launchctl_factory()
    try:
        state = launchctl.state(domain, label)
    except ScheduleError as exc:
        _error(str(exc))
        return ExitCode.ENVIRONMENT
    print(f"label: {label}")
    print(f"launchd: {state.value}")
    if not plist.exists():
        print(f"{plist}: not installed")
        return ExitCode.OK
    try:
        job = parse_plist(plist.read_bytes())
    except (OSError, ScheduleError) as exc:
        _error(f"WARNING: {plist}: {exc}")
        return ExitCode.ENVIRONMENT
    print(f"{plist}: {describe_job(job)}")
    inside = isinstance(window_disposition(job.hour, window, ignore_window=False), Run)
    healthy = inside
    (print if inside else _error)(describe_window(job, window, inside=inside))
    if not Path(job.argv[0]).exists():
        healthy = False
        _error(f"WARNING: the job's program {job.argv[0]} does not exist (a moved or deleted venv?); "
               f"every scheduled run will fail. Rerun `nightloop schedule install`.")
    return ExitCode.OK if healthy else ExitCode.ENVIRONMENT


def _read_installed(plist: Path) -> bytes | None:
    """The installed plist's bytes, or ``None`` if it is absent or cannot be read (treated as "differs")."""
    try:
        return plist.read_bytes()
    except OSError:
        return None


def _error(message: str) -> None:
    print(f"{PROG}: {message}", file=sys.stderr)


__all__ = ["Host", "LaunchctlFactory", "read_host", "run_schedule"]
