"""Decides which launchd steps move the installed job to the desired one.

Pure: no I/O. The caller reads the installed plist and the job's state;
this module only compares. A job that is running is never touched: a
``bootout`` would kill the night it is running.
"""

from __future__ import annotations

import enum


class JobState(enum.Enum):
    """What ``launchctl print gui/<uid>/<label>`` says about the job."""

    NOT_LOADED = "not loaded"
    LOADED_IDLE = "loaded, idle"
    RUNNING = "running"


class Step(enum.Enum):
    """One thing ``apply`` does. Steps carry no data: the plist bytes and paths are passed once to ``apply``."""

    NOTHING = "nothing to do"
    WRITE_PLIST = "write the plist"
    BOOTOUT = "bootout (unload) the job"
    BOOTSTRAP = "bootstrap (load) the job"
    REMOVE_PLIST = "remove the plist"
    REFUSE_RUNNING = "refuse: the job is running now"


def plan_install(desired: bytes, installed: bytes | None, state: JobState) -> tuple[Step, ...]:
    """The steps that make ``desired`` the installed, loaded plist.

    ``installed`` is ``None`` when no plist is installed. An installed plist
    that could not be read should be passed as ``None`` too; it is then
    rewritten, which is the same as "differs". ``RUNNING`` always gives
    ``(REFUSE_RUNNING,)``. Never raises.
    """
    if state is JobState.RUNNING:
        return (Step.REFUSE_RUNNING,)
    same = installed == desired
    if same and state is JobState.LOADED_IDLE:
        return (Step.NOTHING,)
    if same:
        return (Step.BOOTSTRAP,)
    if state is JobState.LOADED_IDLE:
        return (Step.BOOTOUT, Step.WRITE_PLIST, Step.BOOTSTRAP)
    return (Step.WRITE_PLIST, Step.BOOTSTRAP)


def plan_uninstall(installed: bytes | None, state: JobState) -> tuple[Step, ...]:
    """The steps that leave no plist installed and nothing loaded.

    ``RUNNING`` always gives ``(REFUSE_RUNNING,)``. Never raises.
    """
    if state is JobState.RUNNING:
        return (Step.REFUSE_RUNNING,)
    steps: list[Step] = []
    if state is JobState.LOADED_IDLE:
        steps.append(Step.BOOTOUT)
    if installed is not None:
        steps.append(Step.REMOVE_PLIST)
    return tuple(steps) or (Step.NOTHING,)


def mutates(steps: tuple[Step, ...]) -> bool:
    """True if any step changes the file or launchd (``NOTHING`` and ``REFUSE_RUNNING`` do not)."""
    return any(step not in (Step.NOTHING, Step.REFUSE_RUNNING) for step in steps)
