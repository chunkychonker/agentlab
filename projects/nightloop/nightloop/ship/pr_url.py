"""Finds the PR number in what ``gh pr create`` printed.

Pure: no I/O. The manual says ``gh pr create`` prints the new PR's URL on
success; the number is the ``/pull/<n>`` at its end. Anything else (no URL,
or a URL that is not a PR) is ``None``, which the shipper turns into
``NotShipped``: a PR whose number cannot be read is never assumed to exist.
"""

from __future__ import annotations

import re

# A whole line that is an http(s) URL ending /pull/<n>. n is at most 9 digits.
_PR_URL = re.compile(r"https?://\S+/pull/([1-9][0-9]{0,8})/?")


def pr_number(gh_output: str) -> int | None:
    """The PR number from the last line of ``gh_output`` that is a PR URL; ``None`` if there is none."""
    for line in reversed(gh_output.splitlines()):
        found = _PR_URL.fullmatch(line.strip())
        if found:
            return int(found.group(1))
    return None
