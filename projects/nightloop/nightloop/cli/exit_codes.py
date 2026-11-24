"""The ``nightloop`` command's exit codes, defined once for every subcommand.

Pure: no I/O. ``nightloop.cli.main`` re-exports ``ExitCode``, so
``from nightloop.cli import ExitCode`` and ``from nightloop.cli.main import
ExitCode`` keep working.
"""

from __future__ import annotations

import enum


class ExitCode(enum.IntEnum):
    """What ``main`` returns. argparse's own usage errors also exit 2."""

    OK = 0  # the night ran (shipped or not; the metrics say how), or the plan/report/schedule step finished
    # run: a program the night needs (claude, git, gh) could not be started.
    # schedule: a launchctl/plutil step failed, or status found a broken job.
    # bench: git or claude could not be started, or a trial repo could not be built.
    ENVIRONMENT = 1
    USAGE = 2  # bad arguments, bad config or fixture, --repo is not a checkout's top level, or schedule on a non-mac
    REFUSED = 3  # outside the run window, or the clock could not be read
    USAGE_LIMITED = 4  # the night stopped early on a Claude usage limit
    NO_METRICS = 5  # report: the metrics file is missing or holds no usable record
    JOB_RUNNING = 6  # schedule install/uninstall: the job is running now, so nothing was changed
    UNGRADED = 7  # bench: a trial gave no gradable verdict; the report printed, no results were recorded
    BENCH_STALE = 8  # bench --check-fresh: the recorded run measured a different reviewer template
    BENCH_NEVER_RUN = 9  # bench --check-fresh: no results file is recorded
