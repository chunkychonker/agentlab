"""Turns the reviewer's verdict text into the PR body that carries the review record.

Pure: no I/O. A port of agentlab's ``.pipeline/review_record.sh``: the same
marker, the same ``## Independent review`` section (the verdict word, then
the verdict's ``Ran:`` and ``Findings:`` blocks verbatim), the same
credential patterns, and the same truncation rule (only the review section
is cut, never the summary above it; lengths are counted in UTF-8 bytes,
which is never less than GitHub's character count; a cut never splits a
character; fewer than 200 bytes left is a refusal).

One difference from bash: bash adds the section to a PR the maintainer
already opened. Here the shipper opens the PR, so the section goes into the
body at creation and the summary is the shipper's own text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Union

# The hidden line that marks a PR body as carrying the review record.
REVIEW_MARKER: Final[str] = "<!-- agentlab-review -->"
# GitHub refuses a PR body longer than this many characters.
BODY_LIMIT: Final[int] = 65536
# Fewer bytes than this left for the section is a refusal, not a stub.
MIN_SECTION_BYTES: Final[int] = 200

_INTRO = (
    "Written by the reviewer, a separate model run that did not write this code, "
    "before the PR was opened. Posted by nightloop when it opened this PR; "
    "nothing merges it automatically."
)
_TRUNCATION_NOTE = (
    f"\n\n_(Truncated to fit GitHub's {BODY_LIMIT}-character PR body limit. "
    "The full verdict is in the reviewer's verdict file.)_"
)
_SEPARATOR = "\n\n"
_VERDICT_LINE = re.compile(r"VERDICT:[ \t\r\f\v]*([A-Za-z]*)")
_RAN_PREFIX = re.compile(r"Ran:[ \t\r\f\v]*")
_FINDINGS_PREFIX = re.compile(r"Findings:[ \t\r\f\v]*")
# The reviewer's list made concrete, as review_record_secret_hit: Anthropic
# (sk-ant-), other sk- keys, GitHub tokens, AWS access key ids, Slack tokens,
# PEM private keys, and KEY=value lines for names that hold secrets. Bare
# long hex is not matched: findings cite commit SHAs.
_SECRET = re.compile(
    r"sk-ant-[A-Za-z0-9_-]{8,}"
    r"|sk-[A-Za-z0-9]{20,}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|xox[abposr]-[A-Za-z0-9-]{10,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|(?:API_KEY|OAUTH_TOKEN|SECRET|PASSWORD|ACCESS_TOKEN)[A-Z_]*[ \t\r\f\v]*=[ \t\r\f\v]*[^\s$<]{8,}"
)


def has_marker(body: str) -> bool:
    """True if ``body`` carries the review record's marker."""
    return REVIEW_MARKER in body


@dataclass(frozen=True)
class PrBody:
    """A PR body that carries the review record and fits GitHub's limit.

    Raises ``ValueError`` if ``text`` lacks the marker or is ``BODY_LIMIT``
    UTF-8 bytes or longer, so a body that would fail the read-back check or
    be refused by GitHub cannot be built.
    """

    text: str

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TypeError(f"text must be a str, got {type(self.text).__name__}")
        if not has_marker(self.text):
            raise ValueError("a PR body must carry the review record marker")
        if len(self.text.encode("utf-8")) >= BODY_LIMIT:
            raise ValueError(f"a PR body must be under {BODY_LIMIT} bytes")


@dataclass(frozen=True)
class RecordRefused:
    """No PR body may be posted; ``reason`` (one line) says why."""

    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.reason, str) or self.reason.strip() == "" or "\n" in self.reason:
            raise ValueError(f"reason must be one non-empty line, got {self.reason!r}")


ComposeOutcome = Union[PrBody, RecordRefused]


def compose_pr_body(summary: str, verdict_text: str) -> ComposeOutcome:
    """The PR body: ``summary``, a blank line, then the review section from ``verdict_text``.

    Refusals (``RecordRefused``, never an exception):

    - ``verdict_text`` has no line starting ``Findings:``.
    - The summary or the section matches a credential pattern (nothing of
      the review text is returned).
    - The summary leaves fewer than ``MIN_SECTION_BYTES`` for the section.

    A section too long for the limit is cut, and ends with a note saying so.
    """
    section = _review_section(verdict_text)
    if section is None:
        return RecordRefused("the verdict has no 'Findings:' line, so there is no review record to post")
    if _SECRET.search(summary) or _SECRET.search(section):
        return RecordRefused("the review record matches a credential pattern; nothing was posted")
    # 1 byte below the limit, as bash, minus the separator.
    room = BODY_LIMIT - 1 - len(summary.encode("utf-8")) - len(_SEPARATOR)
    section_bytes = section.encode("utf-8")
    if len(section_bytes) <= room:
        return PrBody(summary + _SEPARATOR + section)
    keep = room - len(_TRUNCATION_NOTE.encode("utf-8"))
    if keep < MIN_SECTION_BYTES:
        return RecordRefused("the PR summary leaves no room for the review record")
    # "ignore" drops only the character the cut split: the input was valid UTF-8.
    cut = section_bytes[:keep].decode("utf-8", errors="ignore")
    return PrBody(summary + _SEPARATOR + cut + _TRUNCATION_NOTE)


def _review_section(verdict_text: str) -> str | None:
    """The ``## Independent review`` section for ``verdict_text``, or ``None`` if it has no ``Findings:`` line.

    Ran is every line from the first ``Ran:`` up to the first ``Findings:``;
    Findings is every line after the first ``Findings:`` to the end. Each
    block's first line loses its label. A ``Spec:`` block before ``Ran:``
    is not part of the section. Unlike the bash ``awk``, a second ``Ran:``
    or ``Findings:`` line inside a block is kept once, verbatim, not twice.
    """
    lines = verdict_text.split("\n")
    if not any(line.startswith("Findings:") for line in lines):
        return None
    verdict = ""
    for line in lines:
        match = _VERDICT_LINE.match(line)
        if match:
            verdict = match.group(1)
            break
    ran: list[str] = []
    findings: list[str] = []
    in_ran = False
    in_findings = False
    for line in lines:
        if in_findings:
            findings.append(line)
        elif line.startswith("Findings:"):
            _append_rest(findings, _FINDINGS_PREFIX.sub("", line, count=1))
            in_findings = True
        elif in_ran:
            ran.append(line)
        elif line.startswith("Ran:"):
            _append_rest(ran, _RAN_PREFIX.sub("", line, count=1))
            in_ran = True
    # As bash's $(...), trailing newlines are dropped.
    ran_text = "\n".join(ran).rstrip("\n") or "(not stated)"
    findings_text = "\n".join(findings).rstrip("\n") or "(none listed)"
    return (
        f"{REVIEW_MARKER}\n"
        "## Independent review\n\n"
        f"{_INTRO}\n\n"
        f"**Verdict:** {verdict or 'unknown'}\n\n"
        f"**Ran:**\n\n{ran_text}\n\n"
        f"**Findings:**\n\n{findings_text}\n"
    )


def _append_rest(block: list[str], rest: str) -> None:
    """Start a block with the text after its label, if there is any."""
    if rest != "":
        block.append(rest)
