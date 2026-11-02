# MCP 2026-07-28 Multi Round-Trip Requests: elicitation without a held-open stream

An MCP tool that asks the human "really delete this?" before it acts. The server
is driven offline under **both** protocol eras, so you can see the wire-level
difference while the tool code stays exactly the same:

| Protocol | How the question reaches the client |
|---|---|
| **2026-07-28** (MRTR, SEP-2322) | `tools/call` *returns* an `InputRequiredResult` (`resultType: "input_required"`, `inputRequests`, a sealed `requestState`). The client answers, then **retries the same `tools/call`** carrying `inputResponses` and the echoed `requestState`. No session and no server-initiated request. |
| **2025-11-25** (legacy) | While the `tools/call` is still open, the server sends a standalone `elicitation/create` request. The tool call returns only after the client has answered it. |

Elicitation is worth demoing because it is the one server-to-client feature the
2026-07-28 spec did *not* deprecate (Roots, Sampling and Logging are deprecated).

There are no subprocesses and no network calls, and you don't need an API key.

From the research note:
[`research/2026-11-02-mcp-mrtr-elicitation.md`](../../research/2026-11-02-mcp-mrtr-elicitation.md).
Background: [`knowledge/mcp-mrtr.md`](../../knowledge/mcp-mrtr.md).

## What's here

| File | What it is |
|---|---|
| `policy.py` | The pure core. It holds the `Confirm`/`TypedCount` form schemas, a `Decision` enum, and `decide()`/`decide_purge()`, which map an `ElicitationResult` (accept / decline / cancel) to a `Decision`. There is no I/O. |
| `server.py` | `build_server(store)` returns `MCPServer("mrtr-demo")` with `delete_note(note_id)` (one question) and `purge_notes()` (two dependent questions). Each question is a resolver that returns `Elicit(...)`. Running `python server.py` serves it over stdio. |
| `run_demo.py` | Prints the transcript below. The 2026-07-28 leg drives the MRTR loop by hand so you can see the intermediate result. |
| `test_mrtr.py` | 9 offline self-tests that use the SDK's in-memory `Client`. |
| `requirements.txt` | `mcp==2.3.0`. |

Declaring the question with SDK 2.3.0 (names confirmed by import):

```python
from mcp.server.mcpserver import Elicit, ElicitationResult, MCPServer, Resolve

def ask_delete(note_id: str) -> Elicit[Confirm]:          # resolver: gets tool args by name
    return Elicit(f"Delete note {note_id!r}?", Confirm)

@server.tool()
def delete_note(note_id: str,
                confirm: Annotated[ElicitationResult[Confirm], Resolve(ask_delete)]) -> str:
    ...                                                   # `confirm` is filled by the framework,
                                                          # not by the LLM; it is not in inputSchema
```

If you annotate the parameter as `ElicitationResult[Confirm]`, the tool receives
accept, decline or cancel and can branch on each. If you annotate it as plain
`Confirm`, a decline or cancel aborts the call with a tool error.

## Run it (no API key, no network)

```bash
cd examples/mcp-mrtr-elicitation
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python run_demo.py
```

Demo transcript (`run_demo.py`; `test_mrtr.py` fails if this block drifts):

```
== 2026-07-28: Multi Round-Trip Request ==
client -> tools/call delete_note {"note_id": "n1"}
server -> resultType='input_required', requestState=<sealed, opaque>
           inputRequests['server:build_server.<locals>.ask_delete']: elicitation/create "Delete note 'n1' ('buy milk')?"
human  -> accept {"ok": true}
notes before retry: ['n1', 'n2']
client -> tools/call delete_note (same args + inputResponses + requestState)
server -> resultType='complete': deleted n1
notes after: ['n2']

== 2025-11-25: legacy, server-initiated request ==
client -> tools/call delete_note {"note_id": "n1"}
server -> elicitation/create "Delete note 'n1' ('buy milk')?"  (standalone request, mid-call)
human  -> accept {"ok": true}
server -> tools/call result: deleted n1
notes after: ['n2']
```

Then the self-test:

```bash
.venv/bin/python test_mrtr.py
```

`test_mrtr.py` — Expected output:

```
ok  2026-07-28: accept ok=true -> 'deleted n1', note gone, asked exactly once
ok  2026-07-28: ok=false and action=decline both keep the note; result says 'kept'
ok  manual drive: resultType=input_required, note intact; retry with request_state completes
ok  legacy 2025-11-25: same result and end state, asked once, no InputRequiredResult
ok  purge_notes needs 2 rounds: default cap -> 'purged 2'; max_rounds=1 -> InputRequiredRoundsExceededError
ok  tampered requestState and one replayed on note_id=n2 both -> MCPError; nothing deleted
ok  unknown note_id -> is_error naming the id; the human is never asked
ok  policy: 7 outcome cases map to the expected Decision
ok  README demo transcript equals run_demo.py output

All 9 self-tests passed.
```

Three lines also appear on **stderr**:
`requestState rejected on tools/call: seal`,
`requestState rejected on tools/call: request binding`, and
`Tool 'delete_note' failed: ... no note with id 'nope'`. These are the SDK
logging the rejections and the failure that the tests trigger on purpose. They
do not mean a test failed.

## What the tests pin down

1. **Modern, accept.** With `mode="2026-07-28"` and `elicitation_callback=`,
   `client.call_tool` runs the retry loop for you. The note is deleted and the
   callback runs exactly once.
2. **Modern, no.** Accepting the form with `ok=false` and explicitly declining
   both keep the note. They come back as different reasons (`kept: answered no`
   and `kept: declined`).
3. **Manual drive.** `client.session.call_tool(..., allow_input_required=True)`
   returns the raw `InputRequiredResult`, and the note is **still there** at that
   point. Retrying with `input_responses={key: ElicitResult(...)}` and the
   returned `request_state` completes the call. The response keys are the keys
   of `inputRequests`. In SDK 2.3.0 a key is the resolver's
   `module:qualname`.
4. **Legacy parity.** With `mode="legacy"` you get the same result, the same end
   state and one question. Even with `allow_input_required=True`, the call
   returns a `CallToolResult` directly, because nothing on that wire returns
   `input_required`.
5. **Rounds, not questions.** `purge_notes` asks a yes/no question and then
   "type the count". The second resolver depends on the first, so it cannot be
   asked until a round later. That makes two round trips. Under the default cap
   (10) it purges. With `input_required_max_rounds=1` it raises
   `InputRequiredRoundsExceededError` after the first question, and nothing is
   deleted. Independent questions would be batched into a single round.
6. **`requestState` is sealed and bound.** The SDK seals it with AES-GCM and
   binds it to the method, tool and arguments. A modified token is rejected
   (`seal`), and so is a valid token replayed on `note_id="n2"` (`request
   binding`). Both reach the client as `MCPError: Invalid or expired
   requestState`, and nothing is deleted. This matters because the spec treats
   the echoed state as attacker-controlled.
7. **The README cannot drift.** The demo block above is compared with
   `run_demo.py`'s output on every test run. The `Expected output` block is the
   one that `examples/readme-transcript-check/sweep.py` checks.

## Failure modes and caveats

- **The state key is process-local.** By default `MCPServer` uses
  `RequestStateSecurity.ephemeral()`. A `requestState` minted before a server
  restart, or by a different replica, is rejected. To resume across restarts or
  replicas, pass `request_state_security=` with a shared key. The `requestState`
  also has a TTL. None of this is exercised here.
- **Resolver bodies re-run on every round.** They must be idempotent and free of
  side effects. The resolvers here only read the store and build a question. The
  SDK records only *answered* outcomes in `requestState`, so the human is asked
  each question once.
- **A plain `T` annotation aborts on decline.** `purge_notes`'s first question
  uses plain `Confirm`, so declining it makes the call fail with a tool error
  rather than return "kept". `delete_note` uses `ElicitationResult[Confirm]`
  so that it can report the reason.
- **The capability is required.** If the client declares no form elicitation
  capability (that is, no `elicitation_callback`), the call fails with
  `MCPError: Client did not declare the form elicitation capability`. The
  manually driven legs pass a callback only so that the capability is
  advertised. `run_demo.py`'s callback raises if it is ever reached.
- **The store is a caller-owned dict that is mutated in place.** That lets the
  tests observe it. Give each server its own dict.

## Deviations from the proposal

- **`decide(outcome)` instead of `decide(note_id, outcome) -> str`.** The policy
  returns a `Decision` enum. The tool formats the text, so the core has no
  string protocol and doesn't need the id.
- **Criterion 5 uses a real two-question tool** (`purge_notes`) rather than a
  resolver that never settles. It is a legitimate reason to need more than one
  round, and it shows that the cap counts rounds.
- **Criterion 6 also covers replaying a token on other arguments** (request
  binding), in addition to modifying it.
- **Not `python -I -m unittest`.** This follows the repo's convention of a
  `test_*.py` script that prints `ok` lines. `-I` would drop the example's own
  directory from `sys.path`, so `server`/`policy` would not import.
- **The README transcript check is part of the self-test.** The repo's
  checker accepts only one `Expected output` block per README, so it covers the
  test output, and the demo block is compared by the self-test instead.

## Explicitly out of scope

HTTP transport (including the `Mcp-Method`/`Mcp-Name` headers and the
`HeaderMismatch` `-32020` error), auth and principal binding, the tasks
extension, sampling, roots, `server/discover` probing (`mode="auto"`), and any
real LLM.
