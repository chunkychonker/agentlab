"""The ``nightloop schedule-gha`` subcommand: the workflow generator's imperative shell.

Reads the config, checks the zone exists on this machine, asks the pure
generator for the text, and either prints it (``--dry-run``) or hands it to
``install_workflow``. ``main`` imports this module only for ``schedule-gha``.
Nothing here talks to GitHub.
"""

from __future__ import annotations

import argparse
import sys
import zoneinfo
from pathlib import Path

from ..config import ConfigError, load_config
from ..schedule_gha.budget import MAX_JOB_MINUTES
from ..schedule_gha.errors import SpecError
from ..schedule_gha.render import render_workflow
from ..schedule_gha.spec import Credential, WorkflowSpec, validate_spec
from ..schedule_gha.write import Written, install_workflow
from .exit_codes import ExitCode
from .gha_args import CREDENTIAL_CHOICES
from .gha_text import manual_steps
from .main import CONFIG_NAME, PROG

WORKFLOWS_DIR = Path(".github") / "workflows"
_CREDENTIALS = dict(zip(CREDENTIAL_CHOICES, (Credential.OAUTH_TOKEN, Credential.API_KEY)))


def workflow_path(repo: Path, workflow_name: str) -> Path:
    """``<repo>/.github/workflows/<workflow_name>.yml``."""
    return repo / WORKFLOWS_DIR / f"{workflow_name}.yml"


def run_schedule_gha(args: argparse.Namespace) -> int:
    """Run ``schedule-gha`` and return an ``ExitCode``.

    ``USAGE`` (one line on stderr, nothing written) for: a config outside
    ``--repo`` (the runner sees only the repo), a config that does not load,
    any ``SpecError`` (bad value, hour outside the window, a night that does
    not fit the job's time limit), or a zone this machine's tz database does
    not know. ``--dry-run`` prints the workflow to stdout and the manual
    steps to stderr, and writes nothing. Otherwise the file is written (or
    left alone if identical) and ``OK`` returned. ``OSError`` from the
    write propagates.
    """
    repo = args.repo.resolve()
    config = (args.config if args.config is not None else repo / CONFIG_NAME).resolve()
    try:
        config_rel = config.relative_to(repo)
    except ValueError:
        _error(f"--config {config}: must be inside --repo {repo}; the runner sees only the repo")
        return ExitCode.USAGE
    try:
        cfg = load_config(config)
    except ConfigError as exc:
        _error(f"config {config}: {exc}")
        return ExitCode.USAGE
    job_minutes = args.job_timeout_minutes if args.job_timeout_minutes is not None else MAX_JOB_MINUTES
    try:
        spec = WorkflowSpec(
            nightloop_repo=args.nightloop_repo,
            nightloop_sha=args.nightloop_sha,
            claude_version=args.claude_version,
            credential=_CREDENTIALS[args.credential],
            timezone=args.timezone,
            hour=args.hour,
            minute=args.minute,
            timeout_s=args.timeout_s,
            job_timeout_minutes=job_minutes,
            config_path=config_rel.as_posix(),
            workflow_stem=args.workflow_name,
        )
        _check_zone_exists(spec.timezone)
        valid = validate_spec(cfg, spec)
    except SpecError as exc:
        _error(str(exc))
        return ExitCode.USAGE
    text = render_workflow(valid)
    steps = manual_steps(credential=spec.credential, workflow_name=spec.workflow_stem,
                         agents=(cfg.research.agent, cfg.build.agent, cfg.review.agent))
    if args.dry_run:
        sys.stdout.write(text)
        print(steps, file=sys.stderr)
        return ExitCode.OK
    result = install_workflow(workflow_path(repo, spec.workflow_stem), text)
    if isinstance(result, Written):
        print(f"wrote {result.path}")
    else:
        print(f"{result.path} is already up to date; nothing to do")
    print(steps)
    return ExitCode.OK


def _check_zone_exists(name: str) -> None:
    """Raise ``SpecError("timezone")`` if this machine's tz database has no zone ``name``.

    On the runner an unknown ``TZ`` silently reads as UTC, so the window
    check would use the wrong hour; catching a typo here is the only check.
    The runner's tz database could still differ from this machine's.
    """
    try:
        zoneinfo.ZoneInfo(name)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError) as exc:
        raise SpecError("timezone", f"{name!r} is not a zone in this machine's tz database ({exc})") from None


def _error(message: str) -> None:
    print(f"{PROG}: {message}", file=sys.stderr)


__all__ = ["run_schedule_gha", "workflow_path"]
