"""What the workflow is built from, as values that cannot hold an unsafe or contradictory field.

Pure: no I/O. ``WorkflowSpec`` checks every field against a strict pattern
when it is built, so every string it holds is safe to place in YAML and in a
shell line unquoted (I8: values that do not match are refused, never
escaped). ``validate_spec`` adds the checks that need the config (the slot
is in the window, the night fits the job's time limit) and is the only way
to get a ``ValidSpec``, the only type ``render_workflow`` accepts.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field

from ..model import PHASE_POSTCONDITIONS, Config
from ..schedule_slot import SlotError, slot_for
from .budget import check_budget
from .errors import SpecError

# GitHub owner (letters, digits, single hyphens, 1-39) / repo name.
_REPO = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9_.-]{1,100}")
_SHA = re.compile(r"[0-9a-f]{40}")
_VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
# "UTC", or an IANA Area/Location[/Sub] name. Whether the zone exists is
# checked at the edge (zoneinfo); this only keeps the text inert.
_TIMEZONE = re.compile(r"UTC|[A-Z][A-Za-z_]+(?:/[A-Za-z0-9_+-]+){1,2}")
# A repo-relative path of plain segments: no leading '-', '/', '.' or '..' segment.
_SEGMENT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*|\.[A-Za-z0-9_][A-Za-z0-9_.-]*")
_STEM = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
_MINUTE_MIN, _MINUTE_MAX = 1, 59  # :00 is GitHub's named high-load minute


class Credential(enum.Enum):
    """Which one Claude credential the job exports. The value is the env/secret name.

    ``claude -p`` uses ``ANTHROPIC_API_KEY`` whenever it is set, even with an
    OAuth token present, so the workflow carries exactly one of these.
    """

    OAUTH_TOKEN = "CLAUDE_CODE_OAUTH_TOKEN"
    API_KEY = "ANTHROPIC_API_KEY"


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check_pattern(name: str, value: object, pattern: re.Pattern[str], expected: str) -> None:
    if not isinstance(value, str):
        raise SpecError(name, f"must be a string, got {type(value).__name__}")
    if not pattern.fullmatch(value):
        raise SpecError(name, f"must be {expected}, got {value!r}")


def _check_int(name: str, value: object, lo: int, hi: int | None) -> None:
    if not _is_int(value) or value < lo or (hi is not None and value > hi):
        bound = f">= {lo}" if hi is None else f"from {lo} to {hi}"
        raise SpecError(name, f"must be an integer {bound}, got {value!r}")


def _check_config_path(value: object) -> None:
    if not isinstance(value, str):
        raise SpecError("config_path", f"must be a string, got {type(value).__name__}")
    segments = value.split("/")
    if value == "" or not all(_SEGMENT.fullmatch(s) for s in segments):
        raise SpecError(
            "config_path",
            "must be a path inside the repo made of plain segments "
            f"(letters, digits, '_', '.', '-'; none starting with '-' or being '.'/'..'), got {value!r}",
        )


@dataclass(frozen=True)
class WorkflowSpec:
    """Everything the workflow needs beyond the config. Every field is checked when it is built.

    Raises ``SpecError`` naming the field when:
    ``nightloop_repo`` is not ``owner/name``; ``nightloop_sha`` is not 40
    lowercase hex; ``claude_version`` is not ``X.Y.Z``; ``credential`` is not
    a ``Credential``; ``timezone`` is not ``UTC`` or ``Area/Location``;
    ``hour`` is neither ``None`` nor an int 0-23; ``minute`` is not an int
    1-59; ``timeout_s`` or ``job_timeout_minutes`` is not an int >= 1;
    ``config_path`` is not a plain repo-relative path; ``workflow_stem`` is
    not ``[a-z0-9][a-z0-9-]*`` (at most 63 chars). ``bool`` is never an int.
    """

    nightloop_repo: str
    nightloop_sha: str
    claude_version: str
    credential: Credential
    timezone: str
    hour: int | None
    minute: int
    timeout_s: int
    job_timeout_minutes: int
    config_path: str
    workflow_stem: str

    def __post_init__(self) -> None:
        _check_pattern("nightloop_repo", self.nightloop_repo, _REPO, "a GitHub repo as owner/name")
        _check_pattern("nightloop_sha", self.nightloop_sha, _SHA, "a full 40-character lowercase commit SHA")
        _check_pattern("claude_version", self.claude_version, _VERSION, "an exact version X.Y.Z")
        if not isinstance(self.credential, Credential):
            raise SpecError("credential", f"must be a Credential, got {type(self.credential).__name__}")
        _check_pattern("timezone", self.timezone, _TIMEZONE, "UTC or an IANA zone like Area/City")
        if self.hour is not None:
            _check_int("hour", self.hour, 0, 23)
        _check_int("minute", self.minute, _MINUTE_MIN, _MINUTE_MAX)
        _check_int("timeout_s", self.timeout_s, 1, None)
        _check_int("job_timeout_minutes", self.job_timeout_minutes, 1, None)
        _check_config_path(self.config_path)
        _check_pattern("workflow_stem", self.workflow_stem, _STEM, "lowercase letters, digits and '-'")


_SEAL = object()


@dataclass(frozen=True)
class ValidSpec:
    """A ``WorkflowSpec`` that has passed ``validate_spec`` against a config.

    ``hour``/``minute`` are the slot (inside the window); ``cycles`` and
    ``needed_minutes`` are what the budget was checked with. Built only by
    ``validate_spec``: constructing it directly raises ``TypeError``.
    """

    spec: WorkflowSpec
    hour: int
    minute: int
    cycles: int
    phases: int
    needed_minutes: int
    _seal: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._seal is not _SEAL:
            raise TypeError("ValidSpec is built only by validate_spec")


def validate_spec(cfg: Config, spec: WorkflowSpec) -> ValidSpec:
    """Check ``spec`` against ``cfg`` and return the ``ValidSpec`` to render.

    Raises ``SpecError("hour", ...)`` if the slot (``spec.hour``, default the
    window's start) is outside ``cfg.window``, and
    ``SpecError("job_timeout_minutes", ...)`` if the night's worst case
    (``cfg.cycles`` x 3 phases x ``spec.timeout_s``) plus slack does not
    fit ``spec.job_timeout_minutes`` or that exceeds the hosted limit.
    """
    try:
        hour, minute = slot_for(cfg.window, spec.hour, spec.minute)
    except SlotError as exc:
        name, _, reason = str(exc).partition(": ")
        raise SpecError(name, reason) from None
    phases = len(PHASE_POSTCONDITIONS)
    needed = check_budget(cfg.cycles, phases, spec.timeout_s, spec.job_timeout_minutes)
    return ValidSpec(spec=spec, hour=hour, minute=minute, cycles=cfg.cycles, phases=phases,
                     needed_minutes=needed, _seal=_SEAL)
