# 1-hour cache TTL support in the prompt-caching tool loop

## Question

`examples/prompt-caching-tool-loop/` (built 2026-08-29, PR #35) prices every
cache write at the 5-minute default and explicitly puts the 1-hour TTL
(`{"type": "ephemeral", "ttl": "1h"}`, a 2x write multiplier instead of 1.25x)
out of scope, in both `placement.py`'s module docstring and a README
footnote. Is that still accurate, what exactly changes in the request/response
shape to support it, and is it a small enough increment to build today?

## Why this topic

Every other "natural next increment" visible from the current lab state is
already spoken for:

- The backlog's Coding agents, Skills, and MCP sections have no plain `[ ]`
  item left — every entry is `[done #N]` or a `[stranded cycle/...]` claim.
  I checked each stranded claim's branch directly (`git diff
  $(git merge-base main <branch>) <branch>`): all four
  (`strict-tool-schemas`, `mcp-prompts`, `mcp-resources-claude-code`,
  `context-editing-clear-thinking-preview`) already carry a complete
  research note *and* a built, tested increment sitting unshipped from a
  session-limit-killed cycle (see `logs/last-health.md`'s many "session
  limit" entries from 2026-09-03 through 2026-09-14). Re-researching any of
  them would duplicate real, finished work that just needs a human/maintainer
  to resurrect the branch as a PR — not a job for a fresh research cycle.
- The one open PR touching this area, **#40** ("measure the context-editing
  vs prompt-caching trade"), is the `cycle/2026-09-07-context-editing-cache-tradeoff`
  branch. I diffed it too: it explicitly lists "the 1-hour TTL and its `2x`
  write multiplier" as out of scope in its own research note and code
  comments — so it does not cover this topic.
- `examples/skill-script-execution/README.md` names a second untaken
  candidate ("Manifest formats beyond `requirements.txt`/`package.json`").
  I considered it and it's a fine backup, but the TTL gap is the tighter,
  more self-contained increment (one file's wire shape, one pricing constant,
  one nested `usage` field to read) and has a sharper acceptance test (prove
  the server actually billed at 2x, not just that the request shape changed).

So per the researcher's own fallback rule ("if everything is claimed or
already covered by an open PR, pick the most valuable stale one and say so"),
this cycle targets a footnoted, explicitly-out-of-scope gap in an already-shipped
example rather than re-deriving work that already exists on ice.

## Findings

**Primary source, re-verified live today (2026-09-16):**
[Prompt caching docs](https://platform.claude.com/docs/en/build-with-claude/prompt-caching).
Nothing has changed since the 2026-08-29 verification already recorded in
`knowledge/prompt-caching.md` — this is a confirmation, not a correction:

- **Wire shape.** `ttl` is an optional string field inside `cache_control`,
  one of `"5m"` (the default when `ttl` is omitted — this is the form the
  existing example already sends) or `"1h"`:
  ```json
  { "cache_control": { "type": "ephemeral", "ttl": "1h" } }
  ```
  No beta header, no `client.beta` namespace — this has been GA since before
  the 2026-08-29 note. I did not find the explicit `"ttl": "5m"` form used
  anywhere in the docs' own examples; only *omitting* `ttl` is shown for the
  5-minute case, so the safe move is to keep the existing marker
  (`{"type": "ephemeral"}`) as the 5-minute wire shape rather than introduce
  an unverified explicit-`"5m"` form.
- **Pricing**, unchanged from the knowledge note: 5-minute write **1.25x**
  base input rate, 1-hour write **2x**, read **0.10x** either TTL (Fable 5.1
  / Mythos 5.1 use a different 0.025x read rate — irrelevant here, this
  example runs `claude-sonnet-5`).
- **Response shape.** `usage.cache_creation` is a nested object:
  `{"ephemeral_5m_input_tokens": N, "ephemeral_1h_input_tokens": M}`, and the
  flat `cache_creation_input_tokens` field already on `TurnUsage` "equals the
  sum of the values in the `cache_creation` object" (docs, verbatim). That
  identity is the key fact for scoping the build: **the dollar math does not
  require reading the nested object at all** — for a run that requests one
  TTL throughout, the existing flat counter times the right multiplier is
  already correct. The nested object's only job here is *proof*: reading it
  back lets the increment assert the tokens actually landed under
  `ephemeral_1h_input_tokens` (and `ephemeral_5m_input_tokens == 0`) rather
  than trusting that sending `ttl: "1h"` in the request did what it says.
- **Cap unchanged.** Still 4 `cache_control` breakpoints per request, tools +
  system + messages combined — confirmed again today, matches
  `placement.MAX_BREAKPOINTS`.
- SDK-level field names, corroborated by a second, independent source
  (2026-09-16 search) rather than docs alone: `response.usage.cache_creation`
  is a nested model with `.ephemeral_5m_input_tokens` /
  `.ephemeral_1h_input_tokens` attributes on `anthropic-sdk-python`'s
  `Usage` type. PyPI shows the SDK at **1.5.0** as of 2026-09-10
  ([newreleases.io](https://newreleases.io/project/pypi/anthropic/release/1.4.0));
  the example pins `anthropic==1.2.0`. The nested `cache_creation` field is
  additive API surface, not a breaking change, so 1.2.0 likely already
  exposes it — but the builder should confirm directly against the installed
  SDK's `anthropic.types.Usage` (e.g. `python3 -c "import anthropic,
  inspect; print(inspect.signature(anthropic.types.Usage))"`) before writing
  code that reads `.cache_creation.ephemeral_1h_input_tokens`, rather than
  trusting this note.

**Practitioner reality check** (the discipline the task calls for beyond
vendor docs): [anthropics/claude-code#46829](https://github.com/anthropics/claude-code/issues/46829),
filed 2026-04-12, closed as "not planned." A user analyzed 119,866 real API
calls across two machines and found Claude Code's own sessions used
1-hour-TTL cache writes almost exclusively from Feb 1 to Mar 5, 2026, then
reverted to mostly-5-minute writes from Mar 8 onward — a change Anthropic
never announced, costing that user an estimated 17% cache-write overpayment
across three months. This is about **Claude Code CLI's own internal default**
for its own session caching, not the public `cache_control.ttl` request
parameter this increment touches — the docs' opt-in `ttl: "1h"` string form
verified above is unrelated and, per the docs, unchanged. But it's a directly
relevant caveat for the README: don't assume *any* client (including
Anthropic's own) defaults to the longer TTL for you — you have to set it
explicitly, and even doing so isn't something to take on faith; that's
exactly why this increment's acceptance criterion is "prove the server billed
at 2x," not "prove the request was well-formed." A second source
([dev.to, 2026-04-16](https://dev.to/whoffagents/anthropic-silently-dropped-prompt-cache-ttl-from-1-hour-to-5-minutes-16ao))
corroborates the same regression with the same date range; I'm treating it as
secondary corroboration of the primary GitHub issue, not as its own source of
wire-format facts (its own code sample uses `"ttl": 3600`, an integer/seconds
form I could not find anywhere in current primary docs — likely the author's
own error, not a real accepted form; the docs and a second independent
WebFetch of the same docs page both show only the string `"1h"`/`"5m"` forms).

## Build proposal

Extend `examples/prompt-caching-tool-loop/` — no new `examples/` directory
(checked: no open PR or `cycle/*` branch touches this file set; see "Why this
topic" above). This is additive to the shipped, tested example, not a rewrite:
existing behavior must be reachable unchanged by not passing the new
parameter.

### Intent

Give `place_breakpoints` a TTL choice and let `report.py` price whichever one
was actually used, so the same two-turn demo can show the two real tradeoffs
side by side: cheap-but-fragile (5-minute, 1.25x, evicted by any >5-minute
pause) vs. expensive-but-durable (1-hour, 2x, survives a longer idle gap).
Explicitly out of scope: mixing TTLs within a single request (the docs
describe this as possible — different breakpoints at different TTLs — but
it's a second degree of freedom this increment doesn't need); automatic
caching's TTL behavior; any interaction with `clear_tool_uses_20250919`
(that's PR #40's territory, deliberately not touched here); changing the
default (5-minute stays the default in every existing call site).

### Behavioral spec

**`placement.py`**

- `place_breakpoints(messages, *, budget=MAX_BREAKPOINTS, ttl=CACHE_TTL_5M)`
  gains one new keyword-only parameter, `ttl`, one of two named constants:
  `CACHE_TTL_5M = "5m"` (default) or `CACHE_TTL_1H = "1h"`.
- Invariant: `ttl=CACHE_TTL_5M` (the default, and the only value every
  existing caller passes implicitly) produces a **byte-identical** marker to
  today — `{"type": "ephemeral"}`, no `"ttl"` key at all. All 23 existing
  `test_placement.py` assertions must keep passing unmodified against the new
  signature.
- `ttl=CACHE_TTL_1H` produces `{"type": "ephemeral", "ttl": "1h"}` on every
  block this call marks (rolling and, if present, anchor — both breakpoints
  in one request share one TTL; mixing is out of scope per Intent).
- Failure mode: any `ttl` value other than the two named constants raises
  `ValueError`, validated at the boundary alongside the existing budget check
  — before anything is copied, same pattern `_validate_budget` already
  follows.

**`report.py`**

- `Saving` gains a fourth field, `write_multiplier: float =
  CACHE_WRITE_5M_MULTIPLIER`, replacing the current hardcoded use of the
  module constant inside `write_premium_usd`. This keeps the dollar math
  correct for either TTL from one code path instead of forking `Saving` in
  two, and existing callers that never pass `write_multiplier` get the exact
  behavior they have today (1.25x).
- New module constant `CACHE_WRITE_1H_MULTIPLIER = 2.0`, named once, no
  hardcoded `2` anywhere else.
- `TurnUsage` gains two new optional fields, `ephemeral_5m_input_tokens:
  int | None = None` and `ephemeral_1h_input_tokens: int | None = None`.
  Invariant, checked in `__post_init__`: if both are given (not `None`),
  they must sum to exactly `cache_creation_input_tokens` — a `TurnUsage`
  that fails that identity should be unconstructable, not silently wrong.
  If either is `None` (the existing 17 tests' call sites, which predate this
  field and never pass it), skip the check — backward compatible.
- `summarize()` keeps its current two-argument shape but gains a keyword-only
  `write_multiplier: float = CACHE_WRITE_5M_MULTIPLIER` passed straight into
  the constructed `Saving`.

**`main.py`**

- New CLI flag or env var selecting `ttl` (`"5m"` default, `"1h"` opt-in),
  passed through to `place_breakpoints` and used to pick
  `CACHE_WRITE_1H_MULTIPLIER` vs. `CACHE_WRITE_5M_MULTIPLIER` for
  `summarize()`.
- New failure mode, in the same style as the existing `CacheMiss` /
  `EXIT_NO_CACHE_HIT` guard: when `ttl="1h"` was requested, assert
  `usage.cache_creation.ephemeral_1h_input_tokens == usage.cache_creation_input_tokens`
  and `ephemeral_5m_input_tokens == 0` on turn 1's response — proof the
  server actually wrote at the requested TTL, not just that the request was
  shaped correctly. Raise a named exception (e.g. `TTLMismatch`) and a new
  exit code on failure, exactly the "prove it, don't assume it" discipline
  the existing `CacheMiss` check already uses one level up.

### Acceptance criteria ("it works")

1. All 23 `test_placement.py` and 17 `test_report.py` assertions pass
   unmodified — regression safety for the default path.
2. New offline assertions (no key, no network), added to the same two test
   files:
   - `ttl=CACHE_TTL_1H` produces `{"type": "ephemeral", "ttl": "1h"}` on the
     marked block(s); `ttl=CACHE_TTL_5M` (and the omitted-default case)
     produce exactly `{"type": "ephemeral"}`, no `"ttl"` key.
   - An invalid `ttl` string raises `ValueError` before any copying.
   - A `Saving` built with `write_multiplier=CACHE_WRITE_1H_MULTIPLIER` on a
     hand-picked token count reports a write premium of **100%** of the base
     rate (not 25%), asserted against a computed dollar figure, not just "no
     exception."
   - A `TurnUsage` whose `ephemeral_5m_input_tokens` +
     `ephemeral_1h_input_tokens` does not sum to `cache_creation_input_tokens`
     raises; one that sums correctly, or that passes neither field, does not.
3. One cheap live run with the new flag (documented as costing money, same
   disclaimer style as the existing "Run it live" section): a real,
   dated transcript in the README showing turn 1's `ephemeral_1h_input_tokens`
   carrying the write, `ephemeral_5m_input_tokens == 0`, and the printed
   report computing the write premium at 2x — placed next to the existing
   2026-08-31 5-minute transcript so a reader sees both real dollar figures
   side by side, not just the two multipliers asserted in isolation.
4. README gets a new section (existing sections left alone) stating the
   actual break-even: at 2x write / 0.10x read, the 1-hour TTL earns back its
   extra premium after more reads than the 5-minute TTL needs — compute and
   state the exact number from the constants (not hand-waved), and name the
   situation where it's worth it (idle gaps between turns longer than 5
   minutes, e.g. a human-in-the-loop step) versus not (a tight, fast loop
   where the 5-minute window never expires anyway).

## Open questions

- Whether `anthropic==1.2.0` (the current pin) exposes `usage.cache_creation`
  with the exact attribute names above, or whether the pin needs bumping —
  not verified against the installed package in this research pass; the
  builder should check directly (`python3 -c "import anthropic, inspect;
  print(inspect.signature(anthropic.types.Usage))"` or equivalent) before
  writing code that reads the nested object, rather than trusting the SDK
  search result cited above.
- Whether the docs' claim that different breakpoints in one request can carry
  different TTLs is actually true in practice (not exercised here — mixing
  is explicitly out of scope for this increment) — if a future cycle wants
  it, confirm live rather than from the docs' prose alone.
- The `anthropics/claude-code#46829` regression (Claude Code's own default
  silently reverting from 1h to 5m business logic) was closed "not planned"
  by Anthropic with no public explanation found. Worth a `knowledge/` note if
  a future cycle investigates Claude Code's own caching behavior directly —
  out of scope for this note, which only touches the raw Messages API.
