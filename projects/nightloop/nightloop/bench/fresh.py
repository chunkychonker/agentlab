"""Whether the recorded benchmark run measured the reviewer template as it is now.

Pure: no I/O. The CLI reads the template bytes and the results file and
hands them here. Named ``TemplateFreshness`` so it cannot be confused with
``nightloop.gates.Freshness`` (an artifact's mtime check).
"""

from __future__ import annotations

import enum
import hashlib
import json
import re

# The results file's key for the template hash (``report.to_json`` writes it).
TEMPLATE_SHA256_KEY = "template_sha256"
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")


class TemplateFreshness(enum.Enum):
    """FRESH only when a recorded run's template hash equals the current template's."""

    FRESH = "FRESH"
    STALE = "STALE"  # a run is recorded, for a different template
    NEVER_RUN = "NEVER_RUN"  # no run is recorded


def template_sha256(template: bytes) -> str:
    """The lowercase hex SHA-256 of the template file's bytes. Raises ``TypeError`` for non-bytes."""
    if not isinstance(template, bytes):
        raise TypeError(f"template must be bytes, got {type(template).__name__}")
    return hashlib.sha256(template).hexdigest()


def check_fresh(current_template: bytes, recorded_sha256: str | None) -> TemplateFreshness:
    """Compare the current template with the hash a results file recorded.

    ``None`` means there is no results file: ``NEVER_RUN``, never ``FRESH``.
    Raises ``ValueError`` if ``recorded_sha256`` is not 64 lowercase hex
    characters, and ``TypeError`` if ``current_template`` is not bytes.
    """
    current = template_sha256(current_template)
    if recorded_sha256 is None:
        return TemplateFreshness.NEVER_RUN
    _check_hex(recorded_sha256)
    return TemplateFreshness.FRESH if recorded_sha256 == current else TemplateFreshness.STALE


def recorded_sha256(results_json: str) -> str:
    """The template hash a results file recorded.

    Raises ``ValueError`` if the text is not a JSON object, has no
    ``template_sha256`` string, or that string is not 64 lowercase hex.
    """
    try:
        doc = json.loads(results_json)
    except ValueError as exc:
        raise ValueError(f"results file is not JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise ValueError("results file is not a JSON object")
    value = doc.get(TEMPLATE_SHA256_KEY)
    if not isinstance(value, str):
        raise ValueError(f"results file has no string {TEMPLATE_SHA256_KEY!r}")
    _check_hex(value)
    return value


def _check_hex(value: object) -> None:
    if not isinstance(value, str) or not _SHA256_HEX.fullmatch(value):
        raise ValueError(f"a recorded template hash must be 64 lowercase hex characters, got {value!r}")
