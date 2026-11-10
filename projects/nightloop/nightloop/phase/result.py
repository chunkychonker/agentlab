"""Parses the one JSON object ``claude -p --output-format json`` prints.

Pure: no I/O. The field list comes from agentlab's recorded fixtures
(``.pipeline/test_metrics.sh``) and ``.pipeline/metrics.sh``; the headless
docs do not publish a full schema, so only ``result`` is required.

Two kinds of field, two policies:

- ``result`` and ``is_error`` decide the phase's status. A missing or
  non-string ``result``, or an ``is_error`` that is present but not a bool,
  makes the whole output unreadable (``CliOutputError``): the runner fails
  closed rather than guess whether the phase succeeded.
- Every other field is metrics only, and metrics are a passenger (they must
  never change a phase's status). A metrics field that is absent, or present
  with a type the record cannot hold (say a string ``num_turns``), is
  ``None``, which the metrics line writes as JSON null, never 0.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping


class CliOutputError(ValueError):
    """stdout is not exactly one JSON object with a string ``result``."""


@dataclass(frozen=True)
class Usage:
    """Token counts from the CLI's ``usage`` object; ``None`` when absent."""

    input_tokens: int | None
    output_tokens: int | None
    cache_read_input_tokens: int | None
    cache_creation_input_tokens: int | None


@dataclass(frozen=True)
class CliResult:
    """What one ``claude -p`` run reported about itself.

    ``total_cost_usd`` is the CLI's client-side estimate at API prices; on a
    subscription it is a notional figure, not a bill. ``denials`` is the
    number of tool calls the permission rules refused (the length of
    ``permission_denials``), ``None`` when the CLI did not report it.
    """

    result: str
    is_error: bool | None
    duration_ms: int | None
    num_turns: int | None
    session_id: str | None
    total_cost_usd: float | None
    usage: Usage
    denials: int | None


def parse_cli_json(stdout: str) -> CliResult:
    """Parse the CLI's stdout into a ``CliResult``.

    Surrounding whitespace is allowed. Raises ``CliOutputError`` if
    ``stdout`` is empty, is not valid JSON, holds more than one JSON value,
    contains ``NaN``/``Infinity`` or an integer too long for ``int``, is
    not an object, has no string ``result``, or has an ``is_error`` that is
    not a bool. Never raises anything else for a ``str`` input.
    """
    try:
        doc = json.loads(stdout, parse_constant=_reject_constant)
    except ValueError as exc:
        # JSONDecodeError, a rejected NaN/Infinity, or an integer longer than
        # the interpreter's int-digits limit: all ValueError subclasses.
        raise CliOutputError(f"stdout is not one JSON value: {exc}") from exc
    except RecursionError as exc:
        raise CliOutputError("stdout is nested too deeply to parse") from exc
    if not isinstance(doc, dict):
        raise CliOutputError(f"stdout is a JSON {type(doc).__name__}, not an object")
    result = doc.get("result")
    if not isinstance(result, str):
        raise CliOutputError("stdout has no string 'result' field")
    is_error = doc.get("is_error")
    if is_error is not None and not isinstance(is_error, bool):
        raise CliOutputError(f"'is_error' must be a bool, got {type(is_error).__name__}")

    usage = doc.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    denials = doc.get("permission_denials")
    return CliResult(
        result=result,
        is_error=is_error,
        duration_ms=_int_or_none(doc, "duration_ms"),
        num_turns=_int_or_none(doc, "num_turns"),
        session_id=_str_or_none(doc, "session_id"),
        total_cost_usd=_number_or_none(doc, "total_cost_usd"),
        usage=Usage(
            input_tokens=_int_or_none(usage, "input_tokens"),
            output_tokens=_int_or_none(usage, "output_tokens"),
            cache_read_input_tokens=_int_or_none(usage, "cache_read_input_tokens"),
            cache_creation_input_tokens=_int_or_none(usage, "cache_creation_input_tokens"),
        ),
        denials=len(denials) if isinstance(denials, list) else None,
    )


class _NonFiniteNumber(ValueError):
    pass


def _reject_constant(name: str) -> Any:
    # NaN/Infinity are not JSON, and a metrics line holding one would not be either.
    raise _NonFiniteNumber(f"non-finite number {name}")


def _int_or_none(doc: Mapping[str, Any], key: str) -> int | None:
    value = doc.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _number_or_none(doc: Mapping[str, Any], key: str) -> float | None:
    value = doc.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _str_or_none(doc: Mapping[str, Any], key: str) -> str | None:
    value = doc.get(key)
    return value if isinstance(value, str) else None
