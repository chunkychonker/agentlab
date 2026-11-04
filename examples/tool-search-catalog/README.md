# Tool search over a deferred catalog, in a hand-written loop

When an agent has dozens of tools, every definition is sent and loaded into
the model's context on every request. With the API's **tool search tool**, you
mark most definitions `defer_loading: true`. They are still sent, but they stay
out of context until Claude searches the catalog and the server loads the ones
it found. This example shows the two things a hand-written loop must get right
to use it:

1. **Building the `tools` array.** Put the search tool first, keep a few hot
   tools loaded, defer the rest, and avoid the toolsets the API rejects with a 400.
2. **Handling the new blocks.** A turn now contains `server_tool_use` and
   `tool_search_tool_result` blocks. Echo them back unchanged. Send a
   `tool_result` only for normal `tool_use` blocks, never for the `srvtoolu_`
   search call.

From the research note:
[`research/2026-11-04-tool-search-deferred-tools.md`](../../research/2026-11-04-tool-search-deferred-tools.md)
(primary source: the
[tool search tool docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool)).
The loop's shape comes from [`../minimal-agent-loop/`](../minimal-agent-loop/).

## Design (layers 1-3)

- **Intent:** a loop and toolset builder that use server-side tool search
  correctly on a 40-tool catalog. Out of scope: comparing BM25 with regex
  quality, the MCP connector's `mcp_toolset` deferral, custom (embeddings)
  search via `tool_reference` results, streaming, batches, and accuracy benchmarks.
- **Spec:**
  - `build_tools(catalog, hot, variant)` returns the search tool first and then
    every catalog tool exactly once. Tools in `hot` come back unchanged and every
    other tool gets `defer_loading: True`. The input is not mutated, and a `hot`
    name missing from the catalog is an error.
  - `validate_tools` raises `ToolsetError`, with a different message for each
    rule, for these toolsets: a deferred search tool, every tool deferred,
    `defer_loading` together with `cache_control`, and duplicate names.
  - `local_search.search` models the regex search. It runs case-insensitive
    `re.search` over the name, the description, argument names, and argument
    descriptions. The default limit is 5. No match returns `[]`. A pattern over
    200 characters, or a regex that does not compile, returns
    `SearchError("invalid_tool_input", ...)` as a value.
  - The loop sends the identical full `tools` array on every request and echoes
    each assistant turn unchanged. It answers exactly the `tool_use` ids, so
    a reply that answers a server id cannot be built. It raises
    `ToolSearchFailed(error_code)` on the first failed search. Any
    `stop_reason` other than `end_turn` or `tool_use` raises `UnexpectedStop`.
- **Interfaces:**
  - `catalog.make_catalog(n: int) -> list[dict]`, `catalog.HOT_TOOLS: frozenset[str]`
  - `toolset.build_tools(catalog, hot: frozenset[str], variant: SearchVariant) -> list[dict]`, `toolset.validate_tools(tools) -> None`
  - `local_search.search(catalog, pattern: str, limit: int = 5) -> list[str] | SearchError`
  - `blocks.discovered_tools(content) -> list[str]`, `blocks.search_failures(content) -> list[SearchFailure]`,
    `blocks.pending_client_tool_uses(content) -> list[dict]`, `blocks.followup_user_turn(content, outputs: Mapping[str, str]) -> dict`
  - `agent.run_agent(send: Send, tools, user_message, *, max_turns=6, verbose=False) -> RunResult`,
    where `Send = Callable[[dict], Reply]`, and `agent.sdk_send(client) -> Send`

| File | One job |
|------|---------|
| `catalog.py` | A deterministic synthetic catalog: 12 hand-written tools followed by `<service>_<verb>_<noun>` filler, up to 300 tools. Pure. |
| `toolset.py` | Builds and validates the `tools` array (`SearchVariant`, `ToolsetError`). Pure. |
| `local_search.py` | A local reference model of the regex search. It is a model of the documented behavior, not the server. Pure. |
| `blocks.py` | Reads search hits and failures out of a turn and builds the `tool_result` turn. Pure, plain dicts. |
| `backends.py` | Canned, labeled-synthetic results for the fictional tools. |
| `agent.py` | The imperative shell: the loop over an injected `Send`, the SDK adapter, and the live `main`. It is the only place the key is read. |
| `measure.py` | Live input-token comparison, loaded vs deferred. `--method count` costs $0. `--method create` is billed. |
| `recorded.py` | Docs-shaped assistant turns used by the offline checks. They are recorded shapes, not captured traffic. |
| `test_tool_search.py` | Offline self-test, stdlib only (acceptance criteria 1-5). |
| `sdk_contract_check.py` | Runs the loop through the real `anthropic` SDK over a mocked HTTP transport. Needs the SDK but no key. |
| `requirements.txt` | `anthropic>=2.4.0,<3`. The self-test does not need it. |

## Run the self-test (no API key, no network, stdlib only)

```bash
cd examples/tool-search-catalog
python3 test_tool_search.py
```

Expected output:

```
ok  build_tools: one search tool first, 37 of 40 deferred, hot tools plain, valid
ok  validate_tools: 4 invalid toolsets -> 4 distinct ToolsetErrors; unknown hot name rejected
ok  local search: substring, regex, case, arg fields, limit, empty, invalid_tool_input
ok  blocks: discovered [get_weather]; only the toolu_ id is answered; srvtoolu_ answer refused
ok  loop: turn 1 echoed unchanged, same 41 tools resent, tool_result only for toolu_
ok  loop: search error raises ToolSearchFailed(error_code); unknown stop_reason raises
ok  loop: invalid toolset raises ToolsetError before any request
ok  measure: describe_saving reports smaller / no difference / LARGER

All 8 self-tests passed.
```

The loop test is the central one. A fake `Send` replays a docs-shaped turn
(text, `server_tool_use`, `tool_search_tool_result`, text, `tool_use`) and then
an `end_turn`. It checks three things about the second request: it echoes that
first turn deep-equal, search blocks included; it carries the same 41-tool
array with 37 definitions still deferred; and it has exactly one `tool_result`,
for the `toolu_` id. During the build, three deliberate bugs were each caught
by a failing test: answering the server call, ignoring a search error, and
dropping deferred tools from later requests.

## Check the SDK boundary (needs the SDK, still no key)

```bash
cd examples/tool-search-catalog
pip install -r requirements.txt
python3 sdk_contract_check.py
```

Output from this build, against `anthropic==2.4.0`:

```
ok  SDK 2.4.0 sends anthropic-version 2026-10-21
ok  defer_loading and the search tool reach the wire unchanged (create and count_tokens)
ok  parsed search blocks round-trip through model_dump to the exact JSON the server sent
ok  second request: tool_result only for the toolu_ id, same tools; loop returns the answer

All 4 SDK contract checks passed.
```

The third line depends on `exclude_none=True` in `sdk_send`. Without it, the
SDK's models echo back `"caller": null` on `server_tool_use` and
`"citations": null` on text. This was confirmed during the build by removing the
flag: the check then fails. Whether the API would accept those nulls was not
tested. Keeping the echo identical to what the server sent avoids the question.

## Run it live (needs a key)

```bash
cd examples/tool-search-catalog
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python3 agent.py                       # 40 tools, regex search, weather question
python3 agent.py --variant bm25 "Open a GitHub issue titled 'flaky test' in acme/web"
python3 measure.py                     # $0: count_tokens, loaded vs deferred
python3 measure.py --method create     # billed: 2 tiny requests, max_tokens=16
```

`agent.py` prints each search call (`tool_search_tool_regex({"pattern": ...})`),
the tools it discovered, each client tool call, and the final answer. Without
`ANTHROPIC_API_KEY`, both scripts print a skip note and exit 0.

**Not run for this build:** no key was available. So the live part of
acceptance criterion 6 is **unconfirmed**. Nobody has yet checked whether
`count_tokens` honors `defer_loading`. If it rejects the search tool (an
earlier lab note found it rejects some server tools), `measure.py` prints the
API's message, suggests `--method create`, and exits 2. If the two counts come
back equal, it prints `no difference ... this method does not reflect
defer_loading` instead of claiming a saving. The vendor's figure (>85%
reduction) is unverified here. Every branch of `measure.py` (a saving, a 400
rejection, and `--method create`) was run once against a mocked client with
made-up counts. That proves the plumbing, not the numbers.

## Things to know

- **Deferred is not unsent.** Every definition goes over the wire on every
  request. Deferral saves context and input tokens. It does not save bandwidth
  or request-size limits (10,000 deferred tools max).
- **Discovery depends on the descriptions.** The search matches only name,
  description, and argument names and descriptions. If a tool's text lacks the
  word Claude searches for, the tool is invisible. Run `local_search.search`
  on your own catalog to check this offline. Small models may also write poor
  regexes.
- **Failure posture.** A failed search (`invalid_tool_input`, `unavailable`,
  `too_many_requests`) is an HTTP 200 carrying an error block. This loop raises
  `ToolSearchFailed` on it, even if the model recovered later in the same turn.
  That is deliberately strict. An agent that should let the model retry must
  catch it. SDK 2.4.0 also lists a fourth code, `execution_time_exceeded`, which
  the docs page did not mention. The loop handles it the same way.
- **`pause_turn` is not handled.** It raises `UnexpectedStop`. The docs for
  other server tools use it for long turns. Whether tool search ever returns it
  is unverified.
- **The block type is the discriminator.** `pending_client_tool_uses` selects
  `type == "tool_use"`. It never parses the `toolu_`/`srvtoolu_` prefix.
- **The 400s are modeled, not observed.** `validate_tools` encodes the
  documented 400s. A `tool_reference` naming a tool missing from `tools` is
  another documented 400, but it only arises with custom search, which is out
  of scope.
- **Model.** `MODEL = "claude-haiku-4-5"` is on the docs' supported-model list
  for tool search, and the repo uses it for cheap demos. SDK 2.4.0 also lists
  `claude-haiku-5-5`. Its tool-search support was not checked against the docs,
  so it is not the default.
