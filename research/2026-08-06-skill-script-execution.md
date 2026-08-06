# A skill that shells out to a local script

## Question

What is the documented, current (Claude Code v2.1.x) mechanism for a Skill to
bundle and execute a local script — including how permission pre-approval
(`allowed-tools` + `${CLAUDE_SKILL_DIR}`) actually works — and what's the
smallest same-day-buildable increment that demonstrates it with a script whose
correctness is genuinely offline-testable?

## Findings

Primary source, fetched today (2026-08-06): [Extend Claude with skills](https://code.claude.com/docs/en/skills)
(code.claude.com — the Claude-Code-specific mechanics doc; last confirmed
current for this repo in the 2026-08-05 skill-anatomy research cycle too). Also
[Skill authoring best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices),
re-fetched today for its "Advanced: Skills with executable code" section.

Doc version gates cited inline below (e.g. "v2.1.129") come directly from the
primary source's own frontmatter-reference table — this is a fast-moving
surface, the docs themselves say so by gating individual fields to specific
CLI versions.

### The core mechanism: `${CLAUDE_SKILL_DIR}` + `allowed-tools`

A skill directory can bundle a `scripts/` folder; Claude executes scripts via
Bash rather than reading their source into context — "only the script's output
consumes tokens" (best-practices doc, "Provide utility scripts" section). This
is the level-3 progressive-disclosure tier already documented in
[[agent-skills]] (this repo's knowledge note from 2026-08-05); today's finding
is the *permission* half of the mechanism, which that note didn't cover.

The `${CLAUDE_SKILL_DIR}` variable expands to "the directory containing the
skill's `SKILL.md` file" — correct regardless of whether the skill is
installed at the personal (`~/.claude/skills/`), project (`.claude/skills/`),
or plugin level. Claude Code substitutes it in **two** places: the markdown
body, and `Bash(...)` rules inside the `allowed-tools` frontmatter field. Using
the identical substituted path in both places is what lets a skill's bundled
script run **without a permission prompt**:

```yaml
---
name: render-chart
description: Render a chart from a CSV file
allowed-tools: Bash(${CLAUDE_SKILL_DIR}/scripts/render.sh *)
---

Run `${CLAUDE_SKILL_DIR}/scripts/render.sh <csv-file>` to render the chart.
```

Docs, verbatim: "The `allowed-tools` substitution for `${CLAUDE_SKILL_DIR}`
requires Claude Code v2.1.129 or later. On earlier versions the rule stays a
literal `${CLAUDE_SKILL_DIR}` string and never matches, so the command still
prompts for permission." — a concrete, checkable version gate; the builder
should run `claude --version` and confirm ≥2.1.129 before relying on this to
suppress the permission prompt.

`allowed-tools` grants clear at the end of the turn that invoked the skill
(not the whole session) — re-invoking the skill re-applies the grant. This is
stated identically in the frontmatter reference table and the "Skill content
lifecycle" section.

### `Bash(prefix *)` is a documented prefix-match rule, not just for scripts

The docs show three real examples of the same syntax family, confirming it's
a general prefix-match mechanism, not something special-cased for
`${CLAUDE_SKILL_DIR}`:
- `Bash(${CLAUDE_SKILL_DIR}/scripts/render.sh *)` (render-chart example)
- `Bash(git add *) Bash(git commit *) Bash(git status *)` (a commit-skill example, space-separated multiple rules in one field)
- `Bash(python3 *)` (the codebase-visualizer worked example, below — broad, allows *any* python3 invocation)

For a tighter grant than `Bash(python3 *)`, pinning the rule to the full
command including the script path — e.g.
`Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/scan_dependencies.py *)` — is a
reasonable extrapolation from the documented prefix-match behavior, but the
docs' own worked examples never show a *two-token* prefix (interpreter +
path) together; this exact combination is not literally demonstrated and
should be verified live (see Open questions).

### The full worked example: `codebase-visualizer`

The docs ship a complete, runnable skill (`SKILL.md` + `scripts/visualize.py`,
stdlib-only Python) that scans a directory tree and writes a self-contained
interactive HTML report. It demonstrates the pattern end to end: description
states the trigger ("Use when exploring a new repo..."), body tells Claude to
run `python3 ${CLAUDE_SKILL_DIR}/scripts/visualize.py .`, `allowed-tools:
Bash(python3 *)` pre-approves execution, and the script prints its result path
to stdout so Claude (and a human, in a headless environment) can confirm
success without a browser. This is architecturally the same "recruiting
scanner" shape the backlog item names: a script walks local files and reports
structured findings; the skill's job is to invoke it correctly and narrate the
output. (Note: "recruiting scanner pattern" is not a documented Anthropic term
— it doesn't appear in any primary source found. Treating it as the backlog
author's own analogy for "a skill that shells out to a scanning script,"
which the codebase-visualizer example is a real, primary-sourced instance of.)

### Script-authoring guidance ("Advanced: Skills with executable code")

From the best-practices doc, directly applicable to any script a skill bundles:

- **"Solve, don't defer"**: handle expected error conditions (missing file,
  permission error) explicitly inside the script with a documented fallback —
  never `open(path).read()` and let Claude improvise on the traceback.
- **No "voodoo constants"**: any hardcoded threshold/timeout needs a one-line
  comment justifying the value.
- **Prefer scripts for deterministic operations**: "Write `validate_form.py`
  rather than asking Claude to generate validation code" — the same principle
  this repo already applies with `validate_skill.py` in the skill-anatomy
  example.
- **Package dependencies**: on claude.ai the code-execution sandbox can `pip
  install`; **the Claude API has no network access and no runtime package
  installation**. Not directly relevant here (Claude Code skills run scripts
  via the user's own local Python, not a sandboxed container), but worth
  restating since it's easy to conflate the three "Skills" products (see
  [[agent-skills]]).

### A relevant, older, open bug report — treat allowed-tools as best-effort, verify live

[GitHub issue anthropics/claude-code#14956](https://github.com/anthropics/claude-code/issues/14956)
(status: open, no confirmed fix date; reported against Claude Code v2.0.75,
labels `bug`, `area:security`, `area:tools`, `has repro`) describes a skill
`allowed-tools: Bash(say -v "Samantha":*)` rule being reported as active but
the matching Bash command still prompting for approval. v2.0.75 is far behind
the v2.1.129+ gate the current docs cite for `${CLAUDE_SKILL_DIR}`
substitution specifically, and the repro uses an older `prefix:*` colon
syntax rather than the `prefix *` space-syntax the current docs demonstrate —
so this may already be resolved by version and syntax, but it is not
confirmed fixed from a primary source. **Build implication**: the offline
self-test can only prove the *script* is correct; whether `allowed-tools`
actually suppresses the permission prompt in a live session is not something
this research environment can verify, and should be checked manually by the
builder/reviewer with a real `claude` session, exactly as the skill-anatomy
research note already flagged for triggering itself.

## Build proposal

### Intent

Ship one real, installable Claude Code skill, `scanning-dependencies`, that
demonstrates the shell-out-to-a-bundled-script pattern correctly — a
`${CLAUDE_SKILL_DIR}`-scoped `allowed-tools` grant, a stdlib-only Python
script that scans a directory for dependency manifests
(`requirements.txt`, `package.json`) and reports which entries are pinned vs.
unpinned, per this repo's own rule ("Pin dependency versions. Builds must be
reproducible.", `~/agentlab/CLAUDE.md` §5) — plus an offline test suite for
the script. Out of scope: proving the permission-prompt suppression actually
works live (not deterministically testable offline — same caveat class as
skill triggering itself, documented in [[agent-skills]]); scanning any
manifest format beyond `requirements.txt`/`package.json` (e.g. `Cargo.toml`,
`go.mod` — a natural follow-up, not today's scope); and any change to the
existing `skill-anatomy` example's validator (this is a separate, standalone
example).

### Behavioral spec

**`examples/skill-script-execution/skills/scanning-dependencies/SKILL.md`**

- Frontmatter: `name: scanning-dependencies` (gerund form, per the naming
  convention in the best-practices doc), `description` third-person, states
  what ("Scans a project's `requirements.txt` and `package.json` for
  unpinned dependency versions") and when ("Use when the user asks to check,
  audit, or scan dependency pinning, or asks whether dependencies are
  pinned/locked before a build"), `allowed-tools:
  Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/scan_dependencies.py *)`.
- Body (~20-30 lines): instructs Claude to run
  `python3 ${CLAUDE_SKILL_DIR}/scripts/scan_dependencies.py <directory>`
  (default `.`), parse the JSON on stdout, and summarize findings to the
  user (which files, which packages, why each is unpinned) — explicitly
  told not to re-implement the scanning logic itself (the "medium/low
  freedom" distinction from the best-practices doc: this is a low-freedom,
  "run exactly this script" case).

**`examples/skill-script-execution/skills/scanning-dependencies/scripts/scan_dependencies.py`**

- Input: one optional positional CLI arg, a directory path (default `.`).
- Behavior: walks the tree (skipping `.git`, `node_modules`, `.venv`,
  `venv`, `__pycache__`, `dist`, `build` — same ignore set as the
  codebase-visualizer example, for consistency), and for each
  `requirements.txt` found, flags a line as unpinned if it has no `==`
  exact pin (bare name, or a `>=`/`~=`/`>`/`<` range) — comments and `-r
  other.txt` includes are skipped, not flagged. For each `package.json`
  found, flags each `dependencies`/`devDependencies` entry whose version
  string starts with `^`, `~`, `*`, or is `"latest"` — exact semver strings
  count as pinned.
- Output: a single JSON object on stdout:
  `{"scanned": [<paths>], "findings": [{"file": ..., "package": ...,
  "version_spec": ..., "reason": ...}], "count": N}`. Always valid JSON on
  stdout, even when `count` is 0.
- Exit codes (a genuine correctness boundary, stated in the module
  docstring): `0` for any completed scan, regardless of whether findings
  were produced (finding unpinned deps is data, not a script failure — the
  narration/judgment is left to Claude reading the JSON, matching "solve,
  don't defer" applied to *what the script owns* vs. what it doesn't); `2`
  and a one-line JSON error object on stdout (`{"error": "..."}` — never a
  raw traceback) if the given directory does not exist — a genuine
  operational failure, per "solve, don't defer": handled explicitly, not
  left for Claude to interpret a stack trace.
- Malformed `package.json` (invalid JSON): caught explicitly, reported as
  one finding-like entry with `"reason": "could not parse package.json:
  <json error message>"` rather than crashing the whole scan — a partial
  failure that shouldn't lose findings already collected from other files.

**Acceptance criteria ("it works"):**

1. `python3 scripts/scan_dependencies.py .` run against the skill's own
   fixture directory (see below) exits 0 and prints valid JSON with the
   expected findings.
2. `python3 test_scan_dependencies.py` (stdlib-only `unittest`, no network,
   no API key — same convention as `skill-anatomy/test_validate_skill.py`)
   passes, using `tempfile`/fixture directories to assert the *specific*
   finding for each case, not just "some finding appeared":
   - a `requirements.txt` with a bare `numpy` line → flagged unpinned
   - a `requirements.txt` with `numpy==1.26.4` → not flagged
   - a `requirements.txt` with `numpy>=1.26` → flagged unpinned (range, not exact)
   - a `package.json` dependency `"lodash": "^4.17.21"` → flagged unpinned
   - a `package.json` dependency `"lodash": "4.17.21"` → not flagged
   - a directory with no manifest files → `count: 0`, valid JSON, exit 0
   - a nonexistent directory → exit 2, JSON `{"error": ...}` on stdout, no
     raw traceback
   - a `package.json` containing invalid JSON → reported as one finding
     with a parse-error reason, scan still completes (exit 0) and any
     valid `requirements.txt` findings in the same tree are still present
3. `examples/skill-script-execution/README.md` documents: the
   `${CLAUDE_SKILL_DIR}` + `allowed-tools` mechanism and its v2.1.129+
   version gate (tell the reader to check `claude --version`), how to
   install the skill, the manual/live verification step (open `claude` in
   a directory with a `requirements.txt`, ask "are my dependencies
   pinned?", confirm the skill triggers, the script runs *without* a
   permission prompt, and Claude reports the findings correctly) —
   explicitly labeled manual/live since permission-prompt suppression is
   not offline-testable, and a one-line pointer to the open,
   version-mismatched GitHub issue as a "verify this yourself" caveat.

### Interfaces (stubs only — builder implements bodies)

```python
# examples/skill-script-execution/skills/scanning-dependencies/scripts/scan_dependencies.py
"""Scan a directory for requirements.txt/package.json and report unpinned deps.

Failure modes:
- Nonexistent root directory: prints {"error": ...} JSON to stdout, exits 2.
- Malformed package.json: caught per-file, recorded as one finding with a
  parse-error reason; does not abort the rest of the scan.
- No manifests found: valid JSON with an empty findings list, exit 0 (not
  an error — "nothing to scan" is a legitimate outcome).
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, asdict
from pathlib import Path

IGNORE_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build"}

@dataclass(frozen=True)
class Finding:
    file: str
    package: str
    version_spec: str
    reason: str

def scan_requirements_txt(path: Path) -> list[Finding]: ...

def scan_package_json(path: Path) -> list[Finding]: ...

def scan_directory(root: Path) -> dict:
    """Walk root, return {"scanned": [...], "findings": [...], "count": N}.

    Raises FileNotFoundError if root does not exist (caught by main(),
    never propagated raw to the caller of scan_directory itself — this
    function's contract is to raise, main()'s contract is to catch and
    format).
    """

def main(argv: list[str]) -> int:
    """CLI entry point. Prints one JSON object to stdout. Returns 0 or 2."""

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

### Open questions

- Whether `allowed-tools: Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/scan_dependencies.py *)`
  (a two-token interpreter+path prefix) actually suppresses the permission
  prompt in a live v2.1.129+ session — the docs only demonstrate single-token
  prefixes (`${CLAUDE_SKILL_DIR}/scripts/render.sh *`) and a fully-open
  interpreter prefix (`python3 *`), never the combination this proposal
  uses. Low risk (it's a straightforward extrapolation of a documented
  prefix-match rule) but unconfirmed from a primary source — the README's
  manual verification step is exactly this check, and if it fails, the
  fallback documented in the render-chart pattern (`.sh` wrapper script
  invoked with a single-token path prefix) is the fix.
- Whether the open, unresolved `allowed-tools` bug
  ([#14956](https://github.com/anthropics/claude-code/issues/14956)) still
  reproduces on current Claude Code — filed against v2.0.75 with older
  `prefix:*` colon syntax; not confirmed fixed or still-open-on-current-syntax
  from a primary source.
- Exact current Claude Code version wasn't checked against a live install in
  this research environment (no `claude` CLI available here, consistent with
  the same gap noted in the 2026-08-05 skill-anatomy note) — the builder
  should `claude --version` before finalizing the `allowed-tools` claim in
  the README.
