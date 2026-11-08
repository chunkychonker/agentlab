"""The single exception type raised when a nightloop config is invalid."""

from __future__ import annotations

# Pseudo-field names for errors that are not about one key of the document.
FIELD_FILE = "<file>"  # the file could not be read, or is too large
FIELD_TOML = "<toml>"  # the bytes are not a parseable TOML document


class ConfigError(ValueError):
    """A config value, or the config document itself, is invalid.

    ``field`` is the dotted path of the offending key as written in
    ``nightloop.toml`` (e.g. ``run.cycles`` or ``phase.build.allowed_tools[2]``),
    or one of ``FIELD_FILE`` / ``FIELD_TOML``. ``str(err)`` is
    ``"<field>: <message>"``.
    """

    def __init__(self, field: str, message: str) -> None:
        super().__init__(f"{field}: {message}")
        self.field = field
        self.message = message
