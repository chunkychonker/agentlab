# MCP prompts, the third primitive

## Question

What is the MCP **prompts** primitive at the protocol/SDK level, how do you
declare and exercise one with the official Python SDK's in-memory `Client`,
and how does Claude Code actually surface it — verified fresh today, not
assumed from a three-week-old note?

## Note on how this topic was picked

`BACKLOG.md`'s Coding agents / Skills / MCP sections have no item literally
marked `[ ]` right now — everything is `[done #N]` except three
`[stranded cycle/<date>-unshipped-*]` entries (a claim a past cycle made and
built against, but whose branch never became a PR — see
`knowledge/pipeline-claim-lifecycle.md`, "it reconciles, it does not
salvage"). `gh pr list --state open` shows #40/#39/#36, none of which touch
these three. Of the three, one (`cycle/2026-09-07-unshipped-023436-1`,
context-editing-vs-caching) **is** already substantially covered by open PR
#40's title — skipped per the standard duplicate check. Of the remaining two,
this one (`cycle/2026-08-29-unshipped-120828-1`, opened 2026-08-29) is the
older/most-stale, so per "pick the most valuable stale one," it's the pick.

**Disclosure for the maintainer/builder:** that stranded branch already
contains a complete attempt at this exact increment —
`research/2026-08-29-mcp-prompts.md`, `knowledge/mcp-prompts.md`, and a built
`examples/mcp-prompts/` with a passing offline self-test (visible via
`git show cycle/2026-08-29-unshipped-120828-1:...`). It was never opened as a
PR, so it's not on `main` and this repo's own policy is that reconciling a
stranded branch is a human judgment call, not something a pipeline phase does
automatically. This note is independently re-researched and re-verified
against today's sources (see below — one of its own citations turned out to
be wrong when checked against a primary source), but it converges on a
similar shape because the underlying facts mostly haven't moved in three
weeks. **Before building, check whether salvaging/rebasing that branch is
faster than building fresh** — this note's job is to make sure today's build,
whichever path is taken, rests on currently-correct facts.

## Findings

### The primitive: user-controlled message templates

Verified today (2026-09-18) against
[the spec, protocol version `2026-07-28`](https://modelcontextprotocol.io/specification/2026-07-28/server/prompts)
(fetched today; still the current protocol version — confirmed via
[the MCP blog's 2026 roadmap post](https://blog.modelcontextprotocol.io/posts/2026-mcp-roadmap/)
and search results, no newer dated spec found).

- **Tools** are model-driven, **resources** are application/host-driven,
  **prompts** are **user-controlled**: the server authors named message
  templates; the *user* picks one (a slash command, a menu, a button) and
  fills in its arguments; the rendered messages enter the conversation as if
  typed. "This refers to who decides *when* the prompt is used, not who
  authors its content." Contrast with [[mcp-resources]] for the other two
  primitives.
- Two RPCs: `prompts/list` (paginated + cacheable metadata: `name`, optional
  `title`/`description`/`icons`, `arguments[]`) and `prompts/get`
  (`{name, arguments}` → `{description, messages[]}`, or an
  `InputRequiredResult` for multi-round-trip argument collection — out of
  scope below).
- **Arguments are a flat list of named string values** — `{name,
  description?, required}`, no JSON Schema. "A form a person fills in, not a
  payload a model constructs."
- A `PromptMessage` is `{role: "user"|"assistant", content: <one block>}` —
  `text`, `image`, `audio`, `resource_link`, or `resource`, **not** an array
  of blocks like an Anthropic API message. Pre-filling an `assistant` message
  is the documented way to steer the model's next turn.
- **Spec error handling, stated explicitly in the spec text itself:**
  invalid prompt name → `-32602` (Invalid params); **missing required
  arguments → `-32602` (Invalid params)**; internal errors → `-32603`. This
  exact wording matters for the gap below.

### The Python SDK (`mcp` on PyPI): now v2.2.0, published 2026-09-07

Cross-checked two ways because they initially disagreed: PyPI's own page
reported "released September 7, 2026"; a first-pass summarized fetch of the
GitHub releases page said "September 7, **2024**" for the same tag. Resolved
against the GitHub REST API directly (`api.github.com/repos/.../releases`,
unauthenticated, no LLM summarization in the loop):

```
v2.2.0 | 2026-09-07T15:53:57Z
v2.1.1 | 2026-08-25T15:58:21Z
v2.1.0 | 2026-08-24T19:00:24Z
```

**Lesson worth keeping:** an LLM-summarized `WebFetch` of a changelog page
can silently invent or corrupt a date (or, as below, an entire feature list)
even when the surrounding text is accurate — cross-check anything
load-bearing against the GitHub REST API (`releases/tags/<tag>`, plain JSON,
no summarization) or a raw file fetch, not a fetch tool's prose summary. This
is the same "verify against code/source, not prose" discipline
`knowledge/mcp-python-sdk.md` and `knowledge/mcp-resources.md` already
record, one layer up (it now applies to the *tooling* used to do the
verifying, not just to docs vs. spec).

**What actually changed where, verified against each release's real
changelog body via the API (not a fetch-tool summary):**

- **v2.1.0 (2026-08-24, PR #3320)** — this is the release that matters for
  prompts. `Message` / `UserMessage` / `AssistantMessage` became importable
  directly from `mcp.server.mcpserver` (confirmed by grepping the raw
  `__init__.py` on `main`: `from .prompts.base import AssistantMessage,
  Message, UserMessage` plus all three in `__all__`) — no more reaching into
  `mcp.server.mcpserver.prompts.base`, though that path still works. Prompt
  messages also gained `Image`/`Audio` content and prompt functions may
  return bare content blocks (both out of scope for the build below).
- **v2.1.1 (2026-08-25)** — a docs-only import-guidance fix, no code change.
- **v2.2.0 (2026-09-07)** — **nothing about prompts.** Its real content
  (verified via `gh api repos/modelcontextprotocol/python-sdk/releases/tags/v2.2.0`):
  HTTP client redirects now restricted to the endpoint's own origin, idle
  Streamable HTTP sessions expiring after 30 minutes, and OAuth
  `issuer`-validation hardening. A first-pass fetch-tool summary had
  attributed the v2.1.0 prompt features to v2.2.0 instead — wrong, caught
  only by re-pulling the release body as raw JSON.
- `Client.get_prompt(name, arguments: dict[str, str] | None = None, ...) ->
  GetPromptResult` and `Client.list_prompts(...) -> ListPromptsResult` —
  signatures confirmed against the current `main` branch's
  `src/mcp/client/client.py`.
- `Client.raise_exceptions: bool = False` is still the field name and
  default, confirmed against current `main` source, matching what the
  stranded branch found against 2.1.1 three weeks ago — unchanged since.

**Practical shape**, matching the tool-decorator pattern
`knowledge/mcp-python-sdk.md` already documents:

```python
from typing import Annotated
from mcp.server import MCPServer
from mcp.server.mcpserver import AssistantMessage, Message, UserMessage
from pydantic import Field

mcp = MCPServer("prompts-demo")

@mcp.prompt(title="Code review")
def review_code(
    code: Annotated[str, Field(description="The code to review.")],
    language: Annotated[str, Field(description="Language name.")] = "python",
) -> str:
    """Ask for a review of a snippet."""
    return f"Please review this {language} code:\n\n{code}"

@mcp.prompt()
def debug_error(error: Annotated[str, Field(description="The error text.")]) -> list[Message]:
    """Seed a debugging conversation."""
    return [
        UserMessage(f"I'm seeing this error:\n\n{error}"),
        AssistantMessage("I'll help debug that. What have you tried so far?"),
    ]
```

- Name = function name, description = docstring, arguments = parameters —
  identical derivation rule to `@mcp.tool()`. A parameter without a default
  is required; with a default, optional. No JSON Schema is generated (there's
  nothing to generate it *from* — arguments are always plain strings).
- Return type decides shape: `str` → one `user` message; `list[Message]` →
  seeded multi-turn conversation.

### The spec-vs-SDK gap: missing/unknown-prompt errors are `-32603`, not `-32602`

Verified against the current `docs/servers/prompts.md` on `main` (fetched
today): "Required arguments are enforced before function execution... Missing
required arguments trigger a JSON-RPC error (code `-32603`) with the message
'Internal server error,' and the detailed reason appears in server logs — not
returned to the user." That is the **spec's** `-32603` bucket ("internal
errors"), not its `-32602` bucket ("invalid params") — the spec text quoted
above says missing-required-argument should be `-32602`. Trace: `Prompt.
render()` raises a plain `ValueError` before your function ever runs; the
in-memory dispatch path wraps any unhandled exception as `INTERNAL_ERROR`
(`-32603`), the same generic bucket a genuine bug in your handler would land
in. An unknown prompt name takes the identical path.

This is the **opposite** choice the same SDK makes for resources — a
`ResourceNotFoundError` gets the spec-correct `-32602`, special-cased in
`server.py` (already documented in `knowledge/mcp-resources.md`). So of the
two SDK-level ID-not-found situations already in this repo's knowledge base,
one follows the spec's error code and the other doesn't, in the same
codebase. Confirmed unchanged by today's re-check of the docs page and by
the v2.1.0/v2.1.1/v2.2.0 changelog bodies — none of the three touches this.

The stranded branch's build-time note additionally recorded (against
installed 2.1.1, three weeks ago) that the **in-memory `Client`'s default**
(`raise_exceptions=False`) sanitizes the message to `'Internal server
error'` even off-wire, and that passing `raise_exceptions=True` recovers the
original `ValueError` text (`"Missing required arguments: {...}"` /
`"Unknown prompt: ..."`) as `str(exc)`, chained as `__cause__`. That's a
runtime behavior, not something re-verifiable from docs alone — today's
source read is consistent with it (nothing in the `raise_exceptions` field
or the dispatch path changed across v2.1.1→v2.2.0), but the build should
still assert it directly rather than trust either note's prose, same
"verify by running it" posture this repo already holds for the resources
example.

### How Claude Code surfaces prompts — docs are thinner than three weeks ago, GitHub corroborates the shape

This is the part search results disagreed with themselves on, which is
exactly the kind of thing this discipline exists to catch:

- A full-text fetch of `code.claude.com/docs/en/mcp` today found **no**
  "Use MCP prompts as commands" section — the page currently documents `/mcp`
  (server connection management) but not prompt-derived slash commands. The
  three-week-old research note on the stranded branch cited that section by
  name; either it moved, was cut, or was never as prominent as the note
  implied — I could not find its current location on official docs pages
  (checked `/docs/en/mcp` and `/docs/en/commands`, neither has it).
- What's still independently corroborated, from a **closed GitHub issue**
  (a primary source, not a blog): [anthropics/claude-code#11054](https://github.com/anthropics/claude-code/issues/11054)
  ("MCP Prompts are not visible to Claude in conversation context," opened
  2025-11-05, **closed as not planned**) — the reporter demonstrates prompts
  *do* appear as slash commands in the form `/mcp__<server>__<prompt>`, are
  invocable, and their rendered content *is* injected into the conversation,
  but the model itself cannot see or suggest them — only the user can trigger
  one. Maintainers closed it "not planned," i.e. this is accepted, intended
  behavior, not a bug awaiting a fix. This is a stronger, more current
  confirmation of the "user-controlled, not model-driven" design than the
  spec's prose alone: it's an explicit host-level choice that survived a bug
  report asking for the opposite.
- Third-party 2026 write-ups (not verified as authoritative, but consistent
  with each other and with the closed issue) describe arguments as
  space-separated positional tokens after the slash command, e.g.
  `/mcp__github__create-issue <args>`. I could not re-find this specific
  claim (positional-argument parsing) on a current official docs page today
  — flagging it as **unconfirmed against official docs as of 2026-09-18**,
  narrower than the three-week-old note's confidence, which cited it as an
  official-docs quote. Treat it as "consistent with community reports and
  the SDK's own arguments shape," not "confirmed spec/docs text."

**Net effect on scope:** the live-host half of this topic is *less* settled
right now than it looked three weeks ago (a docs page seems to have moved or
shrunk), which is itself a reason to keep today's build offline-only (SDK
level, in-memory `Client`) and treat "verify the live slash-command shape
against the real Claude Code CLI" as a distinct, separate future increment —
not something to guess at today. This mirrors how `examples/mcp-resources-vs-tools/`
deliberately deferred its own live-host half to `examples/mcp-connect-claude-code/`'s
territory, and how that live-host-for-resources gap is itself still an open,
separately-stranded backlog item (`cycle/2026-09-05-unshipped-133345-1`).

### Practitioner reception

- Consensus framing across MCP-adjacent write-ups: prompts are "the most
  underused primitive" and server authors are advised to ship a handful next
  to their tools, since they encode the author's own domain knowledge of good
  usage.
- Dissent, from a primary source: [modelcontextprotocol/modelcontextprotocol
  Discussion #1779](https://github.com/modelcontextprotocol/modelcontextprotocol/discussions/1779)
  ("Replace MCP prompts with Skills or make prompts invokable by Agent,"
  opened 2025-11-07, ~10.5 months old at time of writing — flagging as
  approaching a year, re-check if reused later) argues the user-only trigger
  model is a real limitation: an agent that knows a task needs a given prompt
  still can't invoke it. It also directly overlaps this repo's own
  `knowledge/agent-skills.md`. Worth noting: Claude Code has merged custom
  commands into skills, so a synced skill and an MCP prompt can collide in
  the same `/` namespace (skill wins) — a fact this repo's own
  `claude-code-mcp-connection` / `agent-skills` notes are adjacent to but
  don't state directly; not independently re-verified today, carried over
  with attribution rather than asserted fresh.

## Build proposal

### Layer 1 — Intent

Add `examples/mcp-prompts/`: a minimal MCP server exposing **one prompt with
a required + an optional argument, returning a single user message** and
**one prompt returning a multi-message seeded conversation (including a
pre-filled assistant turn)**, plus an offline self-test driving both through
the SDK's in-memory `Client` — completing the tools/resources/prompts
trilogy `examples/mcp-resources-vs-tools/` left at two, using the exact same
in-memory-`Client` testing seam every MCP example in this repo already uses.

**Explicitly out of scope** (all deferred, not solved half-way):
`Image`/`Audio` prompt content and bare-content-block returns (real in
v2.1.0+, but add surface area the two-prompt core doesn't need);
`list_changed` notifications/subscriptions; `InputRequiredResult`
multi-round-trip argument collection; argument autocompletion
(`completions`); wiring into the real Claude Code host (the live-surfacing
question above is genuinely unsettled right now — that's its own increment,
not a guess bolted onto this one). Do **not** modify
`examples/mcp-resources-vs-tools/` — keep that reviewed example's diff at
zero; cross-link from the new README instead.

### Layer 2 — Behavioral spec

**`server.py`** — `MCPServer("prompts-demo")`, two `@mcp.prompt()`
functions, `if __name__ == "__main__": mcp.run()` guard (stdio wire — no
`print()` anywhere in the module, per the existing stdout-is-the-wire
convention this repo already enforces in every MCP example).

1. `review_code(code: <required str>, language: str = "python") -> str`.
   Invariant: the returned string always contains the literal `code` text
   and the resolved `language` (default or override). Failure mode: SDK
   rejects a call missing `code` before this function runs (see #5 below);
   the function body itself cannot fail.
2. `debug_error(error: <required str>) -> list[Message]`. Invariant: returns
   exactly `[UserMessage(...), AssistantMessage(...)]`, in that role order,
   with `error`'s text present in the first message. Same no-internal-failure
   shape as #1.

**`test_server.py`** (in-memory `Client`, same file/print/`main()`
convention as `examples/mcp-hello-world/test_server.py` and
`examples/mcp-resources-vs-tools/test_server.py`):

1. `list_prompts()` returns exactly 2 prompts named `review_code` and
   `debug_error`; `review_code`'s `arguments` mark `code` required and
   `language` not required (mirrors the tool-registry pattern of asserting
   schema/metadata shape without invoking the handler).
2. `get_prompt("review_code", {"code": "print(1)"})` (language omitted) →
   one `user`-role message whose text contains `"print(1)"` and `"python"`
   (the default).
3. `get_prompt("review_code", {"code": "fn main() {}", "language": "rust"})`
   → same shape, text contains `"rust"` instead of the default.
4. `get_prompt("debug_error", {"error": "NullPointerException"})` → a
   2-message list, roles `["user", "assistant"]` in that order, first
   message's text contains `"NullPointerException"`.
5. `get_prompt("review_code", {})` (missing required `code`) via a plain
   `Client(mcp)` → raises `MCPError`; assert `exc.code == -32603` (the
   spec-vs-SDK gap above, made checkable rather than cited) and print the
   observed `str(exc)` so the test output itself shows whether it's
   sanitized or descriptive at the default setting — don't hardcode an
   assumption about the message text, only about the code.
6. Same call via `Client(mcp, raise_exceptions=True)` → still `exc.code ==
   -32603`, and this time `str(exc)` is asserted to contain
   `"Missing required arguments"` — the two-Client contrast made concrete,
   the same "assert both columns" discipline the stranded branch's own
   build-time note used.
7. `get_prompt("no_such_prompt", {})` → `MCPError` with `exc.code == -32603`
   too — same bucket as #5/#6, the direct contrast with
   `examples/mcp-resources-vs-tools/`'s `read_resource()` of an unknown id,
   which this repo's existing test already shows lands on the
   spec-correct `-32602`.

**Acceptance criteria ("it works"):** `python3 test_server.py` exits 0 and
prints 7 `ok  ...` lines (or however many the builder lands on if a case
splits/merges — declare the number in the README and keep the printed count
truthful, per the `[done #24]` backlog lesson about a stale "All N passed"
line). No network, no subprocess, no API key. `pip install -r
requirements.txt` (`mcp>=2.1.0,<3` — v2.1.0 is the actual floor this example
needs, for the top-level `Message`/`UserMessage`/`AssistantMessage` import;
older SDK notes in this repo pin `>=2.0.0,<3`, this one needs the narrower
floor and should say so explicitly rather than copy the wider range) into a
fresh venv succeeds on Python 3.10+.

### Layer 3 — Interfaces

```python
# server.py
from mcp.server import MCPServer

mcp: MCPServer

def review_code(code: str, language: str = "python") -> str: ...
def debug_error(error: str) -> list["Message"]: ...
```

```python
# test_server.py
async def test_lists_exactly_two_prompts() -> None: ...
async def test_review_code_default_language() -> None: ...
async def test_review_code_explicit_language() -> None: ...
async def test_debug_error_returns_seeded_conversation() -> None: ...
async def test_missing_required_argument_raises_dash_32603() -> None: ...
async def test_missing_required_argument_message_with_raise_exceptions_true() -> None: ...
async def test_unknown_prompt_name_raises_dash_32603() -> None: ...

def main() -> int: ...  # asyncio.run(_run_all()); prints "All N self-tests passed."; returns 0
```

## Open questions

- Where (if anywhere) does `code.claude.com` currently document the
  prompt-as-slash-command mapping? Not found today at `/docs/en/mcp` or
  `/docs/en/commands`; only corroborated indirectly via a closed GitHub
  issue and third-party write-ups. Someone should re-check `llms.txt`'s full
  page index before concluding it's genuinely gone from official docs rather
  than just moved.
- Is the positional-argument-parsing claim (space-separated tokens mapped to
  the prompt's declared argument order) still accurate, and is it official
  or reverse-engineered community knowledge? Not independently confirmed
  today against a primary source.
- Whether a scripted `claude --bare --strict-mcp-config --mcp-config ... -p
  "/mcp__prompts-demo__review_code ..."` run (the same recipe
  `knowledge/claude-code-mcp-connection.md` already established for tools)
  actually dispatches the prompt the way an interactive slash command does —
  genuinely untested, and exactly the natural next increment once this one
  ships, the same shape as the still-open `mcp-resources-claude-code`
  live-host gap for resources.
- Whether the stranded branch (`cycle/2026-08-29-unshipped-120828-1`) should
  be salvaged/rebased instead of building fresh — a call for a human per
  this repo's own stated policy, not something this note or the pipeline
  should decide unilaterally.
