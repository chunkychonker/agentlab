"""Pure results join: batch result lines are matched back to eval cases by
custom_id and graded.

Input lines are plain mappings in the results-JSONL shape (what
``json.loads`` gives for a file line, or ``model_dump(mode="json")`` gives for
an SDK object). No SDK import, no I/O.
See research/2026-10-19-message-batches-eval.md.
"""

from __future__ import annotations

import enum
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Union

from plan import EvalCase

# The one error type that must be fixed before resending (batch processing
# guide, fetched 2026-10-19). Every other error type can be retried as-is.
NON_RETRYABLE_ERROR_TYPE = "invalid_request_error"


class UnknownCustomId(LookupError):
    """A result line names a custom_id that no case has."""


class MissingResult(LookupError):
    """A case has no result line."""


class DuplicateResult(LookupError):
    """Two result lines carry the same custom_id."""


class MalformedResult(ValueError):
    """A result line does not have the documented shape."""


@dataclass(frozen=True)
class Succeeded:
    text: str  # concatenation of the message's text blocks, in order
    input_tokens: int
    output_tokens: int
    service_tier: str | None  # usage.service_tier; None if the API omitted it


@dataclass(frozen=True)
class Errored:
    error_type: str
    request_id: str | None

    @property
    def retryable(self) -> bool:
        """Derived from error_type, so the two can never disagree."""
        return self.error_type != NON_RETRYABLE_ERROR_TYPE


@dataclass(frozen=True)
class Canceled:
    pass


@dataclass(frozen=True)
class Expired:
    pass


Outcome = Union[Succeeded, Errored, Canceled, Expired]


class Grade(enum.Enum):
    PASS = "pass"
    FAIL = "fail"
    NO_RESULT = "no_result"


def _parse_outcome(custom_id: str, result: Mapping) -> Outcome:
    """Raises MalformedResult on an unknown type or a missing field."""
    try:
        kind = result["type"]
        if kind == "succeeded":
            message = result["message"]
            text = "".join(
                block["text"] for block in message["content"] if block["type"] == "text"
            )
            usage = message["usage"]
            return Succeeded(
                text=text,
                input_tokens=usage["input_tokens"],
                output_tokens=usage["output_tokens"],
                service_tier=usage.get("service_tier"),
            )
        if kind == "errored":
            response = result["error"]
            return Errored(
                error_type=response["error"]["type"],
                request_id=response.get("request_id"),
            )
        if kind == "canceled":
            return Canceled()
        if kind == "expired":
            return Expired()
    except (KeyError, TypeError) as exc:
        raise MalformedResult(
            f"result for {custom_id!r} is missing a field: {exc!r}"
        ) from exc
    raise MalformedResult(f"result for {custom_id!r} has unknown type {kind!r}")


def join_results(
    cases: Sequence[EvalCase], result_lines: Iterable[Mapping]
) -> list[tuple[EvalCase, Outcome]]:
    """Pair every case with its outcome, in case order (not result order).

    Raises (each names the offending id; no partial list is returned):
        MalformedResult: a line lacks ``custom_id``/``result`` or has an
            unknown result type.
        DuplicateResult: two lines share a custom_id.
        UnknownCustomId: a line's custom_id matches no case.
        MissingResult: a case has no line.
    """
    case_ids = {case.id for case in cases}
    by_id: dict[str, Outcome] = {}
    for line in result_lines:
        try:
            custom_id = line["custom_id"]
            result = line["result"]
        except (KeyError, TypeError) as exc:
            raise MalformedResult(f"result line lacks {exc!r}: {line!r}") from exc
        if custom_id in by_id:
            raise DuplicateResult(f"duplicate result for custom_id {custom_id!r}")
        if custom_id not in case_ids:
            raise UnknownCustomId(f"result for unknown custom_id {custom_id!r}")
        by_id[custom_id] = _parse_outcome(custom_id, result)

    joined = []
    for case in cases:
        if case.id not in by_id:
            raise MissingResult(f"no result for case {case.id!r}")
        joined.append((case, by_id[case.id]))
    return joined


def grade(case: EvalCase, outcome: Outcome) -> Grade:
    """PASS iff the outcome succeeded and its text contains the expected
    substring (case-insensitive). A succeeded outcome without it is FAIL.
    Errored, canceled and expired are NO_RESULT, never FAIL: the model was
    never judged. Never raises.
    """
    if not isinstance(outcome, Succeeded):
        return Grade.NO_RESULT
    if case.expected_substring.casefold() in outcome.text.casefold():
        return Grade.PASS
    return Grade.FAIL
