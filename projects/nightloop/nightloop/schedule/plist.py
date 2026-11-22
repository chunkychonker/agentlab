"""Turns a ``LaunchdJob`` into LaunchAgent plist bytes and back.

Pure: no I/O. ``plistlib`` escapes ``& < > " '`` by construction, so unlike
agentlab's bash ``render_runner_plist`` no path is rejected for holding
them. Output is deterministic (sorted keys), which is what lets
``plan_install`` decide "already installed" by comparing bytes.
"""

from __future__ import annotations

import plistlib
from pathlib import PurePosixPath

from .errors import ScheduleError
from .job import LaunchdJob

_KEYS = frozenset({
    "Label", "ProgramArguments", "WorkingDirectory", "EnvironmentVariables",
    "StartCalendarInterval", "RunAtLoad", "StandardOutPath", "StandardErrorPath",
})


def render_plist(job: LaunchdJob) -> bytes:
    """The XML plist for ``job``: same job, same bytes. Never raises for a valid job.

    ``StartCalendarInterval`` always holds both ``Hour`` and ``Minute``: an
    omitted key is a wildcard to launchd, so a missing ``Minute`` would fire
    every minute of the hour. ``RunAtLoad`` is false so installing never
    starts a night.
    """
    return plistlib.dumps(
        {
            "Label": job.label,
            "ProgramArguments": list(job.argv),
            "WorkingDirectory": str(job.workdir),
            "EnvironmentVariables": {"PATH": job.path_env},
            "StartCalendarInterval": {"Hour": job.hour, "Minute": job.minute},
            "RunAtLoad": False,
            "StandardOutPath": str(job.out_log),
            "StandardErrorPath": str(job.err_log),
        },
        fmt=plistlib.FMT_XML,
        sort_keys=True,
    )


def parse_plist(data: bytes) -> LaunchdJob:
    """The ``LaunchdJob`` that ``render_plist`` would have written as ``data``.

    Raises ``ScheduleError`` if ``data`` is not a plist, does not have
    exactly the keys ``render_plist`` writes, a value has the wrong type, or
    the values do not make a valid ``LaunchdJob`` (for example a plist
    written by hand or by another tool).
    """
    try:
        loaded = plistlib.loads(data)
    except Exception as exc:  # plistlib raises several unrelated types for bad input
        raise ScheduleError(f"not a readable plist: {type(exc).__name__}: {exc}") from exc
    if not isinstance(loaded, dict) or set(loaded) != _KEYS:
        found = sorted(loaded) if isinstance(loaded, dict) else type(loaded).__name__
        raise ScheduleError(f"not a plist nightloop writes: keys {found}")
    env, slot, argv = loaded["EnvironmentVariables"], loaded["StartCalendarInterval"], loaded["ProgramArguments"]
    if not isinstance(env, dict) or set(env) != {"PATH"}:
        raise ScheduleError("not a plist nightloop writes: EnvironmentVariables must hold only PATH")
    if not isinstance(slot, dict) or set(slot) != {"Hour", "Minute"}:
        raise ScheduleError("not a plist nightloop writes: StartCalendarInterval must hold Hour and Minute")
    if not isinstance(argv, list):
        raise ScheduleError("not a plist nightloop writes: ProgramArguments must be an array")
    if loaded["RunAtLoad"] is not False:
        raise ScheduleError("not a plist nightloop writes: RunAtLoad must be false")
    return LaunchdJob(
        label=loaded["Label"],
        argv=tuple(argv),
        workdir=_path("WorkingDirectory", loaded["WorkingDirectory"]),
        path_env=env["PATH"],
        hour=slot["Hour"],
        minute=slot["Minute"],
        out_log=_path("StandardOutPath", loaded["StandardOutPath"]),
        err_log=_path("StandardErrorPath", loaded["StandardErrorPath"]),
    )


def _path(key: str, value: object) -> PurePosixPath:
    if not isinstance(value, str):
        raise ScheduleError(f"not a plist nightloop writes: {key} must be a string")
    return PurePosixPath(value)
