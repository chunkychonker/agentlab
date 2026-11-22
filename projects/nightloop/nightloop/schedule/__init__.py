"""Milestone 9: install the nightly ``nightloop run`` as a macOS LaunchAgent, idempotently.

Public API::

    from nightloop.schedule import (
        LaunchdJob, nightly_job, slot_for, default_label, plist_path, domain_for,
        render_plist, parse_plist, JobState, Step, plan_install, plan_uninstall,
        Launchctl, SubprocessLaunchctl, apply, wake_advice, ScheduleError,
    )

Pure: ``job``, ``plist``, ``plan``, ``launchd_print``, ``advice``, ``ports``,
``errors``. Edges: ``launchctl`` (the only module that starts a process) and
``apply`` (the only one that writes files).
"""

from __future__ import annotations

from .advice import wake_advice
from .apply import apply, backup_path
from .errors import ScheduleError
from .job import LaunchdJob, default_label, domain_for, nightly_job, plist_path, slot_for
from .launchctl import SubprocessLaunchctl
from .plan import JobState, Step, mutates, plan_install, plan_uninstall
from .plist import parse_plist, render_plist
from .ports import Launchctl

__all__ = [
    "JobState", "LaunchdJob", "Launchctl", "ScheduleError", "Step", "SubprocessLaunchctl",
    "apply", "backup_path", "default_label", "domain_for", "mutates", "nightly_job",
    "parse_plist", "plan_install", "plan_uninstall", "plist_path", "render_plist",
    "slot_for", "wake_advice",
]
