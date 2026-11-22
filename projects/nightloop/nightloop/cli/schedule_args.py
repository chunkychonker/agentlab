"""Declares the ``nightloop schedule install|uninstall|status`` arguments.

Kept apart from ``schedule_cmd`` so ``main`` can build its parser without
importing the scheduler (and ``plistlib``): ``run`` and ``report`` must
work on a Python whose XML parser cannot load. No I/O; argparse only.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def add_subparser(commands: argparse._SubParsersAction, *, config_name: str) -> None:
    """Add ``schedule`` and its ``install``/``uninstall``/``status`` actions to ``commands``.

    The parsed namespace has ``command == "schedule"`` and
    ``schedule_action`` set; ``--command`` is stored as ``program`` so it
    cannot overwrite the top-level ``command``.
    """
    schedule = commands.add_parser("schedule", help="install, remove or inspect the macOS launchd job")
    actions = schedule.add_subparsers(dest="schedule_action", required=True,
                                      metavar="{install,uninstall,status}")

    where = argparse.ArgumentParser(add_help=False)
    where.add_argument("--repo", type=Path, default=Path("."),
                       help="the checkout the job runs in (default: the current directory)")
    where.add_argument("--label", default=None,
                       help="launchd label (default: dev.nightloop.<repo name>.<hash of the repo path>)")
    config = argparse.ArgumentParser(add_help=False)
    config.add_argument("--config", type=Path, default=None,
                        help=f"config file whose window the job honours (default: <repo>/{config_name})")
    dry = argparse.ArgumentParser(add_help=False)
    dry.add_argument("--dry-run", action="store_true",
                     help="print the plan (and the plist); change nothing in launchd or on disk")

    install = actions.add_parser("install", parents=[where, config, dry],
                                 help="write and load the job (does nothing if it is already in place)")
    install.add_argument("--hour", type=int, default=None,
                         help="hour to run, inside the window (default: the window's start hour)")
    install.add_argument("--minute", type=int, default=0, help="minute to run (default: 0)")
    install.add_argument("--command", dest="program", type=Path, default=None,
                         help="absolute path of the nightloop script to run "
                              "(default: this interpreter with -m nightloop)")
    actions.add_parser("uninstall", parents=[where, dry], help="unload and remove the job")
    actions.add_parser("status", parents=[where, config], help="show the installed job and its state")
