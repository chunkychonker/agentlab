"""Parses review verdict text into a ``Verdict``.

Pure: no I/O. A port of ``review_verdict`` in agentlab's
``.pipeline/verdict.sh``; the bash file is the reference, not a dependency.

The contract is one line at column 0 reading ``VERDICT: PASS`` or
``VERDICT: FAIL``. The match is strict because the gate fails closed: a
verdict that cannot be read with certainty is not a licence to ship.
"""

from __future__ import annotations

import re

from .outcome import Verdict

_PREFIX = "VERDICT:"

# The bash gate's `sed 's/^VERDICT:[[:space:]]*\([A-Za-z]*\).*/\1/'`, spelled
# with explicit ASCII classes: Python's `\s` and `\w` also match Unicode
# whitespace and letters, which POSIX `[[:space:]]` and `[A-Za-z]` do not. The
# letter run is captured whole and compared exactly, so `PASSED` is its own
# (unrecognised) word rather than a PASS prefix.
_WORD = re.compile(r"VERDICT:[ \t\n\v\f\r]*([A-Za-z]*)")

_WORDS = {"PASS": Verdict.PASS, "FAIL": Verdict.FAIL}


def parse_verdict(text: str | None) -> Verdict:
    """Return the verdict recorded in ``text``.

    ``None`` means the file was absent or unreadable, and ``""`` means it was
    zero bytes; both are ``MISSING``. Any other text is ``PASS`` or ``FAIL``
    only if exactly one line starts with ``VERDICT:`` at column 0 and the
    letters after it (past optional ASCII whitespace) are exactly ``PASS`` or
    ``FAIL``; otherwise it is ``MALFORMED``. Lines are split on ``\\n`` only,
    as grep does, never on the other separators ``str.splitlines`` honours.

    Never raises for any ``str`` or ``None`` input.
    """
    if text is None or text == "":
        return Verdict.MISSING
    # Two VERDICT lines are a contradiction, not a vote.
    verdict_lines = [line for line in text.split("\n") if line.startswith(_PREFIX)]
    if len(verdict_lines) != 1:
        return Verdict.MALFORMED
    match = _WORD.match(verdict_lines[0])
    # Cannot be None: the line starts with the prefix and the rest may be empty.
    assert match is not None
    return _WORDS.get(match.group(1), Verdict.MALFORMED)
