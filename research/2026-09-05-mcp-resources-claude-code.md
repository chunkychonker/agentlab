# MCP resources through the real Claude Code host

## Question

When a real `claude` CLI session connects to an stdio MCP server that exposes a
static resource and a resource template, does the host actually *list* and
*read* them — and by what mechanism (`@`-mention, model-callable synthetic
tools, or both) — so `knowledge/mcp-resources.md`'s "how Claude Code surfaces
resources" section can be corrected from "against current docs" to verified
live?

## Findings

Sources fetched **2026-09-05** unless noted.

The **`claude-api` skill is not installed on this machine** — `~/.claude/skills/`
holds only `graphify/`; `~/.claude/plugins/installed_plugins.json` lists only
`clangd-lsp`. This increment is about the Claude Code CLI host, not the Messages
API, so the only API-adjacent fact needed is the `--model` alias, and
`knowledge/anthropic-models.md` already records `claude-haiku-4-5` /
`haiku` as the cheap default. No model-id guessing involved.

### Installed host: `claude` 2.1.252

`which claude` → `/Users/steeb/.local/bin/claude`; `claude --version` →
`2.1.252 (Claude Code)`. This is newer than every version referenced in the
current [MCP docs](https://code.claude.com/docs/en/mcp) (which cite the v2 MCP
runtime at v2.1.232+ and `--strict-mcp-config` approval-skip behaviour at
v2.1.246). So the machine can exercise the newest resource behaviour.

`claude --help` on 2.1.252 confirms every flag the sibling example
`examples/mcp-connect-claude-code/run_e2e.sh` relies on is still present and
unchanged in spelling: `--mcp-config <configs...>`, `--strict-mcp-config`
("Only use MCP servers from --mcp-config, ignoring all other MCP
configurations"), `--bare` ("Anthropic auth is strictly `ANTHROPIC_API_KEY` or
apiKeyHelper ... OAuth and keychain are never read" — and `--mcp-config` is
listed among the things you must explicitly provide under `--bare`),
`--allowedTools`, `--output-format stream-json` (only with `--print`),
`--verbose`. There is **no** `--file`-style flag for MCP resources; `--file
<specs...>` is `file_id:relative_path` for pre-uploaded file attachments, not
MCP.

### Claude Code surfaces MCP resources two ways

Both are real and documented; the protocol-level split is in
`knowledge/mcp-resources.md` (resources are application/user-driven, tools are
model-driven), and Claude Code layers a model-driven path on top.

1. **User-driven `@`-mention.** [FastMCP's Claude Code integration
   page](https://gofastmcp.com/integrations/claude-code) (fetched 2026-09-05):
   *"If your server provides resources, you can reference them with `@`
   mentions"*, format `@server:protocol://resource/path`. For a server named
   `notes` exposing `notes://index` that is `@notes:notes://index`. The current
   [`code.claude.com/docs/en/mcp`](https://code.claude.com/docs/en/mcp) page no
   longer spells the `@`-mention resource syntax out at all — it now focuses on
   tools, tool-search, `WaitForMcpServers`, and config flags — so the
   `@`-mention path is documented only in secondary/older material and is a
   prime "verify live" candidate.

2. **Model-driven synthetic tools.** Claude Code exposes two built-in tools the
   model can call autonomously:
   - **`ListMcpResourcesTool`** — "Lists available data resources (e.g.,
     database schemas, log files) from connected servers" / "enumerates
     available resources from all connected servers."
   - **`ReadMcpResourceTool`** — "Reads the content of a specific resource
     identified by a URI."

   Names confirmed by three independent secondary sources: the title of
   [anthropics/claude-code#11292](https://github.com/anthropics/claude-code/issues/11292)
   ("MCP HTTP server resources not accessible via `ListMcpResourcesTool` and
   `ReadMcpResourceTool`", opened 2025-11-09, closed "not planned", CC 2.0.36),
   and two DeepWiki source analyses of the Claude Code bundle
   ([yasasbanukaofficial](https://deepwiki.com/yasasbanukaofficial/claude-code/3.4-web-lsp-and-mcp-tools),
   [ai-ml-architect](https://deepwiki.com/ai-ml-architect/claude-code/3.5-web-and-mcp-tools),
   both 2026), which reference `src/tools/ReadMcpResourceTool/ReadMcpResourceTool.ts`.
   **The exact input-parameter schema (`server`? `uri`? which required?) is not
   published anywhere I could find** — DeepWiki says only "ReadMcpResourceTool
   takes a resource URI." This is itself a finding: pin it from the live
   transcript, don't hardcode a guess.

### Changelog timeline (from `github.com/anthropics/claude-code/blob/main/CHANGELOG.md`, fetched 2026-09-05, version headers verified by `awk`)

| Version | Entry (verbatim) |
|---|---|
| 1.0.27 | "MCP resources can now be @-mentioned" |
| 1.0.44 | "MCP: resource_link tool results are now supported" |
| 2.1.0 | "Added support for MCP `list_changed` notifications, allowing MCP servers to dynamically update their available tools, prompts, and resources without requiring reconnection" |
| 2.1.6 | "Removed ability to @-mention MCP servers to enable/disable - use `/mcp enable <name>` instead" |
| 2.1.89 | "Added `MCP_CONNECTION_NONBLOCKING=true` for `-p` mode to skip the MCP connection wait entirely, and bounded `--mcp-config` server connections at 5s instead of blocking on the slowest server" |
| 2.1.89 | "Improved `@`-mention typeahead to rank source files above MCP resources with similar names" |
| 2.1.116 | "Faster MCP startup when multiple stdio servers are configured; `resources/templates/list` is now deferred to first `@`-mention" |
| 2.1.122 | "OpenTelemetry: added `claude_code.at_mention` log event for `@`-mention resolution" |
| 2.1.139 | "Fixed MCP resources from disconnected servers lingering in `@server:` autocomplete" |
| 2.1.147 | "Fixed paginating MCP servers dropping resources, templates, and prompts past page 1" |
| 2.1.214 | "Fixed MCP transient errors during prompts/resources refresh clearing the server's slash commands and resources" |
| 2.1.246 | "Fixed `--strict-mcp-config` sessions prompting to approve `.mcp.json` servers they would never load" |
| 2.1.257 | (post-dates installed 2.1.252) same `--strict-mcp-config` fix continued |

Takeaways: `@`-mention of resources has existed since 1.0.27 and is still
actively maintained (2.1.139, 2.1.214). `resources/templates/list` support is
real but lazy since 2.1.116 — **it only fires on the first `@`-mention**, which
means a headless `-p` run that never `@`-mentions may never trigger template
discovery at all. `-p`-mode MCP has known timing sharp edges (2.1.89's
`MCP_CONNECTION_NONBLOCKING`, 5s bound).

### The "discovery only, never reads" claim — credible but stale, unverified on 2.1.x

A [DollhouseMCP research report dated
2025-10-16](https://glama.ai/mcp/servers/@DollhouseMCP/DollhouseMCP) (surfaced
via search; the raw file 404/429'd on direct fetch) states plainly, per search
excerpts: *"Claude Code does NOT call `resources/read` - resources are never
actually fetched. Protocol logs show `resources/list` called successfully, but
`resources/read` NEVER called"*; *"Resources will appear in @-mention
autocomplete but won't be injected into conversations"*; *"As of October 2025,
no major MCP client automatically reads resources."*

This is ~11 months old and predates the entire 2.1.x line, `ReadMcpResourceTool`
maturity, and the pagination/`resource_link` fixes above. It is exactly the kind
of claim `knowledge/mcp-resources.md` should either confirm or retire against a
live 2.1.252 run rather than cite second-hand. **Flag as possibly stale.**

Corroborating, but also aging: [modelcontextprotocol/csharp-sdk#1415](https://github.com/modelcontextprotocol/csharp-sdk/issues/1415)
(opened + closed 2026-03-06/07). The reporter found Claude Code CLI **did not
show resource *templates* at all** in the `@`-picker — only static resources
registered via `resources/list` appeared; adding an explicit
`WithListResourcesHandler` (static) fixed it. Final comment: *"Claude doesn't
display resource templates at all."* This directly contradicts the 2.1.116
changelog line (which implies templates *are* supported, just lazily) — so the
static-vs-template distinction is genuinely version-sensitive and worth
checking on 2.1.252 with a server that has **both** (the `notes` server does).

### Headless / `-p` mode caveats to design around

- MCP servers connect in `-p` mode and their status is reported in the
  `system`/`init` event's `mcp_servers` array (established in
  `knowledge/claude-code-mcp-connection.md`, verified there against a live run).
- `@`-mention *resolution* in `-p` mode is not documented as working or not.
  The `claude_code.at_mention` OTEL event (2.1.122) is generic. Several
  changelog fixes about `@`-mentions "attaching nothing" (2.1.x) are all about
  *files*, not MCP resources. **Whether a `@notes:notes://index` token inside a
  `-p "..."` prompt string is resolved and its content attached is an open
  empirical question** — so the asserted path in the build below is the
  model-callable tools (which behave like any other tool in headless mode), and
  the `@`-mention path is *captured and documented*, not asserted.

### The server to point at already exists — reuse it

`examples/mcp-resources-vs-tools/server.py` is `MCPServer("notes")` with exactly
the three handlers this test wants:

- `notes://index` — **static** resource (`@mcp.resource("notes://index")`),
  returns `json.dumps([{"id","title"}, ...])`; seeded with one note
  `{id:"1", title:"Welcome", body:"This is the seeded first note."}`.
- `notes://{note_id}` — **resource template**
  (`@mcp.resource("notes://{note_id}")`), returns full `{id,title,body}` JSON;
  unknown id raises `ResourceNotFoundError` → `MCPError(-32602)`.
- `create_note(title, body)` — tool, the only writer.

It has `if __name__ == "__main__": mcp.run()` (stdio default) and a
`requirements.txt` of `mcp>=2.0.0,<3`. `run_e2e.sh` can point `--mcp-config` at
`<that dir>/.venv/bin/python <that dir>/server.py` — precisely the "reuse the
server, add only the connection + verification layer" move
`examples/mcp-connect-claude-code/run_e2e.sh` makes with `mcp-hello-world`. The
static resource's *content* contains the string `Welcome`, which appears in a
transcript **only if `resources/read` actually ran** — a direct, mechanical
test of the DollhouseMCP claim.

### The pattern to mirror: `examples/mcp-connect-claude-code/`

That example is the established template for "real billed `claude` CLI run,
mechanically verified":

- `run_e2e.sh` — impure shell: fail-fast on missing `claude` / missing venv,
  build a throwaway `mktemp` `--mcp-config`, `--strict-mcp-config` so the repo's
  own `.mcp.json` (`hn-search`) can't leak in, branch on `ANTHROPIC_API_KEY` for
  `--bare`, retry `MAX_ATTEMPTS=2` to absorb tool-choice non-determinism, exit 0
  only if the verifier passed on some attempt.
- `assert_stream.py` — pure `check(events, ...) -> Result`, no I/O beyond
  `main()`'s stdin read; short-circuits on first failure so the reported reason
  is the true first cause; raises only on a malformed transcript (caller bug),
  never folds that into a `False` verdict.
- `test_assert_stream.py` — offline, no key: one real recorded fixture +
  ~9 one-field mutations, each asserting `check()` fails *for the right reason*.
- `fixtures/real_transcript.jsonl` — a real capture from the build, not
  synthesized.
- README documents cost ($0.026 for the one recorded run in that build, driven
  by non-`--bare` ambient context; "a fraction of a cent" under `--bare`).

Wire-shape facts that carried over and should be assumed here too: tool calls
are `assistant` event `message.content[]` entries `{type:"tool_use", id, name,
input}`; results are `user` event `message.content[]` `{type:"tool_result",
tool_use_id, content}` plus a Claude-Code decoration `tool_use_result` on the
same event; final event is `{type:"result", is_error, result, total_cost_usd}`
with `is_error` (not `isError`) at top level.

### Naming / collision check

`examples/mcp-resources-claude-code/` — **free**. Not in `ls examples/`
(`mcp-connect-claude-code`, `mcp-hello-world`, `mcp-hn-search`,
`mcp-resources-vs-tools` are the MCP dirs). Not an open PR (`gh pr list`: #39
docs-timing, #36 phase-gating — both unrelated). Not a local or remote branch
(`git branch -a`: the two stranded `cycle/*-unshipped-*` branches are MCP
prompts and strict-schemas; the rest are pipeline infra). Distinct in purpose
from `mcp-resources-vs-tools` (offline protocol contract) and
`mcp-connect-claude-code` (a *tool*, not resources).

## Build proposal

Layers 1–3 of the Engineering Protocol, for the builder.

New directory `examples/mcp-resources-claude-code/`. Reuses
`examples/mcp-resources-vs-tools/server.py` unchanged as the server under test —
this increment adds only the connection + verification layer, exactly as
`examples/mcp-connect-claude-code/` does with `mcp-hello-world`. Also updates
`knowledge/mcp-resources.md` and `knowledge/INDEX.md`.

### 1. Intent

Prove mechanically how the real `claude` 2.1.252 host surfaces the `notes`
server's MCP resources over stdio in headless mode: that the static resource
`notes://index` is **discoverable** (`resources/list` runs and the URI reaches
the model) and **readable** (`resources/read` runs and the handler's output —
containing the string `Welcome` — comes back), via the model-callable
`ListMcpResourcesTool` / `ReadMcpResourceTool`; and separately **capture and
document** what a `@notes:notes://index` mention in a `-p` prompt actually does.
Then correct `knowledge/mcp-resources.md`'s "Claude Code's actual behavior"
section from "verified against current docs" to "verified live on 2.1.252",
including whether the "discovery only, never reads" claim still holds and
whether resource *templates* surface.

**Out of scope:**
- Changing `examples/mcp-resources-vs-tools/server.py` in any way.
- The `.mcp.json` project-scope approval workflow (sidestepped by
  `--mcp-config` + `--strict-mcp-config`, same as the sibling example).
- Resource *subscriptions* / `list_changed` notifications.
- OAuth / remote (HTTP/SSE) MCP servers — stdio only.
- Making the `@`-mention path a pass/fail acceptance gate (it is recorded, not
  asserted — its behaviour in `-p` mode is the open question, not a
  prerequisite).
- Any second live run to exercise a resource-*not-found* / template path
  (`notes://{note_id}`); note it in the README as the obvious follow-up, keep
  the live-call budget minimal.
- Reproducing the run without an API key: like `mcp-connect-claude-code`, the
  offline `test_assert_stream.py` is the keyless/CI part; `run_e2e.sh` is
  billed.

### 2. Behavioral spec

#### `assert_stream.py` — pure verifier

**Input:** a list of parsed `stream-json` events (dicts), plus the constants
`SERVER_NAME = "notes"`, `RESOURCE_URI = "notes://index"`, and the expected
content marker `EXPECTED_MARKER = "Welcome"` (the seeded note's title; defined
once, matching `server.py`'s seed — the README states the coupling explicitly
the way `mcp-connect-claude-code` does for its word count).

**Output:** `Result(passed: bool, reason: str)`. Pure — no I/O, clock, env, or
network. `main()` is the only impure part: read stdin, print one
`PASS`/`FAIL: <reason>` line, exit 0/1.

**Tool-name resolution:** the exact on-wire `name` for the list/read tools is
not publicly documented. `assert_stream.py` matches against an ordered
candidate tuple per tool — `LIST_TOOL_NAMES = ("ListMcpResourcesTool",
"mcp__notes__list_resources", ...)`, `READ_TOOL_NAMES = ("ReadMcpResourceTool",
...)` — and `check()`'s success `reason` reports *which* spelling matched, so
the transcript pins reality. The builder seeds the tuples from what the first
recorded run actually shows; unknown spellings fail criterion 2/3 loudly, not
silently.

**Invariants:**
- `check()` short-circuits on the first failed criterion; the reason names the
  true first cause, not a downstream symptom.
- A transcript line that is not valid JSON, or a top-level event that is not a
  dict, raises (`JSONDecodeError` / `ValueError`) — a corrupted/truncated
  capture is a caller bug, never folded into `passed=False`.
- The verifier never calls `claude`, never touches the network, never reads a
  file except `main()`'s stdin.

**Failure modes (each a distinct `Result(False, reason)`):**
- No `system`/`init` event.
- `notes` absent from `init.mcp_servers`, or present with status ≠ `connected`.
- No `assistant` `tool_use` matching any `LIST_TOOL_NAMES` spelling.
- The list tool's `tool_result` is an error, or its content does not contain the
  substring `notes://index` (host did not surface the static resource to the
  model).
- No `assistant` `tool_use` matching any `READ_TOOL_NAMES` spelling whose input
  references `notes://index` (checked as: some string value in the `input` dict
  equals or contains `notes://index`).
- The read tool's `tool_result` is an error, or its content does not contain
  `EXPECTED_MARKER` (`resources/read` did not run, or returned nothing — the
  "discovery only" outcome).
- No final `type:"result"` event, or its `is_error` is not `false`.

**Acceptance criteria (offline unless marked LIVE):**

1. `check()` against the recorded `fixtures/read_transcript.jsonl` returns
   `passed=True`, and its `reason` names the list-tool and read-tool spellings
   that matched.
2. A hand-built minimal passing event list (independent of the fixture) passes.
3. Each of these one-field mutations of the minimal list fails `check()` with
   the documented reason substring: `notes` removed from `mcp_servers`; `notes`
   status `failed`; list `tool_use` replaced with a plain `text` block; list
   `tool_result` content stripped of `notes://index`; read `tool_use` removed;
   read `tool_result` content stripped of `Welcome`; read `tool_result` marked
   `is_error`; final `result` event `is_error: true`; final `result` event
   deleted. (~9 mutations — same discipline as
   `mcp-connect-claude-code/test_assert_stream.py`.)
4. A malformed transcript line makes `_parse_events` raise, not return a
   `False` verdict.
5. `python3 test_assert_stream.py` prints an `ok` line per case and
   `All N self-tests passed.`; exit 0. No key, no network.
6. **LIVE** (`./run_e2e.sh`, one billed `haiku` run, retried ≤2×): the script
   builds `examples/mcp-resources-vs-tools/.venv` if absent, writes a throwaway
   `--mcp-config` with a run-time-resolved absolute path to that venv's python
   + `server.py`, invokes
   `claude [--bare] --strict-mcp-config --mcp-config <f> --allowedTools
   "<list+read tool names>" --model haiku --output-format stream-json --verbose
   -p "<prompt: read the notes://index MCP resource and report the title of the
   first note>"`, pipes the transcript to `assert_stream.py`, and exits 0 iff
   `check()` passed on some attempt. Prints `PASS` and the matched tool
   spellings on success.
7. **LIVE, recorded-not-asserted:** a second `claude -p` invocation whose
   prompt contains the literal token `@notes:notes://index` (and asks the model
   to quote the resource). Its full transcript is saved to
   `fixtures/at_mention_transcript.jsonl`. `run_e2e.sh` prints a one-line
   summary — did an `init`/user event show the resource resolved/attached? did
   it error? — but its exit code does **not** depend on this run. The README's
   findings section and the knowledge-note update are written from this
   transcript.
8. `README.md` states: the cost (a small `haiku` run; may exceed a fraction of a
   cent if `ANTHROPIC_API_KEY` is unset and it falls back to non-`--bare`
   ambient auth, per `mcp-connect-claude-code`'s measured $0.026); that
   `test_assert_stream.py` is the free/keyless part; the exact seeded-marker
   coupling to `server.py`; and the "explicitly out of scope" list from §1.
9. `knowledge/mcp-resources.md`'s "Claude Code's actual behavior (verified
   against current docs, 2026-08-09)" section is rewritten to "verified live on
   `claude` 2.1.252, 2026-09-05" with: whether `resources/list` ran and the
   static resource reached the model; whether `resources/read` ran (i.e.
   whether the 2025-10 "discovery only, never reads" claim still holds);
   whether the `notes://{note_id}` template surfaced; and exactly what the
   `@notes:notes://index` mention did in `-p` mode. `knowledge/INDEX.md`'s
   `[[mcp-resources]]` bullet is updated to say "verified live" not "against
   current docs". A dated line is added to `knowledge/mcp-resources.md`'s
   history; the older 2026-08-09 verification line is kept, not deleted
   (expand/contract, Protocol §5).

**Branch if the first live run shows reads genuinely fail on 2.1.252** (the
DollhouseMCP outcome): that is a valid, shippable result. Flip criterion 6 to
assert the *observed* behaviour — list tool surfaces `notes://index`, read tool
returns an error / empty (assert on `is_error` or absent marker) — capture that
transcript as the fixture, and write the knowledge note to match ("2.1.252
still lists but does not read stdio resources"). The offline test structure is
unchanged; only which state the fixture and the `check()` polarity encode.
Same-day either way.

### 3. Interfaces (no bodies)

```python
# assert_stream.py

SERVER_NAME = "notes"
RESOURCE_URI = "notes://index"
EXPECTED_MARKER = "Welcome"          # seeded note title in mcp-resources-vs-tools/server.py

LIST_TOOL_NAMES: tuple[str, ...] = ("ListMcpResourcesTool",)   # builder pins from 1st capture
READ_TOOL_NAMES: tuple[str, ...] = ("ReadMcpResourceTool",)


@dataclass(frozen=True)
class Result:
    passed: bool
    reason: str


def _find_init_event(events: list[dict]) -> dict | None: ...
def _server_connected(events: list[dict]) -> Result | None: ...   # None = ok
def _find_tool_use(events: list[dict], names: tuple[str, ...]) -> dict | None: ...
def _find_tool_result_event(events: list[dict], tool_use_id: str) -> dict | None: ...
def _tool_result_is_error(result_event: dict, tool_use_id: str) -> bool: ...
def _tool_result_text(result_event: dict, tool_use_id: str) -> str: ...   # joined content, for substring checks
def _input_references_uri(tool_use: dict, uri: str) -> bool: ...
def _find_final_result(events: list[dict]) -> dict | None: ...

def check(events: list[dict]) -> Result:
    """Six criteria in order (server connected -> list tool called -> list
    result surfaces notes://index -> read tool called for that URI -> read
    result not-error and contains EXPECTED_MARKER -> final result is_error is
    False). Short-circuits; `reason` on success names the matched
    LIST/READ tool spellings. Raises only via _parse_events on a malformed
    transcript.
    """


def _parse_events(lines: list[str]) -> list[dict]: ...   # raises on bad JSON / non-dict
def main(argv: list[str]) -> int: ...                     # stdin -> one line -> 0/1
```

```bash
# run_e2e.sh  (mirrors examples/mcp-connect-claude-code/run_e2e.sh)
#   SERVER_DIR = <repo>/examples/mcp-resources-vs-tools
#   PYBIN      = $SERVER_DIR/.venv/bin/python     (fail fast if absent, with the build command)
#   SERVER     = $SERVER_DIR/server.py
#   fail fast if `claude` not on PATH
#   CONFIG_FILE = mktemp; {"mcpServers":{"notes":{"type":"stdio","command":"$PYBIN","args":["$SERVER"]}}}
#   trap-rm CONFIG_FILE + transcripts on EXIT
#   ANTHROPIC_API_KEY set    -> prepend --bare
#   ANTHROPIC_API_KEY unset  -> ambient auth, warn on stderr about extra cost (per sibling README)
#   READ_PROMPT      = "Use the MCP resource tools to read the resource notes://index from the
#                       'notes' server, then tell me the title of the first note. Report only that title."
#   AT_MENTION_PROMPT= "Quote the contents of @notes:notes://index verbatim."
#   attempt loop (<=2): claude <args> -p "$READ_PROMPT" > T; python3 assert_stream.py < T; exit 0 on first pass
#   after the asserted loop: one un-retried claude <args> -p "$AT_MENTION_PROMPT" > fixtures/at_mention_transcript.jsonl
#     print a one-line human summary of what it did; do NOT gate exit code on it
#   exhausted attempts -> print last reason, exit 1
```

```
examples/mcp-resources-claude-code/
  README.md
  run_e2e.sh                 # impure orchestrator (billed)
  assert_stream.py           # pure verifier
  test_assert_stream.py      # offline self-test, no key
  fixtures/
    read_transcript.jsonl        # real capture, asserted path
    at_mention_transcript.jsonl  # real capture, documented-only path
```

No change to `assert_stream.py`'s public surface beyond `check(events)` +
`main`; everything else underscore-private (Protocol §2).

## Open questions

1. **Does `@`-mention resolution run in `-p`/headless mode at all?** No doc
   says yes or no; changelog `@`-mention fixes are all file-scoped. If it does
   not resolve in `-p`, `fixtures/at_mention_transcript.jsonl` will show the
   token passed through as literal text — still a documentable result, and the
   reason the asserted path uses the model-callable tools instead.
2. **Exact input schema of `ReadMcpResourceTool`** — `{uri}` only, or
   `{server, uri}`? Undocumented publicly; the build pins it from the first
   transcript and encodes candidates in `READ_TOOL_NAMES` / the input check.
3. **On-wire tool `name`** — `ListMcpResourcesTool` / `ReadMcpResourceTool`
   verbatim (built-in, source-file names) vs. some `mcp__`-prefixed synthesis.
   The `--allowedTools` value and the `*_TOOL_NAMES` tuples both depend on
   this; resolved on first run.
4. **Do resource *templates* (`notes://{note_id}`) surface on 2.1.252?**
   csharp-sdk#1415 (2026-03) says templates were invisible then; the 2.1.116
   changelog implies lazy-but-present now. Not on the asserted path, but the
   knowledge note should record what `ListMcpResourcesTool` returns for the
   template (present? absent? only after an `@`-mention?).
5. **Does the "discovery only, never reads" claim survive 2.1.252?** The whole
   point of the live run. If reads work, retire the claim in the knowledge
   note; if not, record it as still-true with the version and date.
6. **`system`/`init` contents for resources** — does `init` enumerate
   resources anywhere, or only `mcp_servers: [{name,status}]`? Assumed the
   latter (per `knowledge/claude-code-mcp-connection.md`); the build confirms.
