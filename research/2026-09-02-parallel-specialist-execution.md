# Parallel specialist execution in the orchestrator

## Question

What is the smallest real change that makes `examples/orchestrator-subagents/`
dispatch its independent `Subtask`s concurrently instead of one after another —
keeping `plan_task` and `synthesize` untouched, results reassembled in plan
order, and the whole thing still offline-testable without a key?

## Findings

### The current example is strictly sequential, and its README already names this the next step

`examples/orchestrator-subagents/agent.py` (read 2026-09-02):

- `run_orchestrator(client, task)` does `plan_task` → a list comprehension
  `[(subtask, run_specialist(client, subtask)) for subtask in plan.subtasks]`
  → `synthesize`. The delegate step is a plain Python loop: N specialist calls
  in series.
- `run_specialist`, `plan_task`, `synthesize` are all synchronous and each
  makes exactly one `client.messages.*` call. No shared state between
  specialist calls — each has its own `system` persona and its own one-message
  history. That independence is what makes them safe to run in parallel.
- The README's "Explicitly out of scope for today" section says: *"Parallel
  specialist execution. `run_orchestrator` calls each specialist sequentially,
  not via `ThreadPoolExecutor`/`asyncio.gather` — kept simple for determinism
  and a straightforward offline test. Natural next increment."*
- `test_agent.py::test_run_orchestrator_makes_exactly_four_calls_in_order`
  unpacks `client.messages.create_calls` positionally
  (`specialist_call_1, specialist_call_2, synthesis_call = ...`) and asserts
  `"research" in specialist_call_1["system"]`. **This positional assertion
  only holds for a sequential implementation** — a thread pool appends to
  `create_calls` in completion order, which is non-deterministic. So the
  parallel path cannot reuse that test, and `run_orchestrator` itself should
  not change (expand/contract, Protocol §5).

### ThreadPoolExecutor vs asyncio.gather — the thread pool is the smaller change

The backlog item allows either. The existing module is entirely synchronous
(`agent.py`'s `main()`, all three helpers, and the `_FakeClient` in
`test_agent.py` with sync `.parse`/`.create`). Going async would force
`AsyncAnthropic`, `async def` on the helpers or a wrapper, and an async fake
client — a much larger diff for no benefit at N ≤ 3 calls.

`concurrent.futures.ThreadPoolExecutor` keeps every existing function
byte-for-byte. The key primitive:

- **`executor.map(fn, iterable)` yields results in *input* order**, blocking as
  needed, regardless of completion order (Python stdlib docs,
  <https://docs.python.org/3/library/concurrent.futures.html#concurrent.futures.Executor.map>).
  That is exactly "results reassembled in plan order rather than completion
  order" — no manual `future → index` bookkeeping.
- **`map` re-raises a worker exception when you iterate to that position.** So
  one failing specialist makes the whole fan-out fail loudly at that slot
  rather than returning a partial list — matches Protocol §4 (fail fast, don't
  return a default in place of an error).

Both `concurrent.futures` and `threading` are standard library, so
`requirements.txt` does not change.

### Is the synchronous `Anthropic` client safe to share across a thread pool?

Anthropic Python SDK docs
([Python SDK](https://platform.claude.com/docs/en/api/sdks/python), fetched
2026-09-02):

- The sync client wraps **one** HTTP client (the SDK moved from `httpx` to
  `httpx2`, "an API-compatible fork", in the 1.x line). `DefaultHttpxClient`
  "ensure[s] the SDK's default configuration (such as timeouts and
  **connection limits**) is preserved."
- httpx / httpx2 default pool `max_connections` is **100**
  ([httpx resource limits](https://www.python-httpx.org/advanced/resource-limits/),
  fetched 2026-09-02) — three concurrent specialist calls are nowhere near it.
- The docs describe `copy()` / `with_options()` as enabling "per-request
  configuration without mutating shared client state, supporting thread-safe
  usage patterns"
  ([Client Architecture, DeepWiki](https://deepwiki.com/anthropics/anthropic-sdk-python/4-client-architecture),
  fetched 2026-09-02).
- **Automatic retries are per-call and safe under concurrency:** "Certain
  errors are automatically retried 2 times by default, with a short
  exponential backoff. Connection errors, 408, 409, **429 Rate Limit**, and
  >=500 ... are all retried by default." 429 surfaces as
  `anthropic.RateLimitError` if retries are exhausted. Default timeout is 10
  minutes.

There is **no single sentence in the docs that says "the sync client is
thread-safe"** (noted in Open questions). But it wraps one pooled HTTP client
sized for 100 connections, `httpx.Client` is documented safe for issuing
requests from multiple threads, and the SDK's own async utilities
(`to_thread`, `asyncify`) exist precisely to run the sync client in a thread
pool. The `ThreadPoolExecutor`-over-one-client pattern is the common way to do
this. The offline test never touches a real client; the live `main()` run is
the empirical check.

### The prompt-caching gotcha the README should mention

The specialist calls in this example deliberately **share no prefix** — each
has a distinct `system` string and distinct `messages` — so prompt caching is
not in play here at all. But the moment a fan-out's calls *do* share a large
common prefix (same big system prompt, same tools, same shared context block),
dispatching them concurrently is a cache anti-pattern.

Anthropic's
[prompt caching docs](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
(fetched 2026-09-02) state it directly:

> "For concurrent requests, note that a cache entry only becomes available
> after the first response begins. If you need cache hits for parallel
> requests, wait for the first response before sending subsequent requests."

So N parallel calls sharing a prefix, fired before any returns, produce **N
cache writes and zero reads** — each write billed at 1.25× base input
(5-minute TTL). Serialized, you would get 1 write + (N−1) reads at 0.10× each.
`knowledge/prompt-caching.md` lists cache-killers but not this one; it belongs
there and in the README as a one-paragraph caveat (the delegate step here is
safe *because* it shares nothing, and that is worth saying explicitly).

### Practitioner context (not load-bearing, but useful framing)

Anthropic's own multi-agent Research system is orchestrator-workers with
"3–5 specialized subagents in parallel" and a separate synthesis pass, and it
costs ~15× the tokens of a single chat
([How we built our multi-agent research system](https://www.anthropic.com/engineering/built-multi-agent-research-system),
via secondary summaries fetched 2026-09-02). The lesson that transfers to this
tiny example: **parallelism cuts wall-clock latency, not token cost** — the
fan-out still makes the same plan + N specialist + synthesize calls. The README
should not oversell it.

## Build proposal

### Layer 1 — Intent

Add a concurrent variant of the delegate step to
`examples/orchestrator-subagents/` so the N independent specialist calls run at
once instead of in series, cutting wall-clock latency while returning the
identical result. **Out of scope:** changing `run_orchestrator` (stays the
sequential baseline), `plan_task`, `synthesize`, or the `Plan`/`Subtask`
schema; async / `AsyncAnthropic`; `asyncio`; any cross-specialist
communication; retry/rate-limit policy beyond the SDK's built-in default;
making the parallel variant the default (a later expand/contract cycle can do
that).

### Layer 2 — Behavioral spec

New surface in `agent.py`:

**`run_specialists_parallel(client, subtasks, *, max_workers) -> list[str]`**

- **Input:** a `client` exposing `.messages.create`, a non-empty
  `list[Subtask]`, and a keyword-only `max_workers: int`.
- **Output:** a `list[str]` of specialist text outputs, **index-aligned with
  `subtasks`** (element `i` is the output for `subtasks[i]`), regardless of
  which call finished first.
- **Invariants:** exactly one `run_specialist` call per subtask; the number
  and content of `client.messages.create` calls is identical to the sequential
  path; the returned list length equals `len(subtasks)`.
- **Failure modes:**
  - `max_workers < 1` → `ValueError`, raised before any API call is made
    (boundary validation, Protocol §4).
  - any `run_specialist` raising (e.g. `anthropic.APIStatusError`,
    `RateLimitError` after the SDK's retries) → that exception propagates out
    of `run_specialists_parallel`; the function does **not** return a partial
    list and does **not** swallow it.
  - `subtasks == []` → returns `[]` without creating an executor (the `Plan`
    schema already forbids this upstream; still defined here).

**`run_orchestrator_parallel(client, task) -> str`**

- Same input/output contract as the existing `run_orchestrator`.
- Runs `plan_task` → `run_specialists_parallel(client, plan.subtasks,
  max_workers=len(plan.subtasks))` → `synthesize`, in that order.
- `synthesize` receives `list(zip(plan.subtasks, outputs))` — pairs in plan
  order.
- **Failure modes:** propagates `plan_task`'s `RuntimeError` (unchanged);
  propagates any specialist exception from `run_specialists_parallel`.

Unchanged: `run_orchestrator`, `plan_task`, `run_specialist`, `synthesize`,
`Plan`, `Subtask`, `MODEL`, `requirements.txt`.

`main()` change (minimal): after printing the plan, replace the sequential
`for subtask in plan.subtasks` delegate loop with a single
`run_specialists_parallel(...)` call wrapped in `time.perf_counter()`, and
print the elapsed seconds for the fan-out step before the per-specialist
outputs. Everything else in `main()` stays.

Module constant (Protocol §2, no magic values):
`MAX_PARALLEL_SPECIALISTS = 4` — the `Plan` schema caps `subtasks` at 3, so
this is headroom; `run_orchestrator_parallel` passes
`max_workers=len(plan.subtasks)` directly and never exceeds it, but the
constant documents the intended ceiling and can bound `max_workers` if a
caller passes a longer list.

### Layer 3 — Interfaces (no bodies)

```python
# examples/orchestrator-subagents/agent.py  — additions only

import time
from concurrent.futures import ThreadPoolExecutor

MAX_PARALLEL_SPECIALISTS: int = 4  # Plan caps subtasks at 3; headroom of 1.


def run_specialists_parallel(
    client,
    subtasks: list[Subtask],
    *,
    max_workers: int,
) -> list[str]:
    """Run one `run_specialist` call per subtask across a thread pool and
    return their text outputs in subtask (plan) order, not completion order.

    Uses `ThreadPoolExecutor.map`, which yields results in input order. The
    synchronous `client` is shared across worker threads (see README /
    knowledge/orchestrator-workers.md for why that is safe here).

    Failure modes:
    - `max_workers < 1` -> `ValueError`, before any API call is made.
    - Any `run_specialist` call raising -> that exception propagates out of
      this function (fail fast); partial results are never returned.
    - `subtasks == []` -> returns `[]` (no executor created).
    """


def run_orchestrator_parallel(client, task: str) -> str:
    """`plan_task` -> `run_specialists_parallel` (max_workers =
    len(plan.subtasks)) -> `synthesize`. Same return contract as
    `run_orchestrator`; only the delegate step differs. `plan_task` and
    `synthesize` are called unchanged.
    """
```

### Layer 3 — Test interfaces

`test_agent.py` additions. The existing `_FakeClient` / `_FakeMessages` records
`create_calls` via a bare `list.append`; that is GIL-atomic so it will not
corrupt, but its **order is not meaningful** under a thread pool — new tests
assert on call *count* and *contents*, never position. New tests use a
barrier/event-aware fake (a small subclass or a sibling fake) so overlap is
provable deterministically offline.

- `test_specialists_run_concurrently_not_serially` — fake `.messages.create`
  waits on `threading.Barrier(3, timeout=5)` before returning its scripted
  block. `run_specialists_parallel(client, [3 subtasks], max_workers=3)`
  returns all three scripted outputs. A sequential implementation would leave
  the barrier with one party and raise `BrokenBarrierError` inside the 5s
  timeout → test fails loudly. (Stronger optional assertion: record
  `perf_counter` on entry/exit of each fake call and assert
  `max(entries) < min(exits)`.)
- `test_results_are_in_plan_order_regardless_of_finish_order` — fake scripted
  so `subtasks[2]`'s call returns before `subtasks[0]`'s (via two
  `threading.Event`s). Assert `run_specialists_parallel(...) ==
  ["out-0", "out-1", "out-2"]` exactly, and that
  `run_orchestrator_parallel`'s `synthesize` prompt lists the outputs in plan
  order.
- `test_run_orchestrator_parallel_call_accounting` — fake scripted for a
  3-item plan: assert exactly 1 `parse` call and 4 `create` calls (3
  specialist + 1 synthesis), the set of specialist `system` strings covers all
  three specialists, and the return value is the scripted synthesis text.
- `test_specialists_parallel_rejects_bad_max_workers` —
  `run_specialists_parallel(client, [1 subtask], max_workers=0)` raises
  `ValueError` and makes zero `create` calls.
- `test_specialists_parallel_propagates_worker_error` — fake scripted so
  `subtasks[1]`'s call raises `RuntimeError("boom")`;
  `run_specialists_parallel` re-raises it (assert `RuntimeError` reaches the
  caller) rather than returning a 3-element list.

The existing 7 tests are untouched and must still pass (they cover the
unchanged sequential path and schema).

### Acceptance criteria — "it works"

1. `cd examples/orchestrator-subagents && pip install -r requirements.txt &&
   python test_agent.py` — no key, no network — prints every existing `ok`
   line **plus** the 5 new ones, ending `All 12 self-tests passed.` (count in
   `main()`'s list and in the README's expected-output block both updated).
2. `test_specialists_run_concurrently_not_serially` fails (raises
   `BrokenBarrierError` within 5s) if `run_specialists_parallel` is swapped for
   a serial loop — i.e. the test genuinely proves overlap, not just "it ran".
3. `test_results_are_in_plan_order_regardless_of_finish_order` passes with the
   later subtask's call scripted to return first.
4. `run_orchestrator` and all 7 original tests are unchanged and pass.
5. `requirements.txt` is unchanged (stdlib only).
6. With `ANTHROPIC_API_KEY` set, `python agent.py` runs plan → 3 specialists
   concurrently → synthesize, prints the plan, the elapsed wall-clock for the
   fan-out step, each specialist's output, and the final synthesis. The
   fan-out wall-clock is visibly less than three serial calls would take
   (demonstrated in the printed number and the committed README transcript;
   not asserted by an automated test). Token cost is unchanged vs the
   sequential version.
7. README updated: "Parallel specialist execution" moved out of "out of
   scope"; a new "Sequential vs parallel fan-out" subsection explains
   `run_orchestrator` (baseline, deterministic, positional test) vs
   `run_orchestrator_parallel` (`ThreadPoolExecutor.map` preserves plan
   order); a "Prompt caching and parallel fan-out" paragraph states that these
   specialist calls share no prefix so caching is moot here, and that
   fanning out calls which *do* share a prefix pays N cache writes + 0 reads
   (quote + link the prompt-caching docs and `knowledge/prompt-caching.md`); a
   short note that the sync `Anthropic` client wraps one pooled HTTP client
   (100 connections) and is shared across the 3-worker pool by design.

### Where it goes

Extends the existing **`examples/orchestrator-subagents/`** (not a new dir) —
`agent.py`, `test_agent.py`, `README.md` change; `requirements.txt` does not.
Confirmed on `main` (2026-09-02): no other `examples/` dir is a better home,
no open PR (`gh pr list` shows only #36, unrelated) and no local/remote branch
touches this example.

## Open questions

- **No explicit "sync client is thread-safe" guarantee in the SDK docs.** The
  evidence (one pooled `httpx2` client, 100-connection default, `copy()`
  "thread-safe usage patterns" language, the SDK's own `to_thread`/`asyncify`
  helpers) strongly implies sharing it across a small thread pool is intended,
  and it is the common pattern — but Anthropic has not stated it in one
  sentence. The offline tests don't depend on this; the live `main()` run is
  the check. If a live run shows connection errors under 3-way concurrency,
  the fallback is one `ThreadPoolExecutor` worker per call with
  `client.with_options()` per thread, or `max_workers=1` (degenerates to
  sequential).
- **The ~40% back-to-back cache-miss race** reported in
  [anthropic-sdk-python#1451](https://github.com/anthropics/anthropic-sdk-python/issues/1451)
  (even *sequential* requests sometimes miss a cache the previous request just
  wrote; a 2s sleep fixes it). Not relevant to this build (no shared prefix,
  no caching) but worth a line in `knowledge/prompt-caching.md` since it
  compounds the concurrent-fan-out problem.
- Whether `ThreadPoolExecutor.map` should take a per-call `timeout`. Left out:
  the SDK already imposes a 10-minute per-request timeout and retries, and a
  second timeout layer is its own backlog-sized topic (cf.
  `examples/tool-error-policy/`).

## Sources

- `examples/orchestrator-subagents/{agent.py,test_agent.py,README.md,requirements.txt}` — read 2026-09-02
- `research/2026-07-29-orchestrator-subagents.md`; `knowledge/orchestrator-workers.md`; `knowledge/prompt-caching.md` — read 2026-09-02
- [Anthropic Python SDK docs](https://platform.claude.com/docs/en/api/sdks/python) — retries (2 by default; 429/5xx/408/409/conn), 10-min timeout, `httpx2`, `DefaultHttpxClient` connection limits, `AsyncAnthropic` — fetched 2026-09-02
- [Prompt caching docs](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) — "a cache entry only becomes available after the first response begins ... wait for the first response before sending subsequent requests"; 1.25× write / 0.10× read multipliers; 5-min TTL — fetched 2026-09-02
- [Python stdlib: `concurrent.futures.Executor.map`](https://docs.python.org/3/library/concurrent.futures.html#concurrent.futures.Executor.map) — yields results in input order, re-raises worker exceptions on iteration
- [httpx resource limits](https://www.python-httpx.org/advanced/resource-limits/) — default `max_connections=100` — fetched 2026-09-02
- [Client Architecture, DeepWiki (anthropic-sdk-python)](https://deepwiki.com/anthropics/anthropic-sdk-python/4-client-architecture) — `copy()`/`with_options()` "thread-safe usage patterns" — fetched 2026-09-02
- [anthropic-sdk-python#1451](https://github.com/anthropics/anthropic-sdk-python/issues/1451) — back-to-back cache-miss race — fetched 2026-09-02
- [PyPI: anthropic](https://pypi.org/project/anthropic/) — latest 1.3.0, Python 3.10+ — checked 2026-09-02
- [How we built our multi-agent research system](https://www.anthropic.com/engineering/built-multi-agent-research-system) — orchestrator-workers, 3–5 parallel subagents, ~15× token cost (via secondary summaries) — 2026-09-02
