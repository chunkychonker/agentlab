"""Decides which problems a ``PluginTree`` has.

Pure: no I/O. Covers the defects that ``claude plugin validate --strict``
does not report but that break a plugin at load or at run time: an
ignored agent field, a hook script that does not exist, a ``bin/`` entry
that is not executable (all three confirmed unreported on CLI 2.1.329,
2026-11-21), plus the manifest and hooks-file shape rules from the plugins
reference. Stricter than Claude Code in one way: the manifest is required,
because nightloop's plugin always ships one.
"""

from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from typing import Iterator

from .frontmatter import FrontmatterError, parse_frontmatter
from .layout import HOOKS_FILE, MANIFEST, is_agent_file, is_bin_entry
from .problem import Problem, ProblemKind as K
from .snapshot import PluginTree

_KEBAB = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_RESERVED_PREFIXES = ("claude-", "anthropic-")
# Manifest keys whose values are paths to components (a string or a list of
# strings). An object value is an inline config, which has no path to check.
_PATH_KEYS = ("commands", "agents", "skills", "hooks", "mcpServers", "outputStyles", "lspServers")
# Setting this key replaces the default agents/ scan instead of adding to it.
_REPLACES_SCAN = "agents"
_RELATIVE = "./"

# Plugin agents: honoured fields, and fields Claude Code ignores for plugin
# agents for security (the sub-agents and plugin components pages).
SUPPORTED_AGENT_FIELDS = frozenset({
    "name", "description", "model", "effort", "maxTurns", "autoCompactWindow", "tools",
    "disallowedTools", "skills", "memory", "background", "omitClaudeMd", "isolation", "color",
})
IGNORED_AGENT_FIELDS = frozenset({"permissionMode", "hooks", "mcpServers", "initialPrompt"})
_REQUIRED_AGENT_FIELDS = ("name", "description")

_PLUGIN_ROOT_REF = re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}/([^\s\"'`\\$;&|<>()]+)")


def check_tree(tree: PluginTree) -> list[Problem]:
    """Every problem in ``tree``, in a stable order: manifest, agents, hooks, bin. Never raises."""
    return [
        *_manifest_problems(tree),
        *_agent_problems(tree),
        *_hooks_problems(tree),
        *_bin_problems(tree),
    ]


def _manifest_problems(tree: PluginTree) -> Iterator[Problem]:
    if MANIFEST not in tree.files:
        yield Problem(K.MANIFEST_MISSING, MANIFEST, "no plugin manifest")
        return
    manifest = _json_object(tree.texts.get(MANIFEST))
    if manifest is None:
        yield Problem(K.MANIFEST_INVALID_JSON, MANIFEST, "not a UTF-8 JSON object")
        return
    name = manifest.get("name")
    if not isinstance(name, str) or not name:
        yield Problem(K.MANIFEST_NO_NAME, MANIFEST, "'name' is missing or not a non-empty string")
    elif name.startswith(_RESERVED_PREFIXES):
        yield Problem(K.MANIFEST_RESERVED_PREFIX, MANIFEST, f"name {name!r} uses a reserved prefix")
    elif not _KEBAB.fullmatch(name):
        yield Problem(K.MANIFEST_BAD_NAME, MANIFEST, f"name {name!r} is not kebab-case")
    if _REPLACES_SCAN in manifest:
        yield Problem(
            K.MANIFEST_AGENTS_SET, MANIFEST,
            f"'{_REPLACES_SCAN}' replaces the agents/ scan; leave it unset",
        )
    for key in _PATH_KEYS:
        if key in manifest:
            yield from _path_problems(tree, key, manifest[key])


def _path_problems(tree: PluginTree, key: str, value: object) -> Iterator[Problem]:
    if isinstance(value, dict):
        return
    paths = [value] if isinstance(value, str) else value
    if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
        yield Problem(K.MANIFEST_PATH_WRONG_TYPE, MANIFEST, f"'{key}' is not a path or a list of paths")
        return
    for path in paths:
        if not path.startswith(_RELATIVE):
            yield Problem(K.MANIFEST_PATH_NOT_RELATIVE, MANIFEST, f"'{key}' path {path!r} does not start with './'")
        elif ".." in PurePosixPath(path).parts:
            yield Problem(K.MANIFEST_PATH_ESCAPES, MANIFEST, f"'{key}' path {path!r} contains '..'")
        elif not _exists(tree, path):
            yield Problem(K.MANIFEST_PATH_MISSING, MANIFEST, f"'{key}' path {path!r} does not exist")


def _agent_problems(tree: PluginTree) -> Iterator[Problem]:
    for rel in sorted(f for f in tree.files if is_agent_file(f)):
        text = tree.texts.get(rel)
        if text is None:
            yield Problem(K.AGENT_FRONTMATTER_INVALID, rel, "not UTF-8")
            continue
        try:
            fields = parse_frontmatter(text)
        except FrontmatterError as e:
            yield Problem(K.AGENT_FRONTMATTER_INVALID, rel, str(e))
            continue
        for key in _REQUIRED_AGENT_FIELDS:
            if not fields.get(key):
                yield Problem(K.AGENT_MISSING_FIELD, rel, f"'{key}' is missing or empty")
        for key in fields:
            if key in IGNORED_AGENT_FIELDS:
                yield Problem(K.AGENT_IGNORED_FIELD, rel, f"'{key}' is ignored for plugin agents")
            elif key not in SUPPORTED_AGENT_FIELDS:
                yield Problem(K.AGENT_UNKNOWN_FIELD, rel, f"'{key}' is not a plugin agent field")


def _hooks_problems(tree: PluginTree) -> Iterator[Problem]:
    if HOOKS_FILE not in tree.files:
        return
    doc = _json_object(tree.texts.get(HOOKS_FILE))
    if doc is None:
        yield Problem(K.HOOKS_INVALID_JSON, HOOKS_FILE, "not a UTF-8 JSON object")
        return
    events = doc.get("hooks")
    if not isinstance(events, dict):
        yield Problem(K.HOOKS_NO_WRAPPER, HOOKS_FILE, "no top-level 'hooks' object; the file will not load")
        return
    for event, groups in events.items():
        handlers = _handlers(groups)
        if handlers is None:
            yield Problem(K.HOOKS_MALFORMED, HOOKS_FILE, f"'{event}' is not a list of {{'hooks': [...]}} groups")
            continue
        for handler in handlers:
            for script in _plugin_root_refs(handler):
                if ".." in PurePosixPath(script).parts or script not in tree.files:
                    yield Problem(
                        K.HOOK_SCRIPT_MISSING, HOOKS_FILE,
                        f"'{event}' runs ${{CLAUDE_PLUGIN_ROOT}}/{script}, which is not a file in the plugin",
                    )


def _handlers(groups: object) -> list[dict[str, object]] | None:
    """The handler objects of one event's groups, or None if the shape is wrong."""
    if not isinstance(groups, list):
        return None
    found: list[dict[str, object]] = []
    for group in groups:
        inner = group.get("hooks") if isinstance(group, dict) else None
        if not isinstance(inner, list) or not all(isinstance(h, dict) for h in inner):
            return None
        found.extend(inner)
    return found


def _plugin_root_refs(handler: dict[str, object]) -> list[str]:
    """Paths after ``${CLAUDE_PLUGIN_ROOT}/`` in a handler's ``command`` and string ``args``."""
    words: list[str] = []
    command = handler.get("command")
    if isinstance(command, str):
        words.append(command)
    args = handler.get("args")
    if isinstance(args, list):
        words.extend(a for a in args if isinstance(a, str))
    return [m.group(1).rstrip("/") for w in words for m in _PLUGIN_ROOT_REF.finditer(w)]


def _bin_problems(tree: PluginTree) -> Iterator[Problem]:
    for rel in sorted(f for f in tree.files if is_bin_entry(f)):
        if rel not in tree.executable:
            yield Problem(K.BIN_NOT_EXECUTABLE, rel, "not executable; chmod +x it")


def _json_object(text: str | None) -> dict[str, object] | None:
    if text is None:
        return None
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _exists(tree: PluginTree, path: str) -> bool:
    rel = PurePosixPath(path).as_posix()
    return rel == "." or rel in tree.files or rel in tree.dirs
