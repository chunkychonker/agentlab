"""The single exception the GitHub Actions generator raises for a bad input."""

from __future__ import annotations


class SpecError(ValueError):
    """One workflow input is invalid; nothing has been rendered or written.

    ``field`` names the input (``timezone``, ``hour``, ``job_timeout_minutes``,
    ...). ``reason`` is one line for a person. ``str(err)`` is
    ``"<field>: <reason>"``.
    """

    def __init__(self, field: str, reason: str) -> None:
        super().__init__(f"{field}: {reason}")
        self.field = field
        self.reason = reason
