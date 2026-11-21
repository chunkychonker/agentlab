"""The problems the plugin checker reports.

Pure: no I/O. One enum member per defect, so a test can assert that a defect
produces exactly its own problem and nothing else.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class ProblemKind(enum.Enum):
    """One kind of defect in a plugin directory."""

    MANIFEST_MISSING = "manifest_missing"
    MANIFEST_INVALID_JSON = "manifest_invalid_json"
    MANIFEST_NO_NAME = "manifest_no_name"
    MANIFEST_BAD_NAME = "manifest_bad_name"
    MANIFEST_RESERVED_PREFIX = "manifest_reserved_prefix"
    MANIFEST_AGENTS_SET = "manifest_agents_set"
    MANIFEST_PATH_WRONG_TYPE = "manifest_path_wrong_type"
    MANIFEST_PATH_NOT_RELATIVE = "manifest_path_not_relative"
    MANIFEST_PATH_ESCAPES = "manifest_path_escapes"
    MANIFEST_PATH_MISSING = "manifest_path_missing"
    AGENT_FRONTMATTER_INVALID = "agent_frontmatter_invalid"
    AGENT_MISSING_FIELD = "agent_missing_field"
    AGENT_IGNORED_FIELD = "agent_ignored_field"
    AGENT_UNKNOWN_FIELD = "agent_unknown_field"
    HOOKS_INVALID_JSON = "hooks_invalid_json"
    HOOKS_NO_WRAPPER = "hooks_no_wrapper"
    HOOKS_MALFORMED = "hooks_malformed"
    HOOK_SCRIPT_MISSING = "hook_script_missing"
    BIN_NOT_EXECUTABLE = "bin_not_executable"


@dataclass(frozen=True)
class Problem:
    """One defect: what kind, which file (relative to the plugin root), and a one-line detail."""

    kind: ProblemKind
    where: str
    detail: str
