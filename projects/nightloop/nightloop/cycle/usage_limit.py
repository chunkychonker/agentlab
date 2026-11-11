"""Recognises Claude's usage-limit message in a phase's output.

Pure: no I/O. A port of ``usage_limit_reset`` in agentlab's
``.pipeline/usage_limit.sh``; the bash file is the reference, not a
dependency. Why it matters: a usage limit is "come back later", not a defect
in the increment, and every later phase of the night would hit it too.
"""

from __future__ import annotations

import re

# The bash USAGE_LIMIT_RE, matched case-insensitively (grep -iE). The first
# alternative is the exact CLI text seen on 2026-10-19. re.ASCII keeps the
# case folding to ASCII, as grep's does in the C locale.
_LIMIT = re.compile(
    r"hit your (session|usage|weekly) limit|usage limit reached", re.IGNORECASE | re.ASCII
)
# The bash sed: `s/.*[Rr]esets (at )?(.*)$/\2/p`. The greedy `.*` takes the
# last "resets" on the line, and only the R is case-insensitive.
_RESET = re.compile(r".*[Rr]esets (?:at )?(.*)$")
# POSIX [[:space:]] in the C locale, stripped from the end as bash does.
_TRAILING_SPACE = " \t\n\v\f\r"

UNKNOWN_RESET = "unknown"


def usage_limit_reset(text: str) -> str | None:
    """When the limit lifts if ``text`` shows a usage limit, else ``None``.

    Lines are split on ``\\n`` only, as grep does. The last matching line
    decides; the reset is the text after its last ``resets `` (or ``resets
    at ``), with trailing whitespace removed, or ``UNKNOWN_RESET`` when there
    is none. A carriage return inside the reset text (a progress-bar redraw)
    becomes a space, so the result is always one line. Never raises for a
    ``str``.
    """
    matching = [line for line in text.split("\n") if _LIMIT.search(line)]
    if not matching:
        return None
    found = _RESET.match(matching[-1])
    reset = "" if found is None else found.group(1).replace("\r", " ").rstrip(_TRAILING_SPACE)
    return reset or UNKNOWN_RESET
