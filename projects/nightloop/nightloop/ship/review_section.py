"""Turns the reviewer's verdict file into the PR body section that carries the review record.

Pure: no I/O. A port of agentlab's ``.pipeline/review_record.sh``
(``review_record_section``, ``review_record_secret_hit``,
``review_record_compose``) with the same contract: a verdict with no
``Findings:`` line is not posted, a section that matches a credential
pattern is not posted, and when the body would be too long only the review
section is cut, never the text above it.
"""

from __future__ import annotations

import re
from typing import Final

# The hidden line that marks the section as present. Same as bash, so
# agentlab's existing PRs and nightloop's are recognised the same way.
MARKER: Final[str] = "<!-- agentlab-review -->"
# GitHub refuses a PR body longer than this many characters.
BODY_LIMIT: Final[int] = 65536
# A cut section must keep at least this many bytes, or it is refused: below
# it, the section would be mostly a truncation note.
MIN_KEPT_BYTES: Final[int] = 200

_INTRO = (
    "Written by nightloop's reviewer, a separate model run that did not write this code, "
    "before the PR was opened. Posted by the shipper; the mergeable check waits for it."
)
_TRUNCATED = (
    f"\n\n_(Truncated to fit GitHub's {BODY_LIMIT}-character PR body limit. "
    "The full verdict is in the run log.)_"
)
_VERDICT_LINE = re.compile(r"VERDICT:\s*([A-Za-z]*)")
_RAN = "Ran:"
_FINDINGS = "Findings:"
# The reviewer's list made concrete, as in bash: Anthropic (sk-ant-), other
# sk- keys, GitHub tokens, AWS access key ids, Slack tokens, PEM private
# keys, and KEY=value lines for names that hold secrets. Bare long hex is not
# matched: findings cite commit SHAs. Applied per line, as grep does.
_SECRET = re.compile(
    r"sk-ant-[A-Za-z0-9_-]{8,}"
    r"|sk-[A-Za-z0-9]{20,}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|xox[abposr]-[A-Za-z0-9-]{10,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|(API_KEY|OAUTH_TOKEN|SECRET|PASSWORD|ACCESS_TOKEN)[A-Z_]*[ \t\f\v]*=[ \t\f\v]*[^\s$<]{8,}"
)


class ReviewRecordError(ValueError):
    """The review record cannot be posted: no ``Findings:`` line, a credential hit, or no room.

    The message is one line and never quotes the matched credential.
    """


def review_section(verdict_text: str) -> str:
    """The markdown section for ``verdict_text``: marker, heading, verdict word, Ran and Findings.

    Ran is the rest of the first line starting ``Ran:`` plus every line
    after it, up to the first line starting ``Findings:``. Findings is the
    rest of that line plus every line after it. Both are verbatim, with
    trailing newlines dropped. The verdict word is from the first line
    starting ``VERDICT:`` (``unknown`` if none).

    Raises ``ReviewRecordError`` if no line starts ``Findings:`` or if the
    section matches a credential pattern.
    """
    lines = verdict_text.splitlines()
    findings_at = next((i for i, line in enumerate(lines) if line.startswith(_FINDINGS)), None)
    if findings_at is None:
        raise ReviewRecordError("the verdict has no 'Findings:' line; nothing to post")
    verdict = _verdict_word(lines)
    ran = _block(lines[:findings_at], _RAN)
    findings = _block(lines[findings_at:], _FINDINGS)
    section = (
        f"{MARKER}\n"
        "## Independent review\n\n"
        f"{_INTRO}\n\n"
        f"**Verdict:** {verdict}\n\n"
        f"**Ran:**\n\n{ran or '(not stated)'}\n\n"
        f"**Findings:**\n\n{findings or '(none listed)'}\n"
    )
    if any(_SECRET.search(line) for line in section.splitlines()):
        raise ReviewRecordError("the review text matches a credential pattern; not posted")
    return section


def has_marker(body: str) -> bool:
    """True if ``body`` holds the section's marker."""
    return MARKER in body


def compose_body(existing: str, section: str) -> str:
    """``existing``, a blank line, then ``section``, cut to fit ``BODY_LIMIT``.

    Lengths are UTF-8 bytes, never less than GitHub's character count, so
    the result always fits. Only ``section`` is cut, never ``existing``; a
    cut ends with a note saying so and never splits a multi-byte character.
    Raises ``ReviewRecordError`` if ``existing`` leaves room for fewer than
    ``MIN_KEPT_BYTES`` of the section.
    """
    body_n = len(existing.encode("utf-8"))
    section_bytes = section.encode("utf-8")
    room = BODY_LIMIT - 1 - body_n - 2  # 2 for the blank line between them
    if len(section_bytes) <= room:
        return f"{existing}\n\n{section}"
    keep = room - len(_TRUNCATED.encode("utf-8"))
    if keep < MIN_KEPT_BYTES:
        raise ReviewRecordError(f"the PR body leaves no room for the review section ({body_n} bytes)")
    cut = section_bytes[:keep].decode("utf-8", errors="ignore")
    return f"{existing}\n\n{cut}{_TRUNCATED}"


def _verdict_word(lines: list[str]) -> str:
    for line in lines:
        if line.startswith("VERDICT:"):
            word = _VERDICT_LINE.match(line)
            return word.group(1) if word and word.group(1) else "unknown"
    return "unknown"


def _block(lines: list[str], label: str) -> str:
    """The rest of the first line starting ``label`` plus every later line in ``lines``."""
    start = next((i for i, line in enumerate(lines) if line.startswith(label)), None)
    if start is None:
        return ""
    head = lines[start][len(label):].lstrip()
    body = ([head] if head else []) + lines[start + 1:]
    return "\n".join(body).rstrip("\n")
