"""Declares the ``nightloop schedule-gha`` arguments.

Kept apart from ``gha_cmd`` so ``main`` builds its parser without importing
the generator (the same lazy-import rule as ``schedule_args``). No I/O;
argparse only. Values are checked once, by ``WorkflowSpec``, not here.
"""

from __future__ import annotations

import argparse
from pathlib import Path

COMMAND = "schedule-gha"
DEFAULT_WORKFLOW_NAME = "nightloop"
# The CLI spellings of nightloop.schedule_gha.Credential.
CREDENTIAL_CHOICES = ("oauth-token", "api-key")


def add_gha_subparser(commands: argparse._SubParsersAction, *, config_name: str, default_timeout_s: int) -> None:
    """Add ``schedule-gha`` to ``commands``; the parsed namespace has ``command == "schedule-gha"``."""
    gha = commands.add_parser(
        COMMAND,
        help="write a GitHub Actions workflow that runs one night on a cron",
        description="Render <repo>/.github/workflows/<name>.yml from the repo's nightloop config. "
                    "Every value is checked before anything is written; nothing talks to GitHub.",
    )
    gha.add_argument("--repo", type=Path, default=Path("."),
                     help="the checkout the workflow is for (default: the current directory)")
    gha.add_argument("--config", type=Path, default=None,
                     help=f"config inside the repo whose window and cycles the workflow honours "
                          f"(default: <repo>/{config_name})")
    gha.add_argument("--nightloop-repo", required=True,
                     help="GitHub owner/name that holds nightloop's source (projects/nightloop)")
    gha.add_argument("--nightloop-sha", required=True,
                     help="full 40-character commit SHA of nightloop to install (never a branch)")
    gha.add_argument("--claude-version", required=True, help="exact Claude Code version to install, X.Y.Z")
    gha.add_argument("--credential", required=True, choices=CREDENTIAL_CHOICES,
                     help="the one Claude credential the job exports from repo secrets")
    gha.add_argument("--timezone", required=True,
                     help="IANA zone for both the cron and the run's window check, e.g. America/Chicago or UTC")
    gha.add_argument("--minute", type=int, required=True, help="minute to run, 1-59 (GitHub's :00 is busiest)")
    gha.add_argument("--hour", type=int, default=None,
                     help="hour to run, inside the window (default: the window's start hour)")
    gha.add_argument("--timeout-s", type=int, default=default_timeout_s,
                     help=f"each phase's limit, passed to nightloop run (default: {default_timeout_s})")
    gha.add_argument("--job-timeout-minutes", type=int, default=None,
                     help="the job's timeout-minutes (default: GitHub's hosted-job limit)")
    gha.add_argument("--workflow-name", default=DEFAULT_WORKFLOW_NAME,
                     help=f"workflow file stem and name (default: {DEFAULT_WORKFLOW_NAME})")
    gha.add_argument("--dry-run", action="store_true",
                     help="print the workflow to stdout; write nothing")
