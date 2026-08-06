# A skill that shells out to a local script (`scanning-dependencies`)

A Claude Code **Agent Skill** that bundles and runs a local script — the
`${CLAUDE_SKILL_DIR}` + `allowed-tools` pattern for pre-approving a script
invocation, demonstrated with a genuinely offline-testable script: it scans
a directory for `requirements.txt` / `package.json` and reports which
dependency entries are pinned vs. unpinned, per this repo's own rule ("Pin
dependency versions. Builds must be reproducible.", `~/agentlab/CLAUDE.md`
§5).

From the research note:
[`research/2026-08-06-skill-script-execution.md`](../../research/2026-08-06-skill-script-execution.md).
Builds on
[`examples/skill-anatomy`](../skill-anatomy/) and
[`knowledge/agent-skills.md`](../../knowledge/agent-skills.md).

## What's here

| File | What it is |
|------|-----------|
| `skills/scanning-dependencies/SKILL.md` | The skill: frontmatter with a `${CLAUDE_SKILL_DIR}`-scoped `allowed-tools` grant, and a body that tells Claude to run the bundled script and narrate its JSON — not to re-implement the scan itself. |
| `skills/scanning-dependencies/scripts/scan_dependencies.py` | Stdlib-only Python. Walks a directory, flags unpinned deps in any `requirements.txt`/`package.json` it finds, prints one JSON object to stdout. |
| `test_scan_dependencies.py` | Offline self-test (stdlib `unittest`, no network, no API key) — asserts the specific finding for each documented case. |
| `fixtures/` | A small `requirements.txt` + `package.json` with a mix of pinned and unpinned entries, for the manual demo run below. |

## The mechanism: `${CLAUDE_SKILL_DIR}` + `allowed-tools`

`${CLAUDE_SKILL_DIR}` expands to the directory containing this skill's
`SKILL.md`, regardless of whether it's installed at the personal, project,
or plugin level. Claude Code substitutes it in both the markdown body and in
`Bash(...)` rules inside `allowed-tools` frontmatter — using the identical
substituted path in both places is what lets the bundled script run
**without a permission prompt**:

```yaml
allowed-tools: Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/scan_dependencies.py *)
```

**Version gate, per the docs (verbatim):** "The `allowed-tools` substitution
for `${CLAUDE_SKILL_DIR}` requires Claude Code v2.1.129 or later. On earlier
versions the rule stays a literal `${CLAUDE_SKILL_DIR}` string and never
matches, so the command still prompts for permission." Check before relying
on this:

```bash
claude --version
```

(Checked in this build environment: `2.1.221` — above the gate. Confirm on
your own install before assuming the prompt is suppressed.)

`allowed-tools` grants clear at the end of the turn that invoked the skill,
not the whole session — re-invoking the skill re-applies the grant.

This proposal's `allowed-tools` rule is a two-token prefix (`python3
${CLAUDE_SKILL_DIR}/scripts/...`), combining the interpreter and the script
path in one rule. The docs' own worked examples only show this as two
separate patterns — a single-token script-path prefix
(`Bash(${CLAUDE_SKILL_DIR}/scripts/render.sh *)`) or a fully-open
interpreter prefix (`Bash(python3 *)`) — never literally the combination
used here. It's a straightforward extrapolation of the documented
prefix-match rule, but **not confirmed from a primary source**; verify it
live (see below). If it doesn't suppress the prompt, the documented fallback
is a `.sh` wrapper invoked with a single-token path prefix, matching the
`render.sh` pattern.

There is also an older, unresolved GitHub issue,
[anthropics/claude-code#14956](https://github.com/anthropics/claude-code/issues/14956),
reporting an `allowed-tools` rule that appears active but still prompts —
filed against v2.0.75 with the older `prefix:*` colon syntax, not the
`prefix *` space syntax the current docs and this skill use, so it may
already not apply here. Not confirmed fixed or still-reproducing on current
syntax from a primary source — worth being aware of if the live check below
doesn't behave as expected.

## What the script owns vs. what the skill owns

Per the best-practices doc's "solve, don't defer" guidance, the script
handles every expected failure explicitly and never lets Claude improvise on
a traceback:

- Nonexistent directory → `{"error": "..."}` on stdout, exit `2`.
- Malformed `package.json` → one finding with a parse-error `reason`, rest
  of the scan still completes, exit `0`.
- No manifest files found → valid JSON, empty `findings`, `count: 0`, exit
  `0` (not an error — nothing to scan is a legitimate outcome).

The skill body explicitly tells Claude to run the script and narrate its
JSON, not to re-parse `requirements.txt`/`package.json` itself — a
low-freedom, "run exactly this script" case in the best-practices doc's
terms, since the pin-detection rules are exact and deterministic.

## Installing the skill

Project-local (committed, shared with the team):

```bash
mkdir -p .claude/skills
cp -r examples/skill-script-execution/skills/scanning-dependencies .claude/skills/
```

Personal (available in every project):

```bash
cp -r examples/skill-script-execution/skills/scanning-dependencies ~/.claude/skills/
```

## Run the self-test (no API key, no network)

```bash
cd examples/skill-script-execution
python3 test_scan_dependencies.py
```

Expected: `Ran 12 tests in 0.0Xs` / `OK`, covering every acceptance-criteria
case from the research note (bare-name and range specifiers in
`requirements.txt`, caret ranges and `"latest"` in `package.json`, exact
pins of both kinds not flagged, no-manifest and nonexistent-directory
outcomes, malformed-JSON partial failure, ignored directories, and the CLI's
exit codes via subprocess).

## Run the script directly

```bash
python3 skills/scanning-dependencies/scripts/scan_dependencies.py fixtures
```

```json
{
  "scanned": ["package.json", "requirements.txt"],
  "findings": [
    {"file": "requirements.txt", "package": "numpy", "version_spec": "", "reason": "no version pin (bare package name)"},
    {"file": "requirements.txt", "package": "pandas", "version_spec": ">=2.2", "reason": "range specifier, not an exact pin"},
    {"file": "package.json", "package": "lodash", "version_spec": "^4.17.21", "reason": "range or floating version, not an exact pin"},
    {"file": "package.json", "package": "jest", "version_spec": "latest", "reason": "range or floating version, not an exact pin"}
  ],
  "count": 4
}
```

`fixtures/requirements.txt` also has `requests==2.32.3` (exact pin, not
flagged) and a `-r other-requirements.txt` include line (skipped, not
flagged); `fixtures/package.json` has `express` pinned to `4.19.2` (not
flagged). Exit code is `0`.

Against a directory that doesn't exist:

```bash
python3 skills/scanning-dependencies/scripts/scan_dependencies.py /no/such/dir
# {"error": "directory not found: /no/such/dir"}
# exit code 2
```

## Verifying the permission-prompt suppression (manual, live — not part of the automated self-test)

Whether `allowed-tools` actually suppresses the Bash permission prompt is a
live, version- and environment-dependent behavior — not something checkable
offline (the same caveat class the `skill-anatomy` and `skill-reference-files`
notes already flag for skill *triggering* itself). To confirm it manually:

```bash
mkdir -p /tmp/scan-demo && cd /tmp/scan-demo
cp /path/to/agentlab/examples/skill-script-execution/fixtures/requirements.txt .
claude
```

Then, in the session:

1. Ask *"are my dependencies pinned?"* — the skill should trigger from its
   `description` and run the script.
2. Confirm the script runs **without** a permission prompt (assuming
   `claude --version` is ≥ `2.1.129`) — this is the specific thing the docs
   claim and this build cannot verify offline.
3. Confirm Claude's summary matches the JSON: `numpy` flagged as an unpinned
   bare name, nothing else.

## Explicitly out of scope

- Proving the permission-prompt suppression works live (see above — not
  deterministically testable offline).
- Manifest formats beyond `requirements.txt`/`package.json` (e.g.
  `Cargo.toml`, `go.mod`) — a natural follow-up, not this cycle's scope.
- Any change to `examples/skill-anatomy`'s validator — a separate, standalone
  example.
