"""The launchd job nightloop installs, as a value that cannot hold a bad field.

Pure: no I/O. ``LaunchdJob`` checks every field when it is built, so a job
that exists can be rendered and loaded. ``nightly_job`` is the only way the
CLI builds one: it always passes ``run --repo <repo> --config <config>``
and never ``--ignore-window``, so the schedule picks a slot but never
replaces ``nightloop run``'s own window check (launchd runs a missed job
at the next wake, which can be mid-morning).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from ..model import Window
from .errors import ScheduleError

LABEL_PATTERN = re.compile(r"[A-Za-z0-9._-]+")
LABEL_PREFIX = "dev.nightloop"
# The flag that would let a scheduled run start outside the window.
FORBIDDEN_ARG = "--ignore-window"
LAUNCH_AGENTS = PurePosixPath("Library") / "LaunchAgents"
LOG_DIR = PurePosixPath("logs")
OUT_LOG_NAME = "nightloop.launchd.out.log"
ERR_LOG_NAME = "nightloop.launchd.err.log"
_HOUR_MAX, _MINUTE_MAX = 23, 59
_LABEL_HASH_CHARS = 8
_UNSAFE_LABEL_CHARS = re.compile(r"[^A-Za-z0-9-]+")


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check_text(field: str, value: object) -> None:
    """Raise unless ``value`` is a non-empty str with no control character.

    plistlib refuses control characters, and NUL cannot reach exec at all.
    """
    if not isinstance(value, str):
        raise ScheduleError(f"{field}: must be a string, got {type(value).__name__}")
    if value == "":
        raise ScheduleError(f"{field}: must not be empty")
    bad = next((ch for ch in value if ord(ch) < 0x20 or ord(ch) == 0x7F), None)
    if bad is not None:
        raise ScheduleError(f"{field}: must not contain control character {bad!r}")


def _check_path(field: str, value: object) -> None:
    if not isinstance(value, PurePosixPath):
        raise ScheduleError(f"{field}: must be a path, got {type(value).__name__}")
    _check_text(field, str(value))
    if not value.is_absolute():
        raise ScheduleError(f"{field}: must be absolute, got {str(value)!r}")


def _check_clock(hour: object, minute: object) -> None:
    if not _is_int(hour) or not 0 <= hour <= _HOUR_MAX:
        raise ScheduleError(f"hour: must be an integer from 0 to {_HOUR_MAX}, got {hour!r}")
    if not _is_int(minute) or not 0 <= minute <= _MINUTE_MAX:
        raise ScheduleError(f"minute: must be an integer from 0 to {_MINUTE_MAX}, got {minute!r}")


@dataclass(frozen=True)
class LaunchdJob:
    """One LaunchAgent: run ``argv`` in ``workdir`` daily at ``hour:minute`` local time.

    Raises ``ScheduleError`` naming the field when: ``label`` does not match
    ``[A-Za-z0-9._-]+``; ``argv`` is not a non-empty tuple of non-empty
    strings, its first item is not an absolute path, or it contains
    ``--ignore-window``; ``workdir``, ``out_log`` or ``err_log`` is not an
    absolute ``PurePosixPath``; ``path_env`` is empty; any string holds a
    control character (NUL included); ``hour`` is not an int 0-23 or
    ``minute`` not an int 0-59 (``bool`` is not an int here).
    """

    label: str
    argv: tuple[str, ...]
    workdir: PurePosixPath
    path_env: str
    hour: int
    minute: int
    out_log: PurePosixPath
    err_log: PurePosixPath

    def __post_init__(self) -> None:
        _check_text("label", self.label)
        if not LABEL_PATTERN.fullmatch(self.label):
            raise ScheduleError(f"label: must match {LABEL_PATTERN.pattern}, got {self.label!r}")
        if not isinstance(self.argv, tuple) or not self.argv:
            raise ScheduleError("argv: must be a non-empty tuple of strings")
        for index, arg in enumerate(self.argv):
            _check_text(f"argv[{index}]", arg)
            if arg == FORBIDDEN_ARG:
                raise ScheduleError(
                    f"argv[{index}]: {FORBIDDEN_ARG} must never be scheduled; "
                    "the run's own window check is what stops a late launch"
                )
        if not PurePosixPath(self.argv[0]).is_absolute():
            raise ScheduleError(
                f"argv[0]: launchd needs an absolute program path, got {self.argv[0]!r}"
            )
        _check_path("workdir", self.workdir)
        _check_text("path_env", self.path_env)
        _check_clock(self.hour, self.minute)
        _check_path("out_log", self.out_log)
        _check_path("err_log", self.err_log)


def slot_for(window: Window, hour: int | None = None, minute: int = 0) -> tuple[int, int]:
    """The ``(hour, minute)`` the job fires at: ``hour`` (default ``window.start_hour``) and ``minute``.

    Raises ``ScheduleError`` if ``hour`` is not an int in
    ``[window.start_hour, window.end_hour)`` or ``minute`` is not an int
    0-59. Every slot it returns is inside the window, so a job that fires on
    time is never refused by ``nightloop run``.
    """
    chosen = window.start_hour if hour is None else hour
    _check_clock(chosen, minute)
    if not window.start_hour <= chosen < window.end_hour:
        raise ScheduleError(
            f"hour: {chosen} is outside the run window "
            f"[{window.start_hour:02d}:00, {window.end_hour:02d}:00) (end exclusive)"
        )
    return chosen, minute


def default_label(repo: PurePosixPath) -> str:
    """``dev.nightloop.<repo name>.<8 hex of sha256(repo)>``: one label per checkout path.

    The hash keeps two checkouts with the same directory name apart; the
    name keeps the label readable in ``launchctl list``. Characters a label
    may not hold are replaced with ``-``. Never raises for an absolute path.
    """
    name = _UNSAFE_LABEL_CHARS.sub("-", repo.name).strip("-") or "repo"
    digest = hashlib.sha256(str(repo).encode("utf-8")).hexdigest()[:_LABEL_HASH_CHARS]
    return f"{LABEL_PREFIX}.{name}.{digest}"


def plist_path(home: PurePosixPath, label: str) -> PurePosixPath:
    """``<home>/Library/LaunchAgents/<label>.plist``, where a per-user agent lives."""
    return home / LAUNCH_AGENTS / f"{label}.plist"


def domain_for(uid: int) -> str:
    """``gui/<uid>``, the launchd domain of a logged-in user's agents.

    Raises ``ScheduleError`` if ``uid`` is not a non-negative int.
    """
    if not _is_int(uid) or uid < 0:
        raise ScheduleError(f"uid: must be a non-negative integer, got {uid!r}")
    return f"gui/{uid}"


def nightly_job(
    *,
    label: str,
    command: tuple[str, ...],
    repo: PurePosixPath,
    config_path: PurePosixPath,
    path_env: str,
    hour: int,
    minute: int,
) -> LaunchdJob:
    """The job that runs ``<command> run --repo <repo> --config <config_path>`` at ``hour:minute``.

    ``command`` is how to start nightloop (an absolute ``nightloop`` script,
    or ``(<python>, "-m", "nightloop")``). The job runs in ``repo`` and logs
    to ``<repo>/logs/nightloop.launchd.{out,err}.log``. Raises
    ``ScheduleError`` as ``LaunchdJob`` does, plus field ``config_path`` if
    it is not absolute.
    """
    _check_path("config_path", config_path)
    _check_path("repo", repo)
    return LaunchdJob(
        label=label,
        argv=(*command, "run", "--repo", str(repo), "--config", str(config_path)),
        workdir=repo,
        path_env=path_env,
        hour=hour,
        minute=minute,
        out_log=repo / LOG_DIR / OUT_LOG_NAME,
        err_log=repo / LOG_DIR / ERR_LOG_NAME,
    )
