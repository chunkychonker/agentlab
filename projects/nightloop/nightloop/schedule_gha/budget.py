"""The time budget rule: a night's worst case must fit the hosted job's limit.

Pure: integer arithmetic only. A GitHub-hosted job that hits its time limit
is killed mid-cycle, with no recovery step, so the generator refuses a
config whose worst case cannot fit before it writes anything.
"""

from __future__ import annotations

from .errors import SpecError

# "Each job in a workflow can run for up to 6 hours of execution time" for
# all GitHub-hosted runners (docs.github.com/en/actions/reference/limits,
# fetched 2026-11-23).
MAX_JOB_MINUTES = 360
# Setup (two checkouts with full history, Python, Node, the CLI and pip
# installs), the shipper's git/gh calls and the artifact upload, which no
# phase timeout bounds. A guess with margin; see the README.
JOB_SLACK_MINUTES = 30
# After a phase's timeout the runner sends SIGTERM and waits this long before
# SIGKILL. Mirrors phase.subprocess_runner.TERM_GRACE_S, which this pure
# module cannot import (it imports subprocess); a test keeps the two equal.
PHASE_KILL_GRACE_S = 10
_SECONDS_PER_MINUTE = 60


def worst_case_minutes(cycles: int, phases: int, timeout_s: int) -> int:
    """Minutes, rounded up, for ``cycles`` cycles of ``phases`` phases that each run to the kill.

    Each phase counts ``timeout_s`` plus ``PHASE_KILL_GRACE_S``. Callers pass
    positive ints (``validate_spec`` and ``Config`` have checked them).
    """
    seconds = cycles * phases * (timeout_s + PHASE_KILL_GRACE_S)
    return -(-seconds // _SECONDS_PER_MINUTE)


def check_budget(cycles: int, phases: int, timeout_s: int, job_timeout_minutes: int) -> int:
    """Return the minutes a night needs (worst case plus ``JOB_SLACK_MINUTES``) if it fits.

    Raises ``SpecError("job_timeout_minutes", ...)`` if ``job_timeout_minutes``
    exceeds ``MAX_JOB_MINUTES``, or if the needed minutes exceed
    ``job_timeout_minutes``; the message names both numbers. ``needed ==
    job_timeout_minutes`` fits.
    """
    if job_timeout_minutes > MAX_JOB_MINUTES:
        raise SpecError(
            "job_timeout_minutes",
            f"{job_timeout_minutes} exceeds the GitHub-hosted job limit of {MAX_JOB_MINUTES} minutes",
        )
    needed = worst_case_minutes(cycles, phases, timeout_s) + JOB_SLACK_MINUTES
    if needed > job_timeout_minutes:
        raise SpecError(
            "job_timeout_minutes",
            f"a night needs up to {needed} minutes ({cycles} cycle(s) x {phases} phases x "
            f"({timeout_s}s + {PHASE_KILL_GRACE_S}s kill grace), plus {JOB_SLACK_MINUTES} min slack) "
            f"but the job is limited to {job_timeout_minutes} minutes; "
            "lower cycles or --timeout-s",
        )
    return needed
