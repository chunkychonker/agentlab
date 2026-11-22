"""Carries out a planned list of ``Step``s: the only scheduler module that writes files.

Order of effects for an install that replaces a loaded job: save the old
plist once to ``<label>.plist.bak``, ``bootout``, write the new plist to a
temp file in the same directory, lint the temp file, ``os.replace`` it into
place, create the log directories, ``enable``, ``bootstrap``. A lint
failure leaves the final path untouched. Every failure after the
``bootout`` says the job is UNLOADED and gives the command that loads it
again, as agentlab's ``setup_runner.sh`` does.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from .errors import ScheduleError
from .plan import Step
from .ports import Launchctl

BACKUP_SUFFIX = ".bak"
PLIST_MODE = 0o644


def backup_path(plist_path: Path) -> Path:
    """Where the first replaced plist is saved: ``<plist>.bak`` (launchd ignores it; it does not end in ``.plist``)."""
    return plist_path.with_name(plist_path.name + BACKUP_SUFFIX)


def apply(
    steps: tuple[Step, ...],
    *,
    plist_path: Path,
    desired: bytes,
    launchctl: Launchctl,
    domain: str,
    label: str,
    log_dirs: tuple[Path, ...],
) -> None:
    """Do ``steps`` in order. ``NOTHING`` does nothing; an empty tuple is not a plan and raises.

    Failure modes (all ``ScheduleError`` unless noted, each saying what state
    the job is in):
    - ``REFUSE_RUNNING`` anywhere in ``steps``: raises before any effect.
    - The backup copy fails: raises; nothing was changed.
    - ``bootout`` fails: raises; the old job is still loaded, plist unchanged.
    - Writing or linting the new plist fails: the temp file is removed and
      the final path is untouched. After a ``bootout`` the message says the
      job is UNLOADED and gives ``launchctl bootstrap <domain> <plist>``.
    - Creating a log directory, ``enable`` or ``bootstrap`` fails: the plist
      is in place; the message says UNLOADED and gives the retry command.
    - Removing the plist fails after a ``bootout``: the job is unloaded but
      would load again at the next login; the message says so.
    ``OSError`` from starting ``launchctl``/``plutil`` propagates unchanged
    (raised by the port, not caught here).
    """
    if not steps:
        raise ScheduleError("apply: an empty step list is not a plan")
    if Step.REFUSE_RUNNING in steps:
        raise ScheduleError(f"{label} is running right now; rerun after it ends. Nothing was changed.")
    backup = backup_path(plist_path)
    if Step.WRITE_PLIST in steps:
        _save_backup_once(plist_path, backup)
    unloaded = False
    for step in steps:
        if step is Step.NOTHING:
            continue
        if step is Step.BOOTOUT:
            try:
                launchctl.bootout(domain, label)
            except ScheduleError as exc:
                raise ScheduleError(f"{exc}. The old job is still loaded; its plist was not changed.") from exc
            unloaded = True
        elif step is Step.WRITE_PLIST:
            try:
                _write_linted(plist_path, desired, launchctl)
            except (OSError, ScheduleError) as exc:
                raise ScheduleError(_write_failure(exc, unloaded, domain, plist_path)) from exc
        elif step is Step.BOOTSTRAP:
            try:
                for directory in log_dirs:
                    directory.mkdir(parents=True, exist_ok=True)
                launchctl.enable(domain, label)
                launchctl.bootstrap(domain, plist_path)
            except (OSError, ScheduleError) as exc:
                saved = f" (the previous plist is saved at {backup})" if backup.exists() else ""
                raise ScheduleError(
                    f"{exc}. The job is UNLOADED; retry: launchctl bootstrap {domain} {plist_path}{saved}"
                ) from exc
        elif step is Step.REMOVE_PLIST:
            try:
                plist_path.unlink()
            except OSError as exc:
                where = "The job is UNLOADED, but" if unloaded else "The job was not loaded, and"
                raise ScheduleError(
                    f"could not remove {plist_path}: {exc}. {where} the plist would load it "
                    f"again at the next login; remove it by hand."
                ) from exc
        else:  # pragma: no cover - every Step is handled above
            raise AssertionError(f"unhandled step {step}")


def _save_backup_once(plist_path: Path, backup: Path) -> None:
    """Copy the installed plist to ``backup`` unless there is no plist or a backup already exists."""
    if not plist_path.exists() or backup.exists():
        return
    try:
        shutil.copy2(plist_path, backup)
    except OSError as exc:
        raise ScheduleError(f"could not save the old plist to {backup}: {exc}. Nothing was changed.") from exc


def _write_linted(plist_path: Path, data: bytes, launchctl: Launchctl) -> None:
    """Write ``data`` to a temp file next to ``plist_path``, lint it, then ``os.replace`` it into place.

    The temp file is removed on every failure, so the final path holds
    either the old bytes or the new, linted ones.
    """
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=plist_path.parent, prefix=f".{plist_path.name}.", suffix=".tmp")
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, PLIST_MODE)
        launchctl.lint(temp)
        os.replace(temp, plist_path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def _write_failure(exc: Exception, unloaded: bool, domain: str, plist_path: Path) -> str:
    if not unloaded:
        return f"could not write {plist_path}: {exc}. Nothing was loaded or unloaded."
    if not plist_path.exists():
        return f"could not write {plist_path}: {exc}. The job is UNLOADED and no plist is installed; rerun install."
    return (
        f"could not write {plist_path}: {exc}. The job is UNLOADED; its previous plist is "
        f"still at {plist_path}; restore with: launchctl bootstrap {domain} {plist_path}"
    )
