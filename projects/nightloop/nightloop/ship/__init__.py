"""Publishes a cycle whose review passed as a pushed branch and an open, reviewed, mergeable PR.

Public API::

    from nightloop.ship import (
        GitHubShipper, GitBranchPublisher, GhCli, verdict_reader,
        BranchPublisher, PullRequests, Published, PublishFailed, GhFailed,
        REVIEW_MARKER,
    )

Production wiring (milestone 7's CLI will do this)::

    GitHubShipper(
        read_verdict=verdict_reader(repo / verdict_path),
        git=GitBranchPublisher(repo),
        gh=GhCli(repo),
        sleep=time.sleep,
    )

Nothing in this package merges a PR, force-pushes, or pushes main.
"""

from __future__ import annotations

from .gh_cli import GhCli
from .git_publisher import GitBranchPublisher
from .ports import BranchPublisher, GhFailed, Published, PublishFailed, PullRequests
from .review_record import REVIEW_MARKER
from .shipper import GitHubShipper
from .verdict_file import verdict_reader

__all__ = [
    "BranchPublisher",
    "GhCli",
    "GhFailed",
    "GitBranchPublisher",
    "GitHubShipper",
    "PublishFailed",
    "Published",
    "PullRequests",
    "REVIEW_MARKER",
    "verdict_reader",
]
