# One Claude Code plugin: a skill plus an MCP server

`agentlab-bundle` is a Claude Code plugin that ships two existing lab pieces
behind one manifest, the `commit-message` skill from
[`skill-anatomy`](../skill-anatomy/) and the `count_words` server from
[`mcp-hello-world`](../mcp-hello-world/). It also includes an offline checker
that parses the manifest and `.mcp.json`, confirms every path they name exists
inside the plugin, and confirms the bundled copies still match their originals.
The checker uses only the Python 3 standard library.

From the research note:
[`research/2026-10-24-claude-code-plugin-bundle.md`](../../research/2026-10-24-claude-code-plugin-bundle.md).

## Design (layers 1-3)

- **Intent:** install the skill and the server with one plugin instead of two
  manual setups, and catch broken manifests and stale copies without Claude
  Code. Out of scope: marketplace publishing, hooks, `userConfig`, installing
  Python dependencies for the server, and symlinks.
- **Spec:** see the acceptance criteria in the research note (numbered 1-8);
  `test_check_plugin.py` has one test class per criterion group. The checker
  exits 0 when there are no errors (and, with `--strict`, no warnings). It exits
  1 on any finding or on unreadable input (missing manifest, bad JSON) and
  prints a one-line message, not a traceback.
- **Interfaces:**
  - `plugin_rules.check_manifest(manifest: object, root_listing: frozenset[str]) -> list[Finding]`
  - `plugin_rules.check_mcp(mcp: object, root_listing: frozenset[str], declared_user_config: frozenset[str]) -> list[Finding]`
  - `plugin_rules.check_drift(copy: bytes, original: bytes, label: str) -> list[Finding]`
  - `plugin_rules.has_failures(findings, strict: bool) -> bool`
  - `check_plugin.main(argv: list[str]) -> int`, the only function that does I/O.

  `Finding(severity: Severity, message: str)`, where `Severity` is an enum
  (`ERROR`/`WARNING`). `root_listing` holds every file and directory under the
  plugin root as relative POSIX paths, and the shell builds it, so the rules
  never touch the filesystem. One change from the proposal: `check_mcp` takes the
  declared `userConfig` keys as a parameter. Criterion 6 needs them, and passing
  them in keeps the function pure.

| File | What it is |
|------|-----------|
| `.claude-plugin/plugin.json` | The manifest: `name`, `version`, `description`, `author`. It names no component paths because the defaults cover both components. |
| `skills/commit-message/SKILL.md` | Byte-for-byte copy of `../skill-anatomy/skills/commit-message/SKILL.md`. |
| `.mcp.json` | Runs `python3 ${CLAUDE_PLUGIN_ROOT}/server/server.py` over stdio. |
| `server/server.py` | Byte-for-byte copy of `../mcp-hello-world/server.py`. |
| `plugin_rules.py` | Pure rules for the manifest, the MCP config, and drift. No I/O. |
| `check_plugin.py` | CLI shell: lists the directory, reads the JSON, prints findings, sets the exit code. |
| `test_check_plugin.py` | Offline self-test of criteria 1-8. |

The skill and server are real copies, not symlinks. Claude Code copies a
plugin into a cache when it installs it, so anything outside the plugin
directory can't be reached afterward. The research note cites community reports
that symlinks aren't reliably dereferenced during that copy. The drift check
catches a copy that falls out of sync with its original.

## Run the self-test (no API key, no Claude Code, no `mcp` package)

```bash
cd examples/plugin-agentlab-bundle
python3 -m unittest -v test_check_plugin
```

Expected: `Ran 22 tests ... OK`. Requires Python 3.10+.

To check the real plugin by hand, including drift against the originals:

```bash
cd examples/plugin-agentlab-bundle
python3 check_plugin.py . --strict \
  --original skills/commit-message/SKILL.md=../skill-anatomy/skills/commit-message/SKILL.md \
  --original server/server.py=../mcp-hello-world/server.py
# OK: 0 error(s), 0 warning(s)
```

After you edit either original, re-copy it:
`cp ../skill-anatomy/skills/commit-message/SKILL.md skills/commit-message/` and
`cp ../mcp-hello-world/server.py server/`.

## What it checks

This is a subset of `claude plugin validate`, which remains the authoritative
check (see below for where the two agree and differ).

| Rule | Severity |
|------|----------|
| `plugin.json` missing, not valid JSON, or not an object | error |
| `name` missing or empty, or contains a space, `@`, `:`, `/`, `\`, or a control/bidi character | error |
| `name` is reserved: starts with `claude-`/`anthropic-`/`anthropics-`/`cc-plugin-`; is `claude`/`anthropic`/`anthropics`/`claude-code`/`claude-mods`; or puts `official` next to a brand word. Case-insensitive, separator runs collapsed | error |
| `name` not kebab-case, or contains `claude`/`anthropic` as a word elsewhere | warning |
| `version`, `description`, or `author` missing | warning |
| `author` present but not `{ "name": string }`; `userConfig` not an object | error |
| A path under `skills`, `agents`, `commands`, `hooks`, `mcpServers`, `lspServers`, `outputStyles`, or `workflows` that doesn't start with `./`, contains `..`, or doesn't exist. `skills` may be `"."`. `mcpServers` may be an `https://` URL ending in `.mcpb` or `.dxt` | error |
| A `commands` object-map entry that doesn't set exactly one of `source`/`content` | error |
| `.mcp.json` not shaped `{ "mcpServers": { ... } }`; a stdio server without a string `command` or with non-string `args`; a remote server without an absolute URL; an unknown `type` | error |
| A `${CLAUDE_PLUGIN_ROOT}/x` reference in an MCP server (`.mcp.json` or inline) where `x` escapes the root or doesn't exist | error |
| A `${user_config.KEY}` reference in an MCP server where `KEY` isn't declared in `userConfig` | error |
| A bundled copy that differs from its `--original` | error |

Not checked: inline `hooks`/`lspServers` bodies, `userConfig`/`channels`/
`monitors` option schemas, unknown top-level keys, `.mcp.json` files referenced
*by path* from `mcpServers` (their existence is checked but their contents are
not), `homepage` URL parsing, and whether each `skills/<name>/` holds a
`SKILL.md`.

## Install it in Claude Code

The `mcp` package is a prerequisite. A plugin can't install Python
dependencies, and the server runs with whichever `python3` is first on `PATH`:

```bash
pip install "mcp>=2.0.0,<3"   # same pin as ../mcp-hello-world/requirements.txt
claude --plugin-dir examples/plugin-agentlab-bundle   # this session only
```

The skill loads as `agentlab-bundle:commit-message`, and the server shows up
as `plugin:agentlab-bundle:hello-world`.

## Live check (run 2026-10-24, Claude Code 2.1.303)

These are not part of the offline test. They were run once during the build.

**The real validator accepts the plugin:**

```text
$ claude plugin validate examples/plugin-agentlab-bundle --strict
Validating plugin manifest: .../examples/plugin-agentlab-bundle/.claude-plugin/plugin.json

✔ Validation passed
```

**Claude Code loads both components** (`--plugin-dir` works on a directory in
the middle of a repo with no marketplace):

```text
$ claude --plugin-dir examples/plugin-agentlab-bundle plugin details agentlab-bundle
agentlab-bundle 0.1.0
  Source: agentlab-bundle@inline
Component inventory
  Skills (1)  commit-message
  MCP servers (1)  hello-world  (tool schemas resolved at runtime; not counted)
Projected token cost
  Always-on:   ~81 tok   added to every session
  commit-message        ~80 (always-on)   ~340 (on-invoke)
```

**What happens when `mcp` is missing.** With the system `python3`, which has no
`mcp` package installed, the plugin still loads, but the server fails to start:

```text
$ claude --plugin-dir examples/plugin-agentlab-bundle mcp list
plugin:agentlab-bundle:hello-world: python3 .../server/server.py - ✘ Failed to connect — CONNECTION_CLOSED: Connection closed
```

With a venv that has `mcp` 2.3.0 first on `PATH`:

```text
$ PATH=/tmp/mcpvenv/bin:$PATH claude --plugin-dir examples/plugin-agentlab-bundle mcp list
plugin:agentlab-bundle:hello-world: python3 .../server/server.py - ✔ Connected
```

The failure message doesn't mention the missing import. If you see
`CONNECTION_CLOSED`, run `python3 -c "import mcp"` first.

**Cross-check against `claude plugin validate`.** Each broken variant was run
through both tools:

| Variant | `check_plugin.py` | `claude plugin validate` |
|---------|-------------------|--------------------------|
| `"skills": "../x"` | 2 errors (`./` prefix, `..`) | 2 errors (same two) |
| `"skills": "./nope"` | error, Path not found | error, Path not found |
| `"skills": "skills"` | error, `./` prefix | error, `./` prefix |
| `"name": "claude-lab"` | error, reserved | error, reserved |
| `"name": "my bundle"` | error | error, cannot contain spaces |
| `"name": "Agentlab_Bundle"`, no `version` | 2 warnings | 2 warnings |
| `"name": "mcp-for-claude"` | warning | warning |
| undeclared `${user_config.token}` in `.mcp.json` env | error | error |
| truncated `plugin.json` | error, not valid JSON | error, invalid JSON syntax |
| `"skills": "."` | passes | passes |
| `${CLAUDE_PLUGIN_ROOT}/server/missing.py` in `.mcp.json` args | **error** | **passes** |
| same, in inline `mcpServers` | **error** | **passes** |
| no `plugin.json` at all | **error** | **passes** (validates components only) |

The last three rows are deliberate differences. The real validator doesn't
check that the file a `${CLAUDE_PLUGIN_ROOT}` path points at actually exists.
That mistake surfaces only at runtime, as the same `Failed to connect` you get
when `mcp` is missing. This checker reports it before you install. Claude Code
treats the manifest as optional, but this checker requires it, because checking
the manifest is the checker's job.

**Not verified live:**
- Invoking the skill in a model session (`/agentlab-bundle:commit-message`) or
  calling `count_words` through Claude. `plugin details` lists the skill as
  `commit-message`. The `agentlab-bundle:` prefix comes from the docs and wasn't
  observed in a session.
- `claude plugin install` from a marketplace, and so whether symlinks survive
  the cache copy. The plugin uses real copies, so it doesn't depend on that.
- Whether the validator warns about the skill's `!` shell injection. It didn't
  warn on this plugin, so it doesn't appear to.
