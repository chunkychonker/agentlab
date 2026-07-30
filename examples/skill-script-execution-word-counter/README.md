# A skill that shells out to a local script

Demonstrates the Skills **script-execution (level 3 resource)** mechanism with
a real, standalone script executed as a real subprocess — not mocked. This is
about *running* a bundled script, as distinct from a Skill *reading* a
reference file (that's a separate, still-open backlog item).

From the research note:
[`research/2026-07-30-skill-script-execution.md`](../../research/2026-07-30-skill-script-execution.md).
Companion piece, on Skill trigger mechanics (levels 1-2 — metadata always
loaded, body loaded on trigger):
[`research/2026-07-29-anatomy-of-a-skill.md`](../../research/2026-07-29-anatomy-of-a-skill.md)
(not yet built as of this cycle).

## What this is — and isn't

This is hand-built against the raw Anthropic **Messages API**, the same way
every other example in this repo is. It deliberately does **not** exercise:

- Claude Code's `${CLAUDE_SKILL_DIR}` / `allowed-tools` permission plumbing —
  that's Claude-Code-specific machinery for skipping a bash permission
  prompt, not reproducible (or meaningful) against the raw API.
- The beta Claude API Skills / code-execution container — that needs a beta
  header and a sandboxed environment this repo doesn't stand up.
- Real skill *triggering* (a live model deciding to invoke the skill from its
  description) — see `anatomy-of-a-skill` for that mechanism. This example's
  tool is always offered to the model directly, the same as every other
  example here.

What it does demonstrate, with real, checkable code:

1. **Only stdout enters context, never the script's source.** `agent.py`'s
   `run_word_counter` tool runs `count_words.py` as a real subprocess and
   returns its captured stdout, verbatim, as the tool result — the ~90 lines
   of Python that make up the script are never read into the model's context.
2. **"Solve, don't defer."** `count_words.py` never lets a Python traceback
   reach stdout or stderr. Every failure mode — file not found, path is a
   directory, file unreadable — is a one-line JSON object on stdout plus a
   distinct nonzero exit code.

## What's here

| File | What it is |
|------|-----------|
| `skills/word-counter/SKILL.md` | A real, correctly authored Skill file: frontmatter + a body that says "Run `count_words.py`..." (execute), never "see `count_words.py` for the algorithm" (read as reference) — the doc's own execute-vs-reference authoring rule, verbatim. Included as a reference artifact; `agent.py` does not parse or load it. |
| `skills/word-counter/scripts/count_words.py` | The real, standalone script. No repo imports; counts words/lines/chars in a file; every failure mode is a clean JSON error + distinct exit code, never a traceback. |
| `agent.py` | `run_word_counter()` (runs the script as a subprocess, returns its stdout) wired into a one-tool manual agent loop, same shape as `examples/minimal-agent-loop/agent.py`. |
| `test_agent.py` | Offline self-test — see below. |
| `sample.txt` | Small text file the live demo asks Claude to count. |
| `requirements.txt` | `anthropic` — only needed for the live run. |

## Run the self-test (no API key needed)

```bash
cd examples/skill-script-execution-word-counter
python3 test_agent.py
```

Expected output:

```
ok  count() returns correct words/lines/chars for real content
ok  missing file -> {"error": ...}, exit 1, no traceback
ok  directory path -> {"error": ...}, exit 2, no traceback
ok  unreadable file -> {"error": ...}, exit 3, no traceback
ok  empty file -> all-zero counts, exit 0 (not an error)
ok  run_word_counter() (subprocess) agrees with count() (direct call)
ok  tool result contains only stdout - the script's own source never crosses in
ok  manual loop executes the real script mid-conversation and relays its real stdout

All 8 self-tests passed.
```

(The unreadable-file case is skipped with a message, not silently dropped, if
running as root makes `chmod 000` unenforceable.)

The most load-bearing assertions:

- **Context-boundary test**: after calling `run_word_counter()`, the returned
  string is asserted to *not* contain distinctive substrings from
  `count_words.py`'s own source (`"def count("`) — the checkable form of
  "only stdout enters context, the code never does."
- **Real-execution-in-the-loop test**: the scripted fake-client loop test
  computes the expected tool result by calling `run_word_counter()`
  independently, *then* asserts the loop's `tool_result` content equals that
  value exactly — proving the loop actually ran the real script mid-
  conversation rather than the test faking the tool's effect.

You can also run the script directly, the same way a subprocess call does:

```bash
python3 skills/word-counter/scripts/count_words.py sample.txt
# {"words": 25, "lines": 3, "chars": 153}

python3 skills/word-counter/scripts/count_words.py /no/such/file
# {"error": "file not found: /no/such/file"}   (exit 1, no traceback)
```

## Run it live (needs a key)

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python3 agent.py
```

It asks Claude to count the words/lines/characters in `sample.txt`, prints the
tool call and the script's real stdout, then Claude's final answer. Without
`ANTHROPIC_API_KEY` set, `agent.py` prints a one-line note and exits 0 — it
never crashes.

Model id is the constant `MODEL` at the top of `agent.py` (default
`claude-haiku-4-5`, the cheapest current model — switch tiers in one line). See
[`knowledge/anthropic-models.md`](../../knowledge/anthropic-models.md).

## Scope

One script, one tool, one loop, one test file — matching the note's explicit
out-of-scope list: no Claude-Code permission plumbing, no beta Skills API
container, no real triggering, no plan-validate-execute pattern, no scripts
registry. The natural next increment (also already in `BACKLOG.md`) is the
read-not-execute sibling: "Packaging a skill with reference files the model
loads on demand."
