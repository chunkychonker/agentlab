"""Offline checks for a Claude Code plugin directory (milestone 8).

Public API::

    from nightloop.plugin_check import check_plugin, Problem, ProblemKind

``check_plugin(root)`` returns ``[]`` for a plugin that passes. It checks
what ``claude plugin validate`` does not: see ``rules``.
"""

from __future__ import annotations

from .check import check_plugin
from .problem import Problem, ProblemKind

__all__ = ["Problem", "ProblemKind", "check_plugin"]
