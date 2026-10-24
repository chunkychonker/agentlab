"""Pure rules that judge a Claude Code plugin's manifest, MCP config, and copies.

No I/O. Every function takes already-parsed JSON values (or bytes) plus a
`root_listing`: the set of relative POSIX paths (files *and* directories, no
`./` prefix, no trailing `/`) that exist under the plugin root. The caller
(`check_plugin.py`) builds that listing from the filesystem; this module never
touches it.

The rules are a deliberately small subset of `claude plugin validate`, taken
from the plugin manifest reference (code.claude.com/docs/en/plugins-reference,
fetched 2026-10-24). Anything not listed in the README's "What it checks"
table is not checked here; the real validator stays the authority.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from enum import Enum


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Finding:
    severity: Severity
    message: str


# --- constants (named once; the docs are the source for each) ---------------

# Component keys whose values are (or may contain) paths relative to the root.
_PATH_ONLY_KEYS = ("skills", "agents", "outputStyles", "workflows")
_PATH_OR_INLINE_KEYS = ("hooks", "mcpServers", "lspServers")  # dict = inline config
_COMMANDS_KEY = "commands"  # path, list of paths, or {name: {source|content}}

_RECOMMENDED_KEYS = ("version", "description", "author")

# "no spaces, `@`, `:`, path separators, control characters, or
# bidirectional-formatting characters"
_NAME_FORBIDDEN = re.compile(r"[\s@:/\\\x00-\x1f\x7f‎‏‪-‮⁦-⁩]")
_KEBAB = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SEPARATOR_RUN = re.compile(r"[-_.\s]+")

_RESERVED_PREFIXES = ("claude-", "anthropic-", "anthropics-", "cc-plugin-")
_RESERVED_EXACT = frozenset({"claude", "anthropic", "anthropics", "claude-code", "claude-mods"})
_BRAND_WORDS = frozenset({"claude", "anthropic", "anthropics"})

_MCP_STDIO = "stdio"
_MCP_REMOTE_TYPES = frozenset({"http", "sse", "ws"})
_MCP_BUNDLE_SUFFIXES = (".mcpb", ".dxt")
_ABSOLUTE_URL = re.compile(r"^(?:https?|wss?)://[^\s/]+")

_PLUGIN_ROOT_REF = re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}/([^\s\"'$]*)")
_USER_CONFIG_REF = re.compile(r"\$\{user_config\.([^}]*)\}")


def _err(message: str) -> Finding:
    return Finding(Severity.ERROR, message)


def _warn(message: str) -> Finding:
    return Finding(Severity.WARNING, message)


# --- name -------------------------------------------------------------------


def _check_name(name: object) -> list[Finding]:
    if not isinstance(name, str) or name == "":
        return [_err('"name" is required and must be a non-empty string')]
    if _NAME_FORBIDDEN.search(name):
        return [
            _err(
                f'"name" {name!r} contains a space, "@", ":", a path separator, '
                "or a control/bidi character"
            )
        ]
    canon = _SEPARATOR_RUN.sub("-", name.lower())
    words = canon.split("-")
    if (
        canon in _RESERVED_EXACT
        or canon.startswith(_RESERVED_PREFIXES)
        or ("official" in words and bool(_BRAND_WORDS & set(words)))
    ):
        return [_err(f'Plugin name "{name}" is reserved: it passes as one of Anthropic\'s own')]
    findings: list[Finding] = []
    if _BRAND_WORDS & set(words):
        findings.append(_warn(f'Plugin name "{name}" reads as one of Anthropic\'s own'))
    if not _KEBAB.match(name):
        findings.append(_warn(f'"name" {name!r} is not kebab-case'))
    return findings


# --- paths ------------------------------------------------------------------


def _check_path(key: str, path: str, root_listing: frozenset[str]) -> list[Finding]:
    """One component path: `./` prefix, no `..`, exists in the listing."""
    if key == "skills" and path == ".":
        return []
    if key == "mcpServers" and path.startswith("https://"):
        if not path.endswith(_MCP_BUNDLE_SUFFIXES):
            return [_err(f'{key}: bundle URL {path!r} must end in .mcpb or .dxt')]
        return []
    # Prefix and traversal are reported independently, as the real validator does.
    findings: list[Finding] = []
    if not path.startswith("./"):
        findings.append(_err(f'{key}: path {path!r} must start with "./"'))
    if ".." in path.split("/"):
        findings.append(_err(f'{key}: {path!r}: Path contains ".." which could be a path traversal attempt'))
    if findings:
        return findings
    rel = posixpath.normpath(path)
    if rel != "." and rel not in root_listing:
        return [_err(f"{key}: {path!r}: Path not found")]
    return []


def _check_component(
    key: str, value: object, root_listing: frozenset[str], user_config_keys: frozenset[str]
) -> list[Finding]:
    if key == _COMMANDS_KEY and isinstance(value, dict):
        return _check_command_map(value, root_listing)
    items = value if isinstance(value, list) else [value]
    findings: list[Finding] = []
    for item in items:
        if isinstance(item, str):
            findings += _check_path(key, item, root_listing)
        elif isinstance(item, dict) and key in _PATH_OR_INLINE_KEYS:
            if key == "mcpServers":
                findings += _check_server_map(item, root_listing, user_config_keys, "plugin.json mcpServers")
            # inline hooks / lspServers bodies are not checked (out of scope)
        else:
            findings.append(_err(f"{key}: unsupported value {item!r}"))
    return findings


def _check_command_map(commands: dict, root_listing: frozenset[str]) -> list[Finding]:
    findings: list[Finding] = []
    for name, entry in commands.items():
        if not isinstance(entry, dict) or (("source" in entry) == ("content" in entry)):
            findings.append(_err(f"commands.{name}: must set exactly one of source or content"))
        elif "source" in entry:
            src = entry["source"]
            findings += (
                _check_path(f"commands.{name}.source", src, root_listing)
                if isinstance(src, str)
                else [_err(f"commands.{name}.source must be a string")]
            )
    return findings


# --- MCP --------------------------------------------------------------------


def _strings_in(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [s for v in value for s in _strings_in(v)]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings_in(v)]
    return []


def _check_server(
    name: str, server: object, root_listing: frozenset[str], user_config_keys: frozenset[str], where: str
) -> list[Finding]:
    label = f"{where}: server {name!r}"
    if not isinstance(server, dict):
        return [_err(f"{label} must be an object")]
    findings: list[Finding] = []
    kind = server.get("type", _MCP_STDIO if "command" in server else None)
    if kind == _MCP_STDIO:
        if not isinstance(server.get("command"), str) or server["command"] == "":
            findings.append(_err(f'{label}: stdio server needs a non-empty string "command"'))
        if "args" in server and not (
            isinstance(server["args"], list) and all(isinstance(a, str) for a in server["args"])
        ):
            findings.append(_err(f'{label}: "args" must be a list of strings'))
    elif kind in _MCP_REMOTE_TYPES or (kind is None and "url" in server):
        url = server.get("url")
        if not isinstance(url, str) or not _ABSOLUTE_URL.match(url):
            findings.append(_err(f"{label}: remote url {url!r} is not a valid absolute URL"))
    else:
        findings.append(_err(f"{label}: unknown or missing type {kind!r} (no command or url)"))

    for text in _strings_in(server):
        for rel in _PLUGIN_ROOT_REF.findall(text):
            norm = posixpath.normpath(rel) if rel else "."
            if ".." in rel.split("/"):
                findings.append(_err(f"{label}: ${{CLAUDE_PLUGIN_ROOT}}/{rel} escapes the plugin root"))
            elif norm != "." and norm not in root_listing:
                findings.append(_err(f"{label}: ${{CLAUDE_PLUGIN_ROOT}}/{rel}: Path not found"))
        for key in _USER_CONFIG_REF.findall(text):
            if key not in user_config_keys:
                findings.append(
                    _err(f"{label}: ${{user_config.{key}}} references an option userConfig doesn't declare")
                )
    return findings


def _check_server_map(
    servers: dict, root_listing: frozenset[str], user_config_keys: frozenset[str], where: str
) -> list[Finding]:
    return [
        f
        for name, server in servers.items()
        for f in _check_server(name, server, root_listing, user_config_keys, where)
    ]


# --- public API -------------------------------------------------------------


def user_config_keys(manifest: object) -> frozenset[str]:
    """Option names declared in the manifest's `userConfig` (empty if absent or malformed;
    `check_manifest` reports the malformed case)."""
    if isinstance(manifest, dict) and isinstance(manifest.get("userConfig"), dict):
        return frozenset(manifest["userConfig"])
    return frozenset()


def check_manifest(manifest: object, root_listing: frozenset[str]) -> list[Finding]:
    """Judge a parsed `.claude-plugin/plugin.json`.

    Errors: not an object; bad/missing/reserved `name`; `author` not an object
    with a string `name`; `userConfig` not an object; any component path that
    lacks `./`, contains `..`, or is absent from `root_listing`; an inline
    `mcpServers` entry that fails the MCP rules.
    Warnings: missing `version`/`description`/`author`; non-kebab-case name;
    a name that contains a brand word. Never raises on any JSON value.
    """
    if not isinstance(manifest, dict):
        return [_err("plugin.json must be a JSON object")]
    findings = _check_name(manifest.get("name"))
    for key in _RECOMMENDED_KEYS:
        if key not in manifest:
            findings.append(_warn(f'missing recommended field "{key}"'))
    author = manifest.get("author")
    if author is not None and not (isinstance(author, dict) and isinstance(author.get("name"), str)):
        findings.append(_err('"author" must be an object with a string "name"'))
    if "userConfig" in manifest and not isinstance(manifest["userConfig"], dict):
        findings.append(_err('"userConfig" must be an object'))
    declared = user_config_keys(manifest)
    for key in (*_PATH_ONLY_KEYS, *_PATH_OR_INLINE_KEYS, _COMMANDS_KEY):
        if key in manifest:
            findings += _check_component(key, manifest[key], root_listing, declared)
    return findings


def check_mcp(mcp: object, root_listing: frozenset[str], declared_user_config: frozenset[str]) -> list[Finding]:
    """Judge a parsed root `.mcp.json` (shape `{"mcpServers": {name: server}}`).

    Errors: wrong top-level shape; a server that is not an object; a stdio
    server without a string `command` or with non-string `args`; a remote
    server without an absolute URL; an unknown `type`; a
    `${CLAUDE_PLUGIN_ROOT}/x` reference whose `x` escapes the root or is not in
    `root_listing`; a `${user_config.KEY}` whose KEY is not in
    `declared_user_config`. Never raises on any JSON value.
    """
    if not isinstance(mcp, dict) or not isinstance(mcp.get("mcpServers"), dict):
        return [_err('.mcp.json must be an object with an "mcpServers" object')]
    return _check_server_map(mcp["mcpServers"], root_listing, declared_user_config, ".mcp.json")


def check_drift(copy: bytes, original: bytes, label: str) -> list[Finding]:
    """Error if a bundled copy is not byte-identical to its original. `label`
    names both paths so the message says which pair drifted."""
    if copy == original:
        return []
    return [_err(f"drift: {label} differ; re-copy the original into the plugin")]


def has_failures(findings: list[Finding], strict: bool) -> bool:
    """True if any error, or (under `strict`) any warning, is present."""
    return any(f.severity is Severity.ERROR or strict for f in findings)
