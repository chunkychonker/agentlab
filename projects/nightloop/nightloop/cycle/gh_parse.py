"""Reads what ``gh pr create`` and ``gh pr view --json`` print.

Pure: no I/O. Every parser returns ``None`` for output it does not
recognise rather than guessing, so the shipper can turn it into
``NotShipped``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

# `gh pr create` prints only the PR's URL; it has no --json option. The
# number is the URL's trailing /pull/<N>, at most 10 digits.
_PR_URL = re.compile(r"https://[^\s/]+/[^\s/]+/[^\s/]+/pull/([1-9][0-9]{0,9})")


def parse_pr_number(stdout: str) -> int | None:
    """The PR number from ``gh pr create``'s stdout, or ``None`` unless it is exactly one PR URL."""
    match = _PR_URL.fullmatch(stdout.strip())
    return int(match.group(1)) if match else None


@dataclass(frozen=True)
class PrView:
    """The fields the shipper asks ``gh pr view --json body,mergeable`` for."""

    body: str
    mergeable: str


def parse_pr_view(stdout: str) -> PrView | None:
    """A ``PrView`` from ``gh pr view --json body,mergeable`` output.

    ``None`` if the output is not JSON (NaN and Infinity count as not
    JSON), is not an object, or ``body``/``mergeable`` is missing or not a
    string.
    """
    try:
        data = json.loads(stdout, parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        return None
    if not isinstance(data, dict):
        return None
    body = data.get("body")
    mergeable = data.get("mergeable")
    if not isinstance(body, str) or not isinstance(mergeable, str):
        return None
    return PrView(body=body, mergeable=mergeable)


def _reject_constant(name: str) -> object:
    raise ValueError(f"not JSON: {name}")
