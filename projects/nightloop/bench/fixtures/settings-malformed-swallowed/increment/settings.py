"""A JSON settings loader: defaults when the file is absent, an error when it is wrong."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_KEYS = ("retries", "verbose", "name")
_MAX_RETRIES = 10


class SettingsError(ValueError):
    """The settings file exists but cannot be used."""


@dataclass(frozen=True)
class Settings:
    retries: int = 3
    verbose: bool = False
    name: str = "tool"


def load_settings(path: Path) -> Settings:
    """Load ``path``; defaults if it does not exist, ``SettingsError`` if it is invalid."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Settings()
    return _from_object(path, raw)


def _from_object(path: Path, raw: object) -> Settings:
    if not isinstance(raw, dict):
        raise SettingsError(f"{path}: must hold a JSON object, got {type(raw).__name__}")
    unknown = sorted(set(raw) - set(_KEYS))
    if unknown:
        raise SettingsError(f"{path}: unknown key(s) {', '.join(unknown)}")
    values = {**Settings().__dict__, **raw}
    retries = values["retries"]
    if isinstance(retries, bool) or not isinstance(retries, int) or not 0 <= retries <= _MAX_RETRIES:
        raise SettingsError(f"{path}: retries must be an int in 0..{_MAX_RETRIES}, got {retries!r}")
    if not isinstance(values["verbose"], bool):
        raise SettingsError(f"{path}: verbose must be a bool, got {values['verbose']!r}")
    name = values["name"]
    if not isinstance(name, str) or name.strip() == "":
        raise SettingsError(f"{path}: name must be a non-empty string, got {name!r}")
    return Settings(retries=retries, verbose=values["verbose"], name=name)
