"""Milestone 10: render a GitHub Actions workflow that runs one ``nightloop run`` a night.

Public API::

    from nightloop.schedule_gha import (
        Credential, WorkflowSpec, ValidSpec, SpecError, validate_spec,
        render_workflow, install_workflow, Written, Unchanged,
        worst_case_minutes, JOB_SLACK_MINUTES, MAX_JOB_MINUTES,
    )

Pure: ``errors``, ``spec``, ``budget``, ``render``. Edge: ``write`` (the
only module that writes files). Nothing here imports ``plistlib`` or the
launchd package, and nothing talks to GitHub.
"""

from __future__ import annotations

from .budget import JOB_SLACK_MINUTES, MAX_JOB_MINUTES, worst_case_minutes
from .errors import SpecError
from .render import render_workflow
from .spec import Credential, ValidSpec, WorkflowSpec, validate_spec
from .write import Unchanged, Written, install_workflow

__all__ = [
    "Credential", "JOB_SLACK_MINUTES", "MAX_JOB_MINUTES", "SpecError", "Unchanged", "ValidSpec",
    "WorkflowSpec", "Written", "install_workflow", "render_workflow", "validate_spec",
    "worst_case_minutes",
]
