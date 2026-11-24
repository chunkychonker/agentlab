"""The reviewer benchmark: how often the review template catches a seeded bug, and how often it fails a clean diff.

Public API::

    from nightloop.bench import (
        load_fixtures, run_bench, summarise, render_report, to_json, exit_code,
        check_fresh, template_sha256, TempGitWorkspace, Fixture, Kind, Outcome,
    )

Pure: ``fixture``, ``classify``, ``stats``, ``report``, ``fresh``, ``ports``
and ``run`` (its effects arrive as parameters). Edges: ``load`` (reads the
fixtures dir) and ``workspace`` (git and temp dirs). The CLI is
``nightloop bench`` (``nightloop.cli.bench_cmd``).
"""

from __future__ import annotations

from .classify import Outcome, classify
from .fixture import Fixture, FixtureError, Kind, MatrixError, parse_expect, require_both_kinds
from .fresh import TemplateFreshness, check_fresh, recorded_sha256, template_sha256
from .load import load_fixtures
from .report import BenchReport, TrialResult, exit_code, render_report, summarise, to_json
from .run import review_request, run_bench
from .stats import wilson
from .workspace import TempGitWorkspace, WorkspaceError

__all__ = [
    "BenchReport",
    "Fixture",
    "FixtureError",
    "Kind",
    "MatrixError",
    "Outcome",
    "TempGitWorkspace",
    "TemplateFreshness",
    "TrialResult",
    "WorkspaceError",
    "check_fresh",
    "classify",
    "exit_code",
    "load_fixtures",
    "parse_expect",
    "recorded_sha256",
    "render_report",
    "require_both_kinds",
    "review_request",
    "run_bench",
    "summarise",
    "template_sha256",
    "to_json",
    "wilson",
]
