"""Pure batch planning: eval cases become Message Batches request dicts, and
a batch's processing_status becomes a poll decision.

No SDK import, no I/O, no clock. The shell (batch_cli.py) owns all of that.
See research/2026-10-19-message-batches-eval.md.
"""

from __future__ import annotations

import enum
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass

# Documented limits (batch processing guide, fetched 2026-10-19).
CUSTOM_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
MAX_REQUESTS_PER_BATCH = 100_000
MAX_BATCH_BYTES = 256 * 1024 * 1024


class InvalidCustomId(ValueError):
    """An eval case id that the API would reject as a custom_id."""


class DuplicateCaseId(ValueError):
    """Two eval cases share an id, so their results could not be told apart."""


class BatchTooLarge(ValueError):
    """The batch exceeds the documented request-count or byte limit."""


@dataclass(frozen=True)
class EvalCase:
    """One prompt plus the substring a correct answer must contain.

    Raises:
        InvalidCustomId: ``id`` does not match ``CUSTOM_ID_PATTERN``.
        ValueError: ``prompt`` or ``expected_substring`` is empty (an empty
            expected substring would make every answer pass).
    """

    id: str
    prompt: str
    expected_substring: str

    def __post_init__(self) -> None:
        if not CUSTOM_ID_PATTERN.fullmatch(self.id):
            raise InvalidCustomId(
                f"case id {self.id!r} must match {CUSTOM_ID_PATTERN.pattern}"
            )
        if not self.prompt:
            raise ValueError(f"case {self.id!r} has an empty prompt")
        if not self.expected_substring:
            raise ValueError(f"case {self.id!r} has an empty expected_substring")


def build_requests(
    cases: Sequence[EvalCase], model: str, max_tokens: int
) -> list[dict]:
    """Turn cases into the ``requests`` list for ``messages.batches.create``.

    Each request is ``{"custom_id": case.id, "params": {...}}`` with a single
    user message. Output order equals input order (the API does not keep it,
    which is why join.py joins on custom_id).

    Raises:
        ValueError: ``cases`` is empty, ``model`` is empty, or ``max_tokens < 1``
            (the batch API rejects ``max_tokens: 0``).
        DuplicateCaseId: two cases share an id; names the id.
        BatchTooLarge: more than MAX_REQUESTS_PER_BATCH cases, or the JSON
            payload exceeds MAX_BATCH_BYTES.
    Nothing is returned on any failure; there is no partial list.
    """
    if not cases:
        raise ValueError("a batch needs at least one case")
    if not model:
        raise ValueError("model must be a non-empty model id")
    if max_tokens < 1:
        raise ValueError(f"max_tokens must be >= 1, got {max_tokens}")
    if len(cases) > MAX_REQUESTS_PER_BATCH:
        raise BatchTooLarge(
            f"{len(cases)} requests exceeds the limit of {MAX_REQUESTS_PER_BATCH}"
        )
    seen: set[str] = set()
    for case in cases:
        if case.id in seen:
            raise DuplicateCaseId(f"duplicate case id {case.id!r}")
        seen.add(case.id)

    requests = [
        {
            "custom_id": case.id,
            "params": {
                "model": model,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": case.prompt}],
            },
        }
        for case in cases
    ]
    size = len(json.dumps(requests).encode("utf-8"))
    if size > MAX_BATCH_BYTES:
        raise BatchTooLarge(f"{size} bytes exceeds the limit of {MAX_BATCH_BYTES}")
    return requests


class PollDecision(enum.Enum):
    WAIT = "wait"
    FETCH = "fetch"


_STATUS_TO_DECISION = {
    "in_progress": PollDecision.WAIT,
    "canceling": PollDecision.WAIT,
    "ended": PollDecision.FETCH,
}


def poll_decision(processing_status: str) -> PollDecision:
    """Map a batch's ``processing_status`` to WAIT or FETCH.

    ``in_progress`` and ``canceling`` mean WAIT; ``ended`` means FETCH.

    Raises:
        ValueError: any other status. An API change then fails loudly instead
            of polling forever.
    """
    try:
        return _STATUS_TO_DECISION[processing_status]
    except KeyError:
        raise ValueError(
            f"unknown processing_status {processing_status!r}; "
            f"expected one of {sorted(_STATUS_TO_DECISION)}"
        ) from None
