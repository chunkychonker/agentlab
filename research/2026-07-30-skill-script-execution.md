# A skill that shells out to a local script

> Shipped as `examples/skill-script-execution-word-counter/` — a separate,
> later cycle (2026-08-06) independently claimed `examples/skill-script-execution/`
> for a different increment (`research/2026-08-06-skill-script-execution.md`)
> and merged first, so this one's directory was disambiguated at merge time.

## Question

What exactly happens, mechanically, when a Skill's body tells Claude to
**run a bundled script** rather than read reference text — what enters the
model's context vs. what stays on disk, what does Anthropic's own guidance say
a well-behaved script must do, and what is the smallest faithful, runnable
increment this repo can ship to demonstrate that specific mechanism today?

## Findings

### This is "level 3" of the same progressive-disclosure model already documented

[[agent-skills]] (this repo's existing note, written 2026-07-29) already
covers levels 1–2 (metadata always loaded, body loaded on trigger) and names
level 3 ("Resources/code... executed via bash; only stdout enters context,
code never does") but doesn't dig into it. This cycle's job is level 3
specifically: **script execution**, not reference-file reading.

### What actually enters context when a script runs

From the Claude Code skills doc (fetched 2026-07-30):

> `scripts/helper.py (utility script - executed, not loaded)`

and, worked through a full example (the `codebase-visualizer` skill, same
doc): the `SKILL.md` body tells Claude to run
`python3 ${CLAUDE_SKILL_DIR}/scripts/visualize.py .`; Claude runs it via Bash;
the script prints one line (`Generated /path/to/codebase-map.html`) and opens
a browser; **that printed line, not the ~120-line script source, is what
Claude sees**. [Extend Claude with skills](https://code.claude.com/docs/en/skills)
— code.claude.com, fetched 2026-07-30, version-gated up to Claude Code
v2.1.218 (actively maintained; re-check version-specific claims against the
installed CLI).

The Skills authoring best-practices doc states the same fact in its own
architecture section (fetched 2026-07-30):

> "Scripts executed efficiently: Utility scripts can be executed through bash
> without loading their full contents into context. Only the script's output
> consumes tokens."

and gives the execute-vs-read distinction explicitly as an authoring
requirement, not an implementation detail Claude infers on its own:

> "Make execution intent clear: 'Run `analyze_form.py` to extract fields'
> (execute) [vs.] 'See `analyze_form.py` for the extraction algorithm' (read
> as reference)"

[Skill authoring best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)
— platform.claude.com, fetched 2026-07-30.

### `${CLAUDE_SKILL_DIR}` + `allowed-tools` is how Claude Code avoids a permission prompt on every run

Claude Code substitutes two variables both in the SKILL.md body *and* in
`allowed-tools` Bash rules: `${CLAUDE_SKILL_DIR}` (the skill's own directory,
regardless of cwd) and `${CLAUDE_PROJECT_DIR}`. The doc's canonical pattern:

```yaml
---
name: render-chart
description: Render a chart from a CSV file
allowed-tools: Bash(${CLAUDE_SKILL_DIR}/scripts/render.sh *)
---
Run `${CLAUDE_SKILL_DIR}/scripts/render.sh <csv-file>` to render the chart.
```

"Using the same variable in both places lets a skill run a bundled script
without a permission prompt." This is Claude-Code-specific plumbing (not part
of the plain API Skills surface, which has no interactive permission prompt
concept at all) — source as above, fetched 2026-07-30.

### Anthropic's own authoring contract for scripts: "solve, don't defer"

This is the one durable, checkable engineering rule in the best-practices doc
(fetched 2026-07-30), and it's a spec, not a style note:

> "When writing scripts for Skills, handle error conditions rather than
> deferring to Claude." Bad: `return open(path).read()` (lets a bare
> `FileNotFoundError` traceback surface). Good: catch the specific exception,
> print a clear message, and return usable output either way.

Same doc, same section, on magic numbers: any timeout/retry constant must be
commented with *why* that value, "voodoo constants" are called out by name
(Ousterhout's law) as an anti-pattern. Directly actionable for any script this
repo ships.

### Package/network constraints differ by substrate (repeats the API-vs-Claude-Code split from [[agent-skills]])

> "claude.ai: Can install packages from npm and PyPI and pull from GitHub
> repositories. Claude API: Has no network access and no runtime package
> installation." — same best-practices doc, fetched 2026-07-30.

Not directly load-bearing for this increment (we don't use the beta Skills
API container at all — see Build proposal), but confirms the container has
no pip install step, so any real Skills-API script must ship pinned to only
what the container provides.

### "recruiting scanner pattern" — could not verify this as a named source

The backlog item's parenthetical ("like the recruiting scanner pattern") does
not match anything findable: no reference to it anywhere in this repo
(`grep -ri recruiting .` returns only the backlog line itself), and a web
search (`WebSearch`, 2026-07-30) for a Claude/Claude-Code skill matching that
description returned no specific hit — only generic skills-collection
repositories. Treating it as the backlog author's own illustrative analogy
(a skill that scans/processes input files via a local script), not a citable
pattern. Flagging per instructions rather than guessing at a source.

### Sources
- [Extend Claude with skills](https://code.claude.com/docs/en/skills) — code.claude.com, fetched 2026-07-30 (script execution, `${CLAUDE_SKILL_DIR}`/`allowed-tools` pattern, full `codebase-visualizer` worked example with real script source)
- [Skill authoring best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices) — platform.claude.com, fetched 2026-07-30 ("Advanced: Skills with executable code" section: solve-don't-defer, voodoo constants, execute-vs-reference, package/network constraints, runtime environment)
- [Agent Skills overview](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview) — background only, previously fetched 2026-07-29 per [[agent-skills]]
- `WebSearch` for "recruiting scanner" skill examples — 2026-07-30, no specific match found (see above)

None of these are stale by the ~1-year bar; both primary docs are the same
actively-version-gated pages already flagged as such in [[agent-skills]].

## Build proposal

### Intent

Demonstrate, with a real (not mocked) script execution, the one concrete,
checkable claim from Findings that the existing `anatomy-of-a-skill` note's
proposed example does **not** cover: when a skill's instructions say "run
this script," (a) only the script's **stdout** enters the conversation as the
tool result — never the script's source text — and (b) a script following the
"solve, don't defer" contract never lets an uncaught exception reach the
model; every failure mode is a deterministic, parseable message on stdout.

**Out of scope:** the beta Claude API Skills/code-execution container (needs
a beta header and a sandboxed environment this repo doesn't stand up);
Claude Code's own `${CLAUDE_SKILL_DIR}`/`allowed-tools` permission plumbing
(Claude-Code-specific, not reproducible against the raw Messages API);
real skill *triggering*/discovery (that's `anatomy-of-a-skill`'s job); the
plan-validate-execute pattern; multiple scripts or a scripts registry.

### Where: `examples/skill-script-execution-word-counter/`

```
examples/skill-script-execution-word-counter/
├── README.md
├── requirements.txt        # anthropic (only needed for the live path)
├── skills/
│   └── word-counter/
│       ├── SKILL.md        # body says: RUN this script, don't read it
│       └── scripts/
│           └── count_words.py
├── agent.py
└── test_agent.py
```

`skills/word-counter/SKILL.md` follows the doc's execute-vs-reference
authoring rule verbatim ("Run `count_words.py` to count words" — imperative,
not "see ... for the algorithm"), and its script follows the solve-don't-defer
contract: every failure mode below produces a one-line JSON object on stdout
and a distinct nonzero exit code — never a Python traceback.

### Behavioral spec

**`count_words.py`** (a real, standalone script — no LLM involved in this
half; it's the "level 3 resource" itself):
- Input: one CLI arg, a file path.
- Output (success): `{"words": N, "lines": M, "chars": K}` on stdout, exit 0.
- Failure modes, each a JSON object on stdout + a distinct nonzero exit code,
  never a traceback: path does not exist (`{"error": "file not found: ..."}`,
  exit 1); path is a directory (`{"error": "not a file: ..."}`, exit 2);
  file unreadable, e.g. permission denied (`{"error": "cannot read: ..."}`,
  exit 3).
- Invariant: an empty file is not an error — `{"words": 0, "lines": 0,
  "chars": 0}`, exit 0.

**`agent.py`**'s one tool, `run_word_counter(path: str) -> str`:
- Runs `count_words.py` as a real local subprocess (`subprocess.run`,
  captured stdout only, per the doc's "only the script's output consumes
  tokens" claim — this repo's tool implementation enforces the same contract
  the docs describe for the Skills execution environment).
- Returns exactly the subprocess's captured stdout as the tool_result
  content string — nothing else, and never the script's own source text.
- Failure mode: if the subprocess cannot even start (e.g. `python3` missing),
  raises — this is an environment bug the caller must see, not something to
  paper over with a default result.

### Interfaces (layer 3 — for the builder to implement)

```python
# count_words.py — standalone, no repo imports, run only as a subprocess.
def count(path: str) -> dict:
    """Count words/lines/chars in a text file.

    Failure modes: returns {"error": "<message>"} instead of raising for
    missing file, non-file path, or unreadable file — see Behavioral spec.
    Never lets an exception escape main().
    """

# agent.py
MODEL = "claude-haiku-4-5"  # knowledge/anthropic-models.md

def run_word_counter(path: str) -> str:
    """Execute scripts/count_words.py as a subprocess and return its stdout
    verbatim as the tool result.

    Failure modes: if the subprocess itself cannot start, propagates the
    OSError (environment problem, not a tool-usage problem — no default).
    """

TOOLS: list[dict]          # one tool: run_word_counter(path: str)
TOOL_FUNCTIONS: dict[str, Callable]

def run_agent(client, user_message: str, *, max_turns: int = 5) -> str:
    """Same shape as examples/minimal-agent-loop/agent.py:run_agent."""
```

### What "it works" means (acceptance criteria / self-test)

All offline, deterministic, no network for the primary assertions (the
script itself is real code executed as a real subprocess — this is the point,
not something to fake):

1. `count("some_real_tmpfile.txt")` with known content returns the exact
   correct `{"words", "lines", "chars"}` dict.
2. `count("/path/does/not/exist")` returns `{"error": "..."}` containing the
   path — not a raised exception — asserted by calling the script as a real
   subprocess (`subprocess.run([...], capture_output=True)`) and checking
   `returncode == 1` and `stdout` parses as JSON with an `"error"` key. This
   is the checkable form of "solve, don't defer": the test proves no
   traceback ever reaches stdout/stderr for this failure mode.
3. Same for a directory path (exit 2) and (if constructible in CI, e.g.
   `chmod 000`) an unreadable file (exit 3) — skip that one gracefully if
   running as root makes permissions unenforceable, but don't silently drop
   the file-not-found and is-a-directory cases.
4. Empty file returns all-zero counts with exit 0 (not treated as an error).
5. `run_word_counter` on a real temp file returns a string that, parsed as
   JSON, matches `count()`'s direct return — proving the subprocess path and
   the direct-call path agree.
6. **The load-bearing context-boundary assertion**: after calling
   `run_word_counter`, assert the returned string does **not** contain any
   distinctive substring from `count_words.py`'s own source (e.g. `"def count"`
   or the script's docstring) — the checkable form of "only stdout enters
   context, the code never does."
7. Fake-client loop test (same pattern as `minimal-agent-loop/test_agent.py`):
   turn 1 = `tool_use` calling `run_word_counter` with a real temp file path;
   turn 2 = `end_turn` text. Assert the `tool_result` sent back on turn 2's
   input equals exactly `run_word_counter`'s real return value (not a
   mocked/scripted one) — proving the loop actually executed the real script
   mid-conversation rather than faking the tool's effect.

`agent.py`'s `main()` follows the established convention: no
`ANTHROPIC_API_KEY` → print and exit 0; if set, one live call asking Claude to
count words in a bundled sample file, printing the tool call and its result.

### README must state explicitly

That this demonstrates the Skills **script-execution / level-3-resource**
mechanism (stdout-only context entry; solve-don't-defer scripts) hand-built
against the raw Messages API — not Claude Code's actual `${CLAUDE_SKILL_DIR}`/
`allowed-tools` permission plumbing, and not the beta Claude API Skills
container. Cross-link to `anatomy-of-a-skill` (levels 1–2: metadata +
trigger) as the companion piece, and to the still-open backlog item
"Packaging a skill with reference files the model loads on demand" (the
level-3 sibling: read-not-execute) as the natural next increment.

## Open questions

- Whether `anatomy-of-a-skill` (this repo's previous research note,
  2026-07-29) has actually been built yet — as of this cycle its `BACKLOG.md`
  line still reads `[researching]`, not `[building]`/`[done]`, and no
  `examples/anatomy-of-a-skill/` directory exists yet. This proposal is
  designed to stand alone either way (it doesn't import from that example),
  but if both land the same day the README cross-links above should be
  checked for accuracy once both exist.
- Could not find any citable source for the backlog's "recruiting scanner
  pattern" phrase — see Findings. If whoever wrote that backlog item had a
  specific example in mind, it would be worth a follow-up to check this
  proposal actually matches their intent.
- Did not verify Claude Code's actual current behavior on a real
  `${CLAUDE_SKILL_DIR}`-using skill in a live session (would require a real
  Claude Code install exercising the filesystem discovery path) — the
  proposal deliberately avoids depending on this by building the mechanism
  against the raw Messages API instead, per the same reasoning
  `anatomy-of-a-skill` used for the trigger mechanism.

## Knowledge base

Extended [[agent-skills]] with a new "Script execution (level 3)" section
covering the stdout-only context-entry fact, the `${CLAUDE_SKILL_DIR}` +
`allowed-tools` no-prompt pattern, and the solve-don't-defer script contract.
No new knowledge file created this cycle — this is additive detail on the
same mechanism, not a new topic.
