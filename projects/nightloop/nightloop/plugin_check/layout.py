"""Where a Claude Code plugin keeps its parts, relative to the plugin root.

Pure: no I/O. Named once so the tree reader and the rules agree on which
files exist and which are read. Source: the Claude Code plugins reference and
components pages, read 2026-11-21 (research/2026-11-21-nightloop-plugin.md).
"""

from __future__ import annotations

from typing import Final

MANIFEST: Final[str] = ".claude-plugin/plugin.json"
HOOKS_FILE: Final[str] = "hooks/hooks.json"
AGENTS_DIR: Final[str] = "agents"
AGENT_SUFFIX: Final[str] = ".md"
BIN_DIR: Final[str] = "bin"


def parent_and_name(rel: str) -> tuple[str, str]:
    """``"agents/builder.md"`` -> ``("agents", "builder.md")``; a top-level name has parent ``""``."""
    parent, _, name = rel.rpartition("/")
    return parent, name


def is_agent_file(rel: str) -> bool:
    """True for ``agents/<name>.md``, a direct child of ``agents/`` only."""
    parent, name = parent_and_name(rel)
    return parent == AGENTS_DIR and name.endswith(AGENT_SUFFIX)


def is_bin_entry(rel: str) -> bool:
    """True for ``bin/<name>``, a direct child of ``bin/`` only."""
    parent, _ = parent_and_name(rel)
    return parent == BIN_DIR


def wants_text(rel: str) -> bool:
    """True for the files whose contents the rules read."""
    return rel in (MANIFEST, HOOKS_FILE) or is_agent_file(rel)
