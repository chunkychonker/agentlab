# Package lab work as a Claude Code plugin

Date: 2026-10-24. Backlog item: "Package lab work as a Claude Code plugin" (marked `[researching]`).
Note: hn-search MCP was unavailable this session; WebSearch/WebFetch only.

## Question

What is the current plugin manifest contract, and what is the smallest single plugin that bundles one existing skill plus the hello-world MCP server, with an offline check that the manifest parses and every named path exists?

## Findings

Primary source: [Plugin manifest reference](https://code.claude.com/docs/en/plugins-reference) (fetched 2026-10-24; local CLI is Claude Code 2.1.303).

- **Manifest is optional**; lives at `.claude-plugin/plugin.json`. Only `name` is required (kebab-case, no spaces/`@`/`:`/path separators). Everything else (`version`, `description`, `author{name}`) is a validator *warning* if missing; `--strict` turns warnings into failures. Unknown top-level keys are stripped (warning), but unknown keys inside `userConfig`/`channels`/`lspServers`/`monitors` are errors.
- **Reserved names**: `claude-*`, `anthropic-*`, `claude`, `claude-code`, `official`+`claude`... are validation errors. Do NOT name the plugin `claude-lab`; use e.g. `agentlab-bundle`.
- **Default layout**: skills in `skills/<name>/SKILL.md`, MCP servers in `.mcp.json` at plugin root, hooks in `hooks/hooks.json`. Everything except `plugin.json` goes at the plugin root, NOT inside `.claude-plugin/`. `skills` in the manifest *adds* to the default `skills/` scan; `mcpServers` *merges* with `.mcp.json`; `commands`/`agents` *replace* defaults.
- **Path rules**: manifest component paths must start with `./`, resolve inside the plugin root, and exist. `..` is a validate error ("Path contains \"..\""); missing is "Path not found". `skills` additionally accepts `"."`.
- **`${CLAUDE_PLUGIN_ROOT}`** resolves inline in MCP stdio `command`/`args`/`env`, hook commands, and skill body markdown; it changes on update, so never write state there (use `${CLAUDE_PLUGIN_DATA}`). It is NOT in the environment of Bash-tool commands Claude runs.
- **Validation**: `claude plugin validate <path> [--json] [--strict]` is "the authoritative check"; since v2.1.281 it also checks MCP entries in `.mcp.json`/inline (entries Claude Code would drop, bad `${user_config.KEY}`, bad URLs). Local CLI also has `claude plugin details <name>` (component inventory + projected token cost).
- **Plugins are copied to a cache on install**, so anything outside the plugin dir is unreachable. This matters here: our skill (`examples/skill-anatomy/skills/commit-message/`) and server (`examples/mcp-hello-world/server.py`) live *outside* any plugin dir. Symlinks are the tempting fix but community issues report they are not reliably dereferenced into the cache and regressed around v2.1.117-2.1.119 ([#57339](https://claudeissues.com/issue/57339-plugin-installer-does-not-resolve-symlinks-when-copying-to-cache-breaking-plugin), [#53948](https://claudeissues.com/issue/53948-bug-plugin-install-no-longer-copies-symlinked-skills-to-cache-regression-in-v2-1), user-filed, closure state unverified). Safe choice: real copies inside the plugin dir plus a drift test against the originals.
- Skill caveat: the existing `commit-message` skill uses `!`-prefixed shell injection (`git status --short`); fine inside a plugin, but it will be namespaced as `agentlab-bundle:commit-message`.
- The MCP server needs `mcp>=2.0.0,<3` importable by whatever `command` runs. `uv` is not installed here; `python3` is. A plugin cannot install deps for you. Simplest honest approach: `.mcp.json` runs `python3 ${CLAUDE_PLUGIN_ROOT}/server/server.py` and README states the prerequisite (`pip install "mcp>=2.0.0,<3"`); record in the live check what happens when it is missing.

## Build proposal

**Intent.** Ship one installable plugin, `examples/plugin-agentlab-bundle/`, containing the `commit-message` skill and the hello-world MCP server, plus an offline checker. Out of scope: marketplace publishing, hooks, userConfig, uv/dependency bootstrapping, symlinks.

**Layout.**
```
examples/plugin-agentlab-bundle/
  .claude-plugin/plugin.json     # name agentlab-bundle, version 0.1.0, description, author{name}
  skills/commit-message/SKILL.md # byte-identical copy of examples/skill-anatomy/skills/commit-message/
  .mcp.json                      # {"mcpServers":{"hello-world":{"type":"stdio","command":"python3","args":["${CLAUDE_PLUGIN_ROOT}/server/server.py"]}}}
  server/server.py               # byte-identical copy of examples/mcp-hello-world/server.py
  check_plugin.py                # pure core + thin CLI shell
  test_check_plugin.py
  README.md
```
Keep `plugin.json` minimal; it need not name `skills`/`mcpServers` since defaults apply, but the checker must handle them if present.

**Interfaces.**
```python
@dataclass(frozen=True) class Finding: severity: Literal["error","warning"]; message: str
def check_manifest(manifest: dict, root_listing: frozenset[str]) -> list[Finding]  # pure
def check_mcp(mcp: dict, root_listing: frozenset[str]) -> list[Finding]            # pure
def check_drift(copy: bytes, original: bytes, label: str) -> list[Finding]         # pure
def main(argv) -> int   # edge: reads files, exit 0 clean / 1 any error
```
`root_listing` is the set of relative POSIX paths under the plugin root (built at the edge), so the core does no I/O.

**Behavior / acceptance criteria (offline, `python -m unittest` or pytest, no key, no network).**
1. Valid fixture -> zero errors.
2. Missing `name` -> error; `name` with space, `@`, `:`, or reserved prefix `claude-`/`anthropic-` -> error.
3. A component path not starting with `./`, containing `..`, or absent from `root_listing` -> error (mirrors "Path not found"/"path traversal").
4. Missing `version`/`description`/`author` -> warning only (error under `--strict`).
5. `.mcp.json` entry with a `${CLAUDE_PLUGIN_ROOT}/x` arg whose `x` is not in the listing -> error.
6. `${user_config.KEY}` referenced but not declared in `userConfig` -> error.
7. Copy differs from original (drift) -> error naming both paths; checker run against the real repo passes.
8. Malformed JSON -> clean nonzero exit with message, no traceback.

**Live check (separate, record results in README, not part of the offline test).**
- `claude plugin validate examples/plugin-agentlab-bundle --strict` -> expect "Validation passed"; paste output. Cross-check that our checker and the real validator agree on one deliberately broken variant (e.g. `../x` path).
- `claude --plugin-dir examples/plugin-agentlab-bundle -p ...` or `claude plugin details agentlab-bundle`: record which components load (skill namespaced `agentlab-bundle:commit-message`, MCP tool `count_words` listed via `/mcp` or `claude mcp list`), and what happens when `mcp` is not importable. Report docs-vs-reality gaps in `knowledge/`. If the live step cannot run in the build environment, say so plainly rather than claiming it.

**Failure domain.** Self-contained directory; nothing existing is modified. Copies can drift, which criterion 7 catches.

## Open questions

- Whether symlinks into sibling `examples/` dirs now work after install (conflicting community reports; not tested).
- Whether `claude --plugin-dir` accepts a path mid-repo without a marketplace, and exact `claude plugin details` behavior for an uninstalled dir; verify live.
- Whether `claude plugin validate` warns about the `!`-injection in the skill or about `python3` not being on a plugin-safe PATH.
- `type: "stdio"` in plugin `.mcp.json`: docs examples omit `type`; confirm it is accepted.
