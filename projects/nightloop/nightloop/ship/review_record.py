"""Turns the review verdict's text into the section the PR body carries.

Pure: no I/O. A port of agentlab's ``.pipeline/review_record.sh``
(``review_record_section``, ``review_record_secret_hit``,
``review_record_compose``), with nightloop's own marker so the two tools
never mistake each other's PRs.
"""

from __future__ import annotations

import re

# The hidden line that marks a PR body as carrying the record. Named once.
REVIEW_MARKER = "<!-- nightloop-review -->"
# GitHub refuses a PR body longer than this many characters.
BODY_LIMIT = 65536
# Below this many bytes of room the section would be mostly a truncation note.
MIN_SECTION_BYTES = 200

_SEPARATOR = "\n\n"
_TRUNCATION_NOTE = (
    f"\n\n_(Truncated to fit GitHub's {BODY_LIMIT}-character PR body limit. "
    "The full verdict is in the run's verdict file.)_"
)
_BLURB = (
    "Written by nightloop's review phase, a separate model run that did not write this code, "
    "before the PR was opened. Posted by nightloop's shipper as part of opening the PR."
)

# bash's [[:space:]] within one line (grep and awk never see the newline).
_SP = r"[ \t\r\f\v]"
_VERDICT_LINE = re.compile(rf"VERDICT:{_SP}*([A-Za-z]*)")
_RAN_PREFIX = re.compile(rf"Ran:{_SP}*")
_FINDINGS_PREFIX = re.compile(rf"Findings:{_SP}*")
# The reviewer's credential list, ported verbatim from review_record_secret_hit.
# Bare long hex is deliberately absent: findings cite commit SHAs.
_SECRET = re.compile(
    r"sk-ant-[A-Za-z0-9_-]{8,}"
    r"|sk-[A-Za-z0-9]{20,}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|xox[abposr]-[A-Za-z0-9-]{10,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    rf"|(API_KEY|OAUTH_TOKEN|SECRET|PASSWORD|ACCESS_TOKEN)[A-Z_]*{_SP}*={_SP}*[^ \t\r\f\v\n$<]{{8,}}"
)


def _lines(text: str) -> list[str]:
    """The text's lines as grep and awk see them (``\\n`` only; no empty last line)."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _block(lines: list[str], prefix: re.Pattern[str], stop: re.Pattern[str] | None) -> str:
    """bash's awk: every line after a ``prefix`` line (plus that line's remainder), up to ``stop``.

    Like ``$(...)``, trailing newlines are dropped.
    """
    out: list[str] = []
    on = False
    for line in lines:
        if stop is not None and stop.match(line):
            break
        if on:
            out.append(line)
        head = prefix.match(line)
        if head:
            rest = line[head.end():]
            if rest != "":
                out.append(rest)
            on = True
    return "\n".join(out).rstrip("\n")


def review_section(verdict_text: str) -> str | None:
    """The marker, an "## Independent review" heading, the verdict word, and the Ran and Findings blocks.

    Ran is every line from ``Ran:`` up to the first ``Findings:`` line;
    Findings is every line after ``Findings:`` to the end. Both are copied
    verbatim. Returns ``None`` if no line starts with ``Findings:``: a
    verdict without findings is not one the reviewer's contract allows, so
    it is not posted as if it were. Raises ``TypeError`` if
    ``verdict_text`` is not a str.
    """
    if not isinstance(verdict_text, str):
        raise TypeError(f"verdict_text must be a str, got {type(verdict_text).__name__}")
    lines = _lines(verdict_text)
    if not any(_FINDINGS_PREFIX.match(line) for line in lines):
        return None
    verdict = next((m.group(1) for m in map(_VERDICT_LINE.match, lines) if m), "")
    ran = _block(lines, _RAN_PREFIX, _FINDINGS_PREFIX)
    findings = _block(lines, _FINDINGS_PREFIX, None)
    return (
        f"{REVIEW_MARKER}\n"
        "## Independent review\n\n"
        f"{_BLURB}\n\n"
        f"**Verdict:** {verdict or 'unknown'}\n\n"
        f"**Ran:**\n\n{ran or '(not stated)'}\n\n"
        f"**Findings:**\n\n{findings or '(none listed)'}\n"
    )


def has_secret(text: str) -> bool:
    """True if ``text`` matches a credential pattern (Anthropic, GitHub, AWS, Slack, PEM, KEY=value).

    Raises ``TypeError`` if ``text`` is not a str.
    """
    if not isinstance(text, str):
        raise TypeError(f"text must be a str, got {type(text).__name__}")
    return _SECRET.search(text) is not None


def compose_body(base: str, section: str) -> str | None:
    """``base``, a blank line, then ``section``, cut to fit ``BODY_LIMIT``.

    Only the section is ever cut, never ``base``, and a cut section ends
    with a note saying so. Lengths are UTF-8 bytes, which is never less than
    GitHub's character count, so the result always fits; a cut never splits
    a character. Returns ``None`` if fewer than ``MIN_SECTION_BYTES`` of the
    section would survive. Raises ``TypeError`` for non-str arguments.
    """
    if not isinstance(base, str) or not isinstance(section, str):
        raise TypeError("base and section must be str")
    raw = section.encode("utf-8")
    room = BODY_LIMIT - 1 - len(base.encode("utf-8")) - len(_SEPARATOR)
    if len(raw) <= room:
        return f"{base}{_SEPARATOR}{section}"
    keep = room - len(_TRUNCATION_NOTE.encode("utf-8"))
    if keep < MIN_SECTION_BYTES:
        return None
    cut = raw[:keep].decode("utf-8", errors="ignore")
    return f"{base}{_SEPARATOR}{cut}{_TRUNCATION_NOTE}"
