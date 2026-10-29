# SDK 2.x / API `2026-10-21` compat scan (static, offline, stdlib only)

On 2026-10-21 Anthropic shipped API version `2026-10-21`, the first new
`anthropic-version` in about three years. Python `anthropic` **2.0.0** is the
first SDK release that sends it, and PyPI's latest is 2.3.0. Some code that
worked yesterday now fails: `temperature=` returns a 400 even at its default
value, `MessageParam(...)` raises `TypeError`, and two credentials on one client
raise `ValueError`. Any `anthropic>=0.120.0` requirement now installs 2.x,
which the code was never tested against.

This tool walks a Python tree and reports which of those documented breaking
changes it can see. It does not import, run, or call anything. It never says
"safe". The strongest thing it says is "no known pattern found".

From the research note:
[`research/2026-10-29-sdk2-compat-scan.md`](../../research/2026-10-29-sdk2-compat-scan.md)
(primary source: Anthropic's
[Migrating to API version 2026-10-21](https://platform.claude.com/docs/en/api/migration-2026-10-21)).

## What's here

| File | One job |
|------|---------|
| `rules.py` | Turns source text into findings. Pure: no filesystem, environment, or clock. `scan_source(path, source) -> SourceReport`, `scan_requirement(path, line_no, line) -> Finding \| None`. |
| `scan.py` | The shell. It walks a root, reads files, prints findings as text or `--json`, and sets the exit code. |
| `test_rules.py` | Offline self-test. One test per acceptance criterion (AC1-AC7), plus the message-shape and pragma rules. |

There is no `requirements.txt`. The tool uses only the standard library (`ast`,
`tokenize`, `re`), so you can run it in a project without installing anything
into that project.

## Rules

| Id | Severity | Fires on |
|----|----------|----------|
| `SAMPLING-PARAM` | error | `temperature=`/`top_p=`/`top_k=` on `<x>.messages.{create,stream,parse,count_tokens,tool_runner}`, or a dict literal with one of those keys next to `"max_tokens"`/`"messages"` (batch request params) |
| `MESSAGEPARAM-CALL` | error | `MessageParam(...)` / `BetaMessageParam(...)` called, or used as a base class. Annotations and dict literals are fine |
| `MODELS-BETAS` | error | `.models.list/retrieve(betas=...)` not called through `.beta.` |
| `DUAL-CREDENTIAL` | error | `Anthropic`/`AsyncAnthropic(...)`, `.copy(...)`, `.with_options(...)` given two or more non-`None` values among `api_key`, `auth_token`, `credentials`, `config`, `profile` |
| `XAPIKEY-HEADER` | warn | any string literal equal to `x-api-key` (case-insensitive). SDK 2.x sends `Authorization: Bearer`, so proxies that read `X-Api-Key` break |
| `DOC-IN-ASSISTANT` | warn | a `{"type": "document"}` block directly in a `{"role": "assistant"}` message literal |
| `CITATIONS-IN-USER` | warn | a text block with a non-empty `citations` list, in a `{"role": "user"}` message literal, either directly or inside a `search_result` |
| `UNBOUNDED-PIN` | warn | an `anthropic` line in `requirements*.txt` with no upper-bounding specifier (`<`, `<=`, `==`, `~=`, `===`) |
| `PARSE-ERROR` | error | a `.py` file that doesn't parse or isn't UTF-8, an `anthropic` requirement that can't be parsed (or is a `name @ url` direct reference), or a pragma that names an unknown rule |

The scanner exits 0 when it finds nothing, 1 when it finds anything (warnings
included), and 2 on bad usage or an unreadable root, file, or directory. It
skips `.git`, `.venv`, `venv`, `node_modules`, and `__pycache__`.

## Run the self-test (no API key, no network, no dependencies)

```bash
cd examples/sdk2-compat-scan
python3 test_rules.py
```

Expected output:

```
ok  AC1 sampling params flagged on messages.* and request dicts, even at default
ok  AC2 MessageParam called or subclassed is flagged; annotations and dicts are not
ok  AC3 two non-None credentials on a client or with_options are flagged
ok  AC4 models.list/retrieve(betas=) flagged unless called through client.beta
ok  AC5 unbounded anthropic pins flagged; capped, exact, other packages are not
ok  AC6 a syntax error is a PARSE-ERROR finding; an unreadable root exits 2
ok  AC7 scan.py over a tmp tree: right lines, skips .venv, exit 1 then 0 once fixed
ok  document-in-assistant and citations-in-user flagged only in the documented shapes
ok  x-api-key literal flagged; pragmas suppress only their rule and typos are loud

All 9 self-tests passed.
```

`python3 -I test_rules.py` (isolated mode) produces the same output. Both entry
points put their own directory on `sys.path` explicitly. AC7 also runs
`scan.py` under `-I`.

## Scan this lab

```bash
cd examples/sdk2-compat-scan
python3 scan.py ../            # every example
python3 scan.py ../ --json     # same, machine-readable
```

Output on 2026-10-29 (exit 1). This output changes whenever an example's pins
change, so it is a dated record and the transcript checker does not check it:

```
memory-tool-handler/requirements.txt:4: warn  UNBOUNDED-PIN  anthropic >=0.120.0 has no upper bound: a fresh install resolves to SDK 2.x (API 2026-10-21), which this code was not tested on
minimal-agent-loop/requirements.txt:3: warn  UNBOUNDED-PIN  anthropic >=0.120.0 has no upper bound: a fresh install resolves to SDK 2.x (API 2026-10-21), which this code was not tested on
orchestrator-subagents/requirements.txt:4: warn  UNBOUNDED-PIN  anthropic >=0.120.2 has no upper bound: a fresh install resolves to SDK 2.x (API 2026-10-21), which this code was not tested on
skill-reference-files-bigquery/requirements.txt:3: warn  UNBOUNDED-PIN  anthropic >=0.120.0 has no upper bound: a fresh install resolves to SDK 2.x (API 2026-10-21), which this code was not tested on
skill-script-execution-word-counter/requirements.txt:3: warn  UNBOUNDED-PIN  anthropic >=0.120.0 has no upper bound: a fresh install resolves to SDK 2.x (API 2026-10-21), which this code was not tested on
streaming-tool-loop/requirements.txt:6: warn  UNBOUNDED-PIN  anthropic >=0.120.0 has no upper bound: a fresh install resolves to SDK 2.x (API 2026-10-21), which this code was not tested on
strict-tool-schemas/requirements.txt:9: warn  UNBOUNDED-PIN  anthropic >=1.3.0 has no upper bound: a fresh install resolves to SDK 2.x (API 2026-10-21), which this code was not tested on
tool-error-policy/requirements.txt:3: warn  UNBOUNDED-PIN  anthropic >=0.120.0 has no upper bound: a fresh install resolves to SDK 2.x (API 2026-10-21), which this code was not tested on
typed-tool-registry/requirements.txt:4: warn  UNBOUNDED-PIN  anthropic >=0.120.0 has no upper bound: a fresh install resolves to SDK 2.x (API 2026-10-21), which this code was not tested on
9 finding(s) (0 error, 9 warn) in 150 file(s); 1 suppressed by pragma
```

What this shows: the scanner found the same nine open-ended pins that the
research note found with grep, and it found no code-level pattern anywhere in
`examples/`. So the lab's risk is in what gets installed, not in its code. The
three `<2` caps and four exact pins are correctly left out. Running
`python3 scan.py ../..` (the whole repo, including `projects/` and `.pipeline/`)
gives the same nine findings across 159 files. The single suppressed finding is
the scanner's own rule constant (see below).

This scan does **not** check whether those nine examples' offline tests still
pass on `anthropic` 2.3.0. That would need a clean venv per example, and it is
the natural next cycle.

## Pragmas

A finding can be silenced on one line with a comment naming its rule:

```python
_XAPIKEY = "x-api-key"  # sdk2-compat: ignore[XAPIKEY-HEADER]
```

That exact line is in `rules.py`, because otherwise the scanner would flag its
own rule table. A suppressed finding is not dropped. It moves to
`SourceReport.suppressed`, gets counted in the summary line, and is listed
under `"suppressed"` in `--json`. A pragma only suppresses the rule it names.
A pragma naming an unknown rule id is reported as a `PARSE-ERROR`, so a typo
can't silently suppress nothing. Only real comment tokens count: pragma text
inside a string or a docstring is ignored.

## Design choices (where the note left room)

- **`UNBOUNDED-PIN` means "no upper bound", not "admits 2.x".** `anthropic<3`
  and `anthropic==2.3.0` are deliberate choices, so they are not flagged. The
  rule targets the case where nobody chose: a floor that now resolves to a
  major version the code never ran on. Because of this, the rule only needs
  the operator and never compares version numbers.
- **`SAMPLING-PARAM` requires `.messages.` right before the method.** That
  keeps `openai.chat.completions.create(temperature=...)` out, at the cost of
  missing aliases (`create = client.messages.create`).
- **`scan_source` returns a `SourceReport`, not a bare `list[Finding]`.** This
  differs from the note's sketch. It's needed so suppressed findings can be
  carried separately instead of being dropped (see Pragmas).
- **Rules are an `Enum` that carries id and severity.** A finding can't hold a
  rule id that doesn't exist, and `Finding` won't construct with `line < 1` or
  an empty message.
- **Message-shape rules follow the documented shapes only.** Citations are
  checked on direct user content and inside `search_result`. They are not
  checked inside `tool_result`, because the migration page doesn't say that
  case breaks.

## Not covered (known misses, not "clean")

Calls through aliases or `getattr`; keywords passed through `**kwargs`;
`dict(temperature=...)` built with the constructor; message dicts assembled at
runtime; variable-role dicts (the note says these fail type-checking only);
`pyproject.toml`, `setup.py`, `Pipfile` and lock files; backslash continuations
in requirements files; non-Python SDKs (Text Completions removal, Go `/v2`,
Java 3.0.0); Bedrock/Vertex/Foundry clients. Auto-fixing is out of scope. The
failure domain is read-only: the scanner opens files for reading and nothing
else, and any directory or file it cannot read stops the scan with exit 2
instead of producing a partial "clean" report.
