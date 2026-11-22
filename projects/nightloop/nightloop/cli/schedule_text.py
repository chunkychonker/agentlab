"""Formats what ``nightloop schedule`` prints: plans, jobs and the wake advice.

Pure: no I/O. The command module decides; this module only words it.
"""

from __future__ import annotations

import shlex

from ..model import Window
from ..schedule.advice import wake_advice
from ..schedule.job import LaunchdJob
from ..schedule.plan import Step


def describe_plan(steps: tuple[Step, ...]) -> str:
    """``plan: <step>, <step>`` in order."""
    return "plan: " + ", ".join(step.value for step in steps)


def describe_job(job: LaunchdJob) -> str:
    """``<label> runs `<argv>` daily at HH:MM in <workdir>``."""
    return (
        f"{job.label} runs `{shlex.join(job.argv)}` daily at "
        f"{job.hour:02d}:{job.minute:02d} in {job.workdir}"
    )


def describe_window(job: LaunchdJob, window: Window, *, inside: bool) -> str:
    """One line saying the job's slot is (``inside``) or is not inside ``window``; the caller decides which."""
    span = f"[{window.start_hour:02d}:00, {window.end_hour:02d}:00)"
    if inside:
        return f"slot {job.hour:02d}:{job.minute:02d} is inside the run window {span}"
    return (
        f"WARNING: slot {job.hour:02d}:{job.minute:02d} is outside the run window {span}; "
        f"every scheduled run will be refused. Rerun `nightloop schedule install`."
    )


def advice_text(job: LaunchdJob) -> str:
    """The wake-schedule advice, saying plainly that nightloop did not run it."""
    return (
        "launchd cannot wake a sleeping Mac; a missed slot runs at the next wake, "
        "and `nightloop run` refuses it if that is outside the window.\n"
        f"To wake the Mac {job.hour:02d}:{job.minute:02d} minus 10 minutes every day, a human can run:\n"
        f"  {wake_advice(job.hour, job.minute)}\n"
        "(this replaces any existing repeating pmset schedule; check `pmset -g sched` first). "
        "nightloop ran nothing of this."
    )
