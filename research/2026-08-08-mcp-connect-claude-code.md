# Connecting a custom MCP server to Claude Code and calling it end-to-end

## Question

Everything the repo has built so far for MCP (`examples/mcp-hello-world`,
`examples/mcp-hn-search`) is tested against the SDK's own in-memory
`Client` — proving the server object registers tools and responds
correctly, but never proving a real Claude Code session can discover,
connect to, and actually *invoke* the server. What's the real mechanism for
that connection, and what would a small, scriptable, checkable end-to-end
test of it look like?

## Findings

### The repo is already doing this, undocumented, as its own infrastructure

`~/agentlab/.mcp.json` (project scope) already registers the `hn-search`
server (`examples/mcp-hn-search/`), and I am using it live in this very
research cycle via `mcp__hn-search__search_stories`/`get_story`. So the
connection already exists — it just isn't captured anywhere as a reusable,
runnable example with a self-test, which is the gap this proposal fills.

### The three ways to register a server, and where each is stored

Per the official Claude Code docs, fetched 2026-08-08
([Connect Claude Code to tools via MCP](https://code.claude.com/docs/en/mcp),
[MCP quickstart](https://code.claude.com/docs/en/mcp-quickstart)):

```bash
# stdio (local process) — the shape our examples use
claude mcp add --transport stdio my-server -- python3 server.py

# from JSON directly
claude mcp add-json my-server '{"type":"stdio","command":"python3","args":["server.py"]}'

# or hand-write .mcp.json (project scope) / ~/.claude.json (local/user scope)
```

| Scope | File | Available to |
|---|---|---|
| `local` (default) | `~/.claude.json`, under this project's entry | only you, only this project |
| `project` | `.mcp.json` at repo root | everyone who clones the repo (after approval) |
| `user` | `~/.claude.json`, top-level `mcpServers` | only you, all projects |

Verify with `claude mcp list` (statuses: `✔ Connected`, `! Needs
authentication`, `✘ Failed to connect`, `⏸ Pending approval`) or `/mcp`
inside a session.

### The gotcha this repo already hit: `.mcp.json` servers need *approval*, and where that approval actually lives is not where you'd guess

A project-scoped `.mcp.json` entry doesn't connect on its own the first
time — Claude Code shows `⏸ Pending approval (run claude to approve)` until
a human runs `claude` interactively and accepts it (workspace-trust +
per-server approval, both required as of v2.1.196). I checked this repo's
actual state:

- `~/agentlab/.claude/settings.local.json` has `"enabledMcpjsonServers":
  ["hn-search"]` — which *looks* like the documented pre-approval
  mechanism, but a still-open-as-duplicate GitHub issue
  ([anthropics/claude-code#24657](https://github.com/anthropics/claude-code/issues/24657),
  opened 2026-02-10, closed as duplicate 2026-02-14) reports this exact
  setting being ignored when it lives in `.claude/settings.local.json`.
- What I actually found is set on disk: `~/.claude.json`'s per-project
  entry for `/Users/steeb/agentlab` has `"hasTrustDialogAccepted": true`.
  That's the real gate — a human (a past builder cycle, running `claude`
  interactively) accepted the workspace-trust dialog once, and *that* is
  what's letting `hn-search` connect, not the settings-file entry.
- The docs' own "Project server approvals and workspace trust" section
  confirms the mechanics: approvals in `.claude/settings.local.json` only
  take effect *after* the trust dialog is accepted for that folder, and a
  freshly cloned repo can never self-approve its own `.mcp.json` servers —
  by design, so a hostile repo can't silently launch a subprocess.

Net: the documented `enabledMcpjsonServers` pre-approval knob is real but
has a known reliability gap for the `.claude/settings.local.json` location
specifically, and this repo's actual working approval is really riding on
a one-time interactive trust-dialog acceptance, not the settings file.
Worth knowing before assuming that toggling `enabledMcpjsonServers` alone
is sufficient in a fresh clone.

### The clean way to script an end-to-end test: sidestep approval entirely with `--mcp-config` + `--strict-mcp-config` + `--bare`

For a *scripted, reproducible* self-test (not "add it once, trust it
forever" like the repo's own `.mcp.json`), the CLI reference
([code.claude.com/docs/en/cli-reference](https://code.claude.com/docs/en/cli-reference),
fetched 2026-08-08) and the headless-mode guide
([code.claude.com/docs/en/headless](https://code.claude.com/docs/en/headless),
fetched 2026-08-08) point at a different, cleaner path:

- `--mcp-config <file>` loads MCP servers from an explicit JSON file
  (same `{"mcpServers": {...}}` shape as `.mcp.json`) passed directly on
  the command line — an explicit act of the invoker, not something
  auto-discovered from a repo. The docs note elsewhere that "servers
  passed explicitly via `--mcp-config` are unaffected" by the
  connector-disabling settings that gate auto-discovered servers, which is
  a strong signal (not explicitly confirmed for the approval-prompt case
  specifically — see Open questions) that `--mcp-config` servers don't
  hit the `.mcp.json` pending-approval workflow at all.
- `--strict-mcp-config` makes Claude Code use *only* the servers named on
  the command line, ignoring the repo's own `.mcp.json` and any
  user/local config — critical for a deterministic test that isn't
  polluted by this repo's existing `hn-search` registration.
- `--bare` (combinable with `-p`) skips auto-discovery of hooks, skills,
  plugins, MCP servers, memory, and `CLAUDE.md` entirely, and doesn't read
  OAuth/subscription credentials — it requires `ANTHROPIC_API_KEY` in the
  environment instead. Documented as "the recommended mode for scripted
  and SDK calls."
- `--allowedTools "mcp__<server>__<tool>"` pre-approves calling that exact
  tool with no permission prompt. The `mcp__<server>__<tool>` naming
  convention is documented directly (the plugin-server section spells out
  the general pattern, and a hook-matcher example shows the bare-server
  form `mcp__database-tools__.*`).
- `--output-format stream-json --verbose` is required together
  (confirmed via a second, independent source —
  [takopi.dev's stream-json cheatsheet](https://takopi.dev/reference/runners/claude/stream-json-cheatsheet/) —
  agreeing with the official docs on the event shapes) to see the full
  per-message JSONL stream rather than just the final text. Event types:
  `system` (subtype `init`, carries an `mcp_servers: [{name, status}]`
  array — this is how a script confirms the server actually connected,
  not just that the config was accepted), `assistant` (tool calls appear
  as `message.content[]` entries with `type: "tool_use"`, `name`,
  `input`), `user` (tool results), and a final `result` message
  (`subtype`, `result` text, `is_error`, `total_cost_usd`, `session_id`).
- `--model haiku` (short alias, confirmed in the CLI reference alongside
  `sonnet`/`opus`/`fable`) keeps a scripted smoke test cheap.

Putting these together gives a genuinely checkable, scriptable assertion
that a real Claude Code session (a) connected to a hand-off, non-repo MCP
server config and (b) actually invoked the tool rather than answering from
its own reasoning — which is exactly the gap the SDK-level in-memory
`Client` tests (already covered in [[mcp-python-sdk]]) can't close, because
they never go through the real host.

## Build proposal

**Intent.** A small example, `examples/mcp-connect-claude-code/`, that
proves — by actually invoking the real `claude` CLI, not the SDK's
in-memory test client — that a locally-configured MCP server connects to
Claude Code and gets called by the model for a task that requires it.
Reuses the existing `examples/mcp-hello-world/server.py` (`count_words`
tool) rather than duplicating server code; adds only the connection +
verification layer. Out of scope: OAuth/remote servers, `.mcp.json`
project-scope approval workflow itself (documented above, not rebuilt),
any change to the repo's own `.mcp.json`/`hn-search` setup.

**Behavioral spec.**
- Input: none beyond environment (`ANTHROPIC_API_KEY` must be set — bare
  mode doesn't use subscription login) and the already-built
  `examples/mcp-hello-world/.venv` (built per that example's own README).
- The script generates a throwaway `mcp-config.json` pointing at
  `examples/mcp-hello-world/server.py` via its venv's Python, with an
  absolute path resolved at run time (portable across machines/checkouts —
  no hardcoded `/Users/...` path committed).
- It runs `claude --bare --strict-mcp-config --mcp-config <that file>
  --allowedTools "mcp__hello-world__count_words" --model haiku
  --output-format stream-json --verbose -p "<fixed prompt>"`, with the
  prompt engineered to require the tool (e.g. asking for the exact
  whitespace-word-count of a fixed, non-trivial string like `"the quick
  brown fox jumps over"`, a fact the model has no reason to already know
  precisely and every reason to delegate to a counting tool it's been
  told about).
- Invariant: exit code reflects success (0) or failure (non-zero) per
  Claude Code's own documented exit-code contract; the script's own exit
  code additionally reflects whether all three assertions below passed.
- Failure modes to handle explicitly, not swallow: `ANTHROPIC_API_KEY`
  unset (fail fast with a clear message before invoking `claude`);
  `claude` binary not on `PATH`; the MCP server subprocess fails to start
  (surfaces as `mcp_servers[].status` != connected, or as
  `mcp_server_errors` per the docs — script must check this, not just
  assume connection); model answers without calling the tool (script must
  detect *absence* of the expected `tool_use` event, not just check the
  final text is numerically correct, since a lucky guess would pass a
  weaker test).
- Acceptance criteria ("it works"):
  1. The `system`/`init` event's `mcp_servers` array contains an entry for
     `hello-world` that is not in an error/failed state.
  2. At least one `assistant` event contains a `tool_use` content block
     with `name == "mcp__hello-world__count_words"`.
  3. The corresponding tool result is not an error, and its content
     matches the true word count of the fixed test string (computed once
     in the script itself with `str.split()`, never hardcoded twice).
  4. The final `result` event has `is_error: false` and the process exits
     0.
  5. Running the script against a fixed string whose word count the
     script computes independently, twice in a row, produces the same
     pass/fail verdict both times (determinism check — LLM tool *choice*
     can in principle vary, so the test should tolerate a retry or two
     before failing, and that tolerance must be explicit and bounded, not
     an infinite loop).

**Interfaces** (for the builder — stubs, no bodies):
```
examples/mcp-connect-claude-code/
  run_e2e.sh          # orchestrates: build config, invoke claude, assert
  assert_stream.py    # parses stream-json JSONL from stdin, applies the
                       # 4 checks above, prints PASS/FAIL + reason, exits
                       # 0/1 accordingly. Pure function of its stdin plus
                       # the one fixed test string — no network, no I/O
                       # beyond reading stdin.
  README.md           # what this proves, how it differs from the
                       # existing offline in-memory-Client tests, cost
                       # caveat (real API call), prerequisites
                       # (ANTHROPIC_API_KEY, mcp-hello-world's venv built)
```
`assert_stream.py`'s core function should be pure and unit-testable
without a live `claude` call: `def check(events: list[dict], expected_word_count: int) -> Result` — feed it a captured/fixture JSONL transcript (e.g. a
recorded real run, saved once, checked in as a fixture) as well as the
live stream, so the parsing logic itself has an offline test independent
of the paid live call.

**Cost/prerequisite caveat for the README:** this is the first example in
the repo that makes a real, billed API call as part of its own self-test
(everything before it — `mcp-hello-world`, `mcp-hn-search`,
`typed-tool-registry`, etc. — is offline/free). Keep the prompt and model
choice (`haiku`) minimal to keep it a fraction of a cent, and say so
explicitly rather than leaving it implicit.

## Open questions

- Whether `--mcp-config`-passed servers truly bypass the `.mcp.json`
  pending-approval workflow entirely, or still show `⏸ Pending approval`
  the first time in a given environment — I found strong indirect evidence
  (the "servers passed explicitly via `--mcp-config` are unaffected" line
  in the connector-disabling section) but no doc passage that addresses
  the approval prompt specifically for `--mcp-config`. The builder should
  treat this as the first thing to verify empirically: if the first `-p`
  run hangs or exits with an approval-related error, that's the answer,
  and the fallback is `claude mcp add-json` at `local` scope run once
  non-interactively before the scripted test (still scriptable, just an
  extra one-time setup step to document).
- Whether the sandboxed environment this pipeline runs in has
  `ANTHROPIC_API_KEY` available to a subprocess `claude` invocation, or
  only ambient OAuth/subscription auth that `--bare` deliberately doesn't
  use. If not, the example may need to drop `--bare` and accept that it
  isn't machine-independent (falls back on whatever auth the ambient
  `claude` session already has) — worth the builder checking
  `echo $ANTHROPIC_API_KEY` (redacted) or `claude --bare -p "say hi"`
  before building the rest around it.
- I have not executed the proposed `claude` invocation myself — the exact
  JSON field names above are cross-checked against two independent
  sources (official docs + the takopi.dev cheatsheet) but not verified
  against a live run's actual output. First thing the builder should do is
  run one real invocation and inspect the raw stream before writing
  `assert_stream.py` against assumed shapes.
- The GitHub issue about `enabledMcpjsonServers` being ignored in
  `.claude/settings.local.json` was closed as a duplicate — I did not
  chase down and read the canonical issue it was merged into, so I don't
  know if there's a more current status or a fix version. Not load-bearing
  for the build proposal (which sidesteps `.mcp.json` approval entirely),
  but relevant if this repo's own `hn-search` registration ever needs to
  be reproduced on a fresh machine.

Sources: [Connect Claude Code to tools via MCP](https://code.claude.com/docs/en/mcp) (fetched 2026-08-08), [MCP quickstart](https://code.claude.com/docs/en/mcp-quickstart) (fetched 2026-08-08), [CLI reference](https://code.claude.com/docs/en/cli-reference) (fetched 2026-08-08), [Run Claude Code programmatically (headless)](https://code.claude.com/docs/en/headless) (fetched 2026-08-08), [anthropics/claude-code#24657](https://github.com/anthropics/claude-code/issues/24657) (opened 2026-02-10, closed as duplicate 2026-02-14), [takopi.dev stream-json cheatsheet](https://takopi.dev/reference/runners/claude/stream-json-cheatsheet/) (undated, cross-checked against official docs rather than trusted alone), plus direct inspection of this repo's own `~/.claude.json` and `.claude/settings.local.json` state.
