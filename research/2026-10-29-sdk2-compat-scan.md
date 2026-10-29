# Anthropic API 2026-10-21 / Python SDK 2.0: a static compat scan for the lab

Date: 2026-10-29. Mode: demo. The feature sections of `BACKLOG.md` had no plain
`[ ]` item (only health findings and stranded claims), and no open PR covers this
(only #62, pipeline work). Topic filed by the researcher, as on 2026-10-27, and
chosen because it is a fresh (8-day-old) breaking release the lab's own pins are
exposed to. hn-search MCP was down; WebSearch/WebFetch only.

## Question
What exactly broke in API version `2026-10-21` / `anthropic` Python 2.x, and can a
small offline static scanner tell a project (starting with this lab) whether it is
exposed?

## Findings
Primary source: [Migrating to API version 2026-10-21](https://platform.claude.com/docs/en/api/migration-2026-10-21)
(official, fetched 2026-10-29, current). Secondary blogs (munderdiffl.in, dev.to/tokely,
cellcog.ai, 2026-10-21..23) agree with it; ignore their extra claims (e.g. Python
dropping Text Completions) unless the official page says so. PyPI (`pip index
versions anthropic`, 2026-10-29): latest **2.3.0**; 2.0.0 is the first 2.x.

- **Version coupling.** An SDK sends the API version current when its major was
  cut. Python `anthropic` **2.0.0** is the first to send `anthropic-version:
  2026-10-21` (previous: `2023-06-01`, unchanged for ~3 years). Raw-HTTP callers
  set the header themselves.
- **Wire-level breaking changes (Messages, `count_tokens`, each Batch request):**
  1. `temperature`, `top_p`, `top_k` -> **400 even at default values**. SDK 2.x
     removed the kwargs entirely. Models 4.7+ already ignored non-defaults, so
     only older models (e.g. Sonnet 4.6) lose behavior. Escape hatch: keep that
     request on `2023-06-01`.
  2. `document` block directly in an `assistant` message -> 400 (nested inside a
     tool result is still fine).
  3. non-empty `citations` array on a `text` block in a `user` message (incl.
     inside `search_result`) -> 400. `citations: {"enabled": true}` on documents
     and citations on *assistant* text blocks are still accepted.
- **Python SDK 2.0 changes:**
  - `MessageParam` / `BetaMessageParam` become unions of per-role types
    (`UserMessageParam`, `AssistantMessageParam`, `SystemMessageParam`). Dict
    literals and annotations like `list[MessageParam]` work; **calling or
    subclassing the name raises `TypeError`**; a dict with a variable `role` fails
    type checking only.
  - `client.models.list/retrieve(betas=...)` -> `TypeError`; use `client.beta.models`.
  - **API key now sent as `Authorization: Bearer`**, not `X-Api-Key` (applies to
    `Anthropic`/`AsyncAnthropic` `api_key` and `ANTHROPIC_API_KEY`;
    `AnthropicFoundry` still `x-api-key`). Proxies/middleware reading `X-Api-Key`
    break; workaround is `default_headers={"X-Api-Key": key}` yourself.
  - **One credential per client**: passing two of `api_key`/`auth_token`/
    `credentials`/`config`/`profile` to the constructor or one `copy()`/
    `with_options()` raises `ValueError` (v1 sent both headers). With none passed,
    env precedence: `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_PROFILE`,
    WIF vars, active profile on disk; both of the first two set = one-time warning.
  - Not Python: Text Completions removal (TS/Go/Java/Ruby/CLI), client-side
    compaction removal from tool runner (TS/Ruby; use server-side
    `compact_20260112`, see [[compaction]]), Go `/v2` import path, Java 3.0.0.
- **Bedrock:** `2026-10-21` only for Claude 4.7+; older models stay on old SDK.
- **Lab exposure (checked 2026-10-29 by grep over `examples/` and `projects/`):**
  no `temperature`/`top_p`/`top_k`, `MessageParam(`, `.completions`, `x-api-key` or
  `auth_token` in any `*.py`, so runtime breakage is unlikely. But of 16
  `requirements.txt` files, **9 are open-ended** (`anthropic>=0.120.0` or
  `>=0.120.2`: minimal-agent-loop, streaming-tool-loop, tool-error-policy,
  typed-tool-registry, orchestrator-subagents, memory-tool-handler,
  skill-script-execution-word-counter, skill-reference-files-bigquery, and
  strict-tool-schemas `>=1.3.0`), so a fresh install now resolves 2.3.0 while
  never having been tested on it. Three are capped `<2`
  (`batch-eval-join`, `skill-api-container`, `token-budget-guard`) and four are exact pins
  (`==0.121.0` x2, `1.2.0`, `1.4.0`). The cap-vs-open split is exactly what a scan
  should surface. Whether the lab's offline tests pass on 2.3.0 is **unverified**.

## Build proposal
`examples/sdk2-compat-scan/` : a stdlib-only static scanner that reports which
2026-10-21 / SDK-2 breaking changes a Python tree is exposed to.

Intent: give a project (first target: this repo) a fast, offline, no-key answer to
"will upgrading to `anthropic` 2.x or API `2026-10-21` break us?" Out of scope: auto-fixing,
non-Python languages, type-checker-only findings (variable-role dicts), Bedrock/Vertex
clients, running the examples under 2.x, any network or API call.

Files (each one concern):
- `rules.py`: pure. `Finding(rule_id, path, line, message)` frozen dataclass; `scan_source(path: str, source: str) -> list[Finding]` using `ast`; `scan_requirement(path: str, line_no: int, line: str) -> Finding | None`. No I/O.
- `scan.py`: shell. Walks a root (`*.py`, `requirements*.txt`), reads files, prints findings, exit 1 if any, 2 on usage/unreadable-root, `--json` optional. Skips `.venv`, `node_modules`, `__pycache__`.
- `test_rules.py`: acceptance tests on inline source strings, plus one run of `scan.py` over a tmp tree. `README.md` with a transcript of scanning the lab (`../`).

Rules (id: trigger):
- `SAMPLING-PARAM`: keyword arg `temperature`/`top_p`/`top_k` in a call whose attribute chain ends `.create`, `.stream`, `.parse`, `.count_tokens`, `.tool_runner`, or a `dict` literal with those keys sitting beside `"max_tokens"`/`"messages"` (batch request params). Severity error.
- `MESSAGEPARAM-CALL`: `MessageParam(...)`/`BetaMessageParam(...)` called, or used as a class base. Error.
- `MODELS-BETAS`: `.models.list/retrieve(... betas=...)` not through `.beta.`. Error.
- `DUAL-CREDENTIAL`: `Anthropic/AsyncAnthropic(...)`, `.copy`, `.with_options` with two or more of `api_key/auth_token/credentials/config/profile` non-`None`. Error.
- `XAPIKEY-HEADER`: string literal `x-api-key` (case-insensitive) anywhere. Warn (proxy/middleware may break).
- `DOC-IN-ASSISTANT` / `CITATIONS-IN-USER`: dict literal `{"role": "assistant", ... {"type": "document"}}` and `{"role": "user", ... "citations": [non-empty]}`. Warn (heuristic, literals only).
- `UNBOUNDED-PIN` (requirements): `anthropic` with no upper bound `<`/`==`/`~=` that excludes 2.x. Warn: "resolves to 2.x untested".

Acceptance criteria (checkable):
1. `scan_source` on `client.messages.create(model=M, max_tokens=1, temperature=0, messages=[])` returns exactly one `SAMPLING-PARAM` with the right line; same call without `temperature` returns `[]`; `temperature=1.0` (the default) is still flagged.
2. `MessageParam(role="user", content="x")` -> `MESSAGEPARAM-CALL`; the annotation `x: list[MessageParam] = []` and dict literal `{"role":"user","content":"x"}` -> no finding (false-positive guard).
3. `Anthropic(api_key=k, auth_token=t)` -> `DUAL-CREDENTIAL`; `Anthropic(api_key=k, auth_token=None)` -> none.
4. `client.models.list(betas=[b])` -> `MODELS-BETAS`; `client.beta.models.list(betas=[b])` -> none.
5. `scan_requirement`: `anthropic>=0.120.0` and `anthropic` -> `UNBOUNDED-PIN`; `anthropic>=1.15.0,<2`, `anthropic==1.4.0`, `anthropic~=1.4` -> none; `anthropic-foo>=1` and comments -> none.
6. A file with a syntax error yields a `PARSE-ERROR` finding (not a crash, not silent skip); an unreadable root exits 2.
7. Running `python scan.py ../` over the lab exits 0 or 1 and its README transcript lists the unbounded-pin files, matching a re-run exactly (the repo's transcript-drift checker applies).
8. Test suite runs with `python -I test_rules.py`, no key, no network, no third-party packages.

Failure modes to state in docstrings: heuristic rules can miss calls through
aliases/`**kwargs` (documented limitation, not silently "clean"); the scanner never
says "safe", only "no known pattern found".

## Open questions
- Python 2.x behavior for Text Completions: only TS/Go/Java/Ruby/CLI confirmed removed.
- Whether `anthropic` 2.x still accepts `tool_runner` kwargs the lab uses; not tested here.
- Whether the lab's offline tests pass on 2.3.0 (a natural follow-up cycle: run each
  example's self-test in a clean venv on 2.3.0 and record results).
- The migration page's "Admin API" and `workspace_id` sections were not read; irrelevant to this scan.
