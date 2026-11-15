"""Reads the open PR for a branch out of ``gh pr list --json number`` output.

Pure: no I/O. The shipper looks for an existing PR before creating one,
because the ``gh pr create`` manual does not say what happens when the
branch already has one; listing first makes a retried ``ship`` reuse it.
"""

from __future__ import annotations

import json


class PrListError(ValueError):
    """The list output is not a JSON array of ``{"number": <positive int>}``, or names more than one PR."""


def open_pr(list_json: str) -> int | None:
    """The one open PR's number, or ``None`` if the list is empty.

    Raises ``PrListError`` if the text is not a JSON array of objects with a
    positive int ``number``, or if it has more than one entry (the shipper
    will not guess which PR is this cycle's).
    """
    try:
        items = json.loads(list_json)
    except ValueError as exc:
        raise PrListError(f"gh pr list did not print JSON: {exc}") from exc
    if not isinstance(items, list):
        raise PrListError("gh pr list did not print a JSON array")
    numbers: list[int] = []
    for item in items:
        number = item.get("number") if isinstance(item, dict) else None
        if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
            raise PrListError(f"gh pr list gave an entry without a positive int number: {str(item)[:80]}")
        numbers.append(number)
    if len(numbers) > 1:
        raise PrListError(f"{len(numbers)} open PRs for one branch: {', '.join(f'#{n}' for n in numbers)}")
    return numbers[0] if numbers else None
