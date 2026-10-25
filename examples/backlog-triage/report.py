"""Renders a list of verdicts as the plain-text triage report.

Pure: verdicts in, string out.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from datetime import date

from classify import Actionable, Historical, Kind, Verdict

TEXT_WIDTH = 88


def _clip(text: str) -> str:
    return text if len(text) <= TEXT_WIDTH else text[: TEXT_WIDTH - 3] + "..."


def render_report(verdicts: Sequence[Verdict], today: date, max_age_days: int) -> str:
    """Header with raw/historical/actionable counts, historical counts per kind,
    then each actionable line with its reason. Ends with a newline. Never raises."""
    historical = [v for v in verdicts if isinstance(v, Historical)]
    actionable = [v for v in verdicts if isinstance(v, Actionable)]

    out = [
        f"backlog triage (today {today.isoformat()}, max age {max_age_days} days)",
        f"unclaimed: {len(verdicts)} raw, {len(historical)} historical, "
        f"{len(actionable)} actionable",
        "",
        "historical by kind:",
    ]
    per_kind = Counter(v.kind for v in historical)
    for kind in Kind:
        if per_kind[kind]:
            out.append(f"  {kind.value:<17} {per_kind[kind]:>3}")
    if not historical:
        out.append("  (none)")

    out += ["", "actionable:"]
    for v in actionable:
        out.append(f"  L{v.line_no:<4} {v.kind.value:<17} {v.reason}")
        out.append(f"        {_clip(v.text)}")
    if not actionable:
        out.append("  (none)")
    return "\n".join(out) + "\n"
