# Connecting a real Claude Code session to an MCP server (end-to-end)

Everything else in this repo's MCP examples (`examples/mcp-hello-world`,
`examples/mcp-hn-search`) proves a server *object* works, against the SDK's
own in-memory `Client` -- no subprocess, no real host, no network. This
example proves the other half: that the real `claude` CLI can discover,
connect to, and actually *invoke* a locally-configured MCP server, end to
end, and checks that mechanically rather than by eyeballing a transcript.

From the research note:
[`research/2026-08-08-mcp-connect-claude-code.md`](../../research/2026-08-08-mcp-connect-claude-code.md).

## What's here

| File | What it is |
|------|-----------|
| `run_e2e.sh` | Orchestrator (impure shell): builds a throwaway `--mcp-config` file, invokes the real `claude` CLI against it, retries up to twice to absorb tool-choice non-determinism, hands the transcript to `assert_stream.py`. |
| `assert_stream.py` | Pure verifier: `check(events, expected_word_count) -> Result`. No I/O beyond `main()`'s one `stdin` read. Applies the four acceptance checks below. |
| `test_assert_stream.py` | Offline self-test of `assert_stream.py` -- no live `claude` call, no cost. Runs `check()` against a real recorded transcript plus nine hand-built mutations, one per failure mode. |
| `fixtures/real_transcript.jsonl` | A real `stream-json` transcript captured from an actual `claude --mcp-config ... -p ...` run during this example's build (see below) -- not synthesized from the docs. |

Reuses `examples/mcp-hello-world/server.py`'s `count_words` tool rather than
duplicating server code; this example adds only the connection + verification
layer.

## Run the offline self-test (no API key, no network, no cost)

```bash
cd examples/mcp-connect-claude-code
python3 test_assert_stream.py
```

Expected output: 10 `ok` lines (1 real-transcript pass case, 1 synthetic pass
case, 8 mutation-based failure cases) and `All 10 self-tests passed.`

This is what CI / a reviewer without an API key can run. It's a genuine test
of the parsing and verification logic, independent of the paid live call
below -- it is not a mock asserting on itself, since the primary case runs
against a real recorded transcript from an actual `claude` process, not one
hand-typed to match the code.

## Run the live end-to-end test (real API call, small cost)

```bash
cd examples/mcp-hello-world && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # once, if not already built
cd ../mcp-connect-claude-code
./run_e2e.sh
```

Prerequisites: the `claude` CLI on `PATH`, and either `ANTHROPIC_API_KEY` set
(runs in `--bare` mode) or an already-logged-in Claude Code session (see
"Cost and prerequisites" below for why this matters).

Expected output (verified during this build):

```
--- attempt 1/2 ---
PASS: mcp server connected, tool invoked, result correct, final result success
run_e2e.sh: PASS on attempt 1/2.
```

Exit code `0`. On failure, `run_e2e.sh` prints the reason from
`assert_stream.py` and, for a `claude`-level failure (non-zero exit), the raw
transcript, then retries once before giving up (bounded -- never an infinite
loop).

## The four acceptance checks (`assert_stream.py`'s `check()`)

1. The `system`/`init` event's `mcp_servers` array contains `hello-world`
   with status `connected` (not missing, not errored).
2. At least one `assistant` event contains a `tool_use` block named
   `mcp__hello-world__count_words` -- catches the case where the model
   answers correctly *without* calling the tool (a lucky guess), which
   checking only the final text would miss.
3. The corresponding tool result is not an error, and its numeric result
   equals the true word count of the fixed test string, computed
   independently in `run_e2e.sh` with `str.split()` -- the same string
   never has its count hardcoded a second time anywhere in this example.
4. The final `result` event has `is_error: false`.

Each check is unit-tested independently in `test_assert_stream.py` by
mutating one field of a known-passing transcript and confirming `check()`
fails for the right reason, not just "fails."

## What I verified empirically, against what the research note assumed

The research note flagged three open questions it hadn't executed against a
live run. This build resolved all three:

- **`--mcp-config` + `--strict-mcp-config` does bypass `.mcp.json`'s
  pending-approval workflow entirely.** No hang, no approval prompt, no
  interactive step -- confirmed by running the exact invocation shape
  against a fresh `mktemp` config directory with no prior trust
  relationship. This is the mechanism that makes the test scriptable at all.
- **`--bare` requires `ANTHROPIC_API_KEY`, and this sandboxed build
  environment does not have one set.** `claude --bare -p "say hi"` with no
  key printed `Not logged in · Please run /login` and exited non-zero.
  Without `--bare`, the same invocation succeeded using the environment's
  ambient Claude Code login. `run_e2e.sh` therefore branches: if
  `ANTHROPIC_API_KEY` is set, it adds `--bare` (isolated, minimal context,
  cheaper, and machine-independent); if not, it falls back to ambient auth
  and says so on stderr. **This means the test as run in this build is not
  machine-independent** -- on a machine with only ambient login and no API
  key, it depends on that session already being authenticated, exactly the
  fallback the research note anticipated.
- **The real `stream-json` event shapes matched the docs closely but not
  exactly.** Confirmed field-by-field against `fixtures/real_transcript.jsonl`:
  - The tool result's actual numeric payload is *not* on the plain
    `tool_result` content block in an easily-typed way -- it's a JSON
    *string* (`"{\"result\":6}"`), and the more reliable field is a
    Claude-Code-specific decoration on the `user` event itself,
    `tool_use_result.structuredContent.result` (an actual int).
    `assert_stream.py` prefers that field and falls back to parsing the
    content string.
  - The final event's error flag is `is_error` (not `isError`) at the top
    level of the `type: "result"` event, matching the doc.

## Cost and prerequisites

This is the first example in the repo that makes a real, billed API call as
part of its own self-test (everything before it -- `mcp-hello-world`,
`mcp-hn-search`, `typed-tool-registry`, etc. -- is offline/free). The offline
`test_assert_stream.py` above has no such cost; only `run_e2e.sh` does.

The research note estimated "a fraction of a cent" assuming `--bare` mode
(minimal context, `haiku` model). **That estimate does not hold in this
build environment**, because no `ANTHROPIC_API_KEY` was available and the
test fell back to non-`--bare` mode, which loads the full ambient session
context (skills, subagents, plugins, memory) on top of the `haiku` call. The
one live run recorded in `fixtures/real_transcript.jsonl` cost
**$0.026** (`total_cost_usd`), driven by ~8.5K cache-creation input tokens
of ambient context that `--bare` would have skipped entirely. On a machine
with `ANTHROPIC_API_KEY` set, expect closer to the original fraction-of-a-cent
estimate. Either way, `run_e2e.sh` retries at most twice
(`MAX_ATTEMPTS=2`), so a full failing run costs at most 2x one invocation,
never unbounded.

## Explicitly out of scope

Per the research note's own scoping: OAuth/remote MCP servers, rebuilding or
testing the `.mcp.json` project-scope approval workflow itself (documented
in the research note, not exercised here since `--mcp-config` sidesteps it
by design), and any change to this repo's own `.mcp.json`/`hn-search`
registration.

Also left out of this build specifically:

- **A live-exercised tool-error path.** `_tool_result_is_error()` in
  `assert_stream.py` checks both `is_error` and `isError` spellings on the
  Claude-Code-decorated `tool_use_result` and on the raw content block, but
  this was never exercised against a real `claude` run where the tool
  actually errors (e.g. by making `count_words` reject its input) --
  `test_assert_stream.py`'s error-path test uses a hand-built synthetic
  event, not a second live transcript, to keep the live-call budget to one
  run. If a future cycle wants that confirmed against a real error
  transcript, the cheapest way is a second server tool intentionally
  designed to always fail its schema.
- **The GitHub issue about `enabledMcpjsonServers` being ignored in
  `.claude/settings.local.json`** (from the research note) is unrelated to
  this build's mechanism (`--mcp-config` doesn't touch that setting at all)
  and was not re-investigated.
