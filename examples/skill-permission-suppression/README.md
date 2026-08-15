# Does `${CLAUDE_SKILL_DIR}` + `allowed-tools` actually suppress the permission prompt?

**Answer, verified live on `claude` 2.1.221 (2026-08-16): yes.** A bundled
script whose exact `${CLAUDE_SKILL_DIR}`-substituted command appears in both the
skill body and an `allowed-tools: Bash(${CLAUDE_SKILL_DIR}/scripts/mark.sh *)`
rule ran in a headless session where **no approval was possible** — and the
byte-identical command, in a byte-identical skill with that one frontmatter line
removed, was denied. Evidence below, transcripts in `fixtures/`.

`examples/skill-script-execution` shipped this pattern but its README says the
no-prompt claim was "**not confirmed from a primary source**", and
`knowledge/agent-skills.md` said "verify with a real session." This example is
that verification, checked mechanically rather than by eyeballing a transcript.

From the research note:
[`research/2026-08-16-skill-permission-suppression.md`](../../research/2026-08-16-skill-permission-suppression.md).

## What's here

| File | What it is |
|------|-----------|
| `skills/verify-allow/` | Test skill **with** `allowed-tools: Bash(${CLAUDE_SKILL_DIR}/scripts/mark.sh *)` — the docs' own single-token, space-suffix form. |
| `skills/verify-deny/` | The control: byte-identical body and script, **no** `allowed-tools` field at all. |
| `skills/*/scripts/mark.sh` | Writes one line to the path in `$1`. The whole point: a real on-disk fact, independent of anything the model claims it did. |
| `assert_transcript.py` | Pure verifier: `find_bash_call(events, substring) -> Outcome` and `judge(expect, outcome, sentinel_exists) -> Verdict`. No I/O outside `main()`. |
| `test_assert_transcript.py` | 19 offline self-tests — no live call, no key, no cost. |
| `fixtures/allow_transcript.jsonl`, `fixtures/deny_transcript.jsonl` | The two real `stream-json` transcripts this parser was built against, captured during this build. Not synthesized from docs. |
| `run_e2e.sh` | Orchestrator (impure shell): throwaway project dir per attempt, invokes the real `claude` CLI twice, checks both signals. |

Only one variable differs between the two skills. Verify that yourself:

```bash
cd examples/skill-permission-suppression/skills
diff verify-allow/scripts/mark.sh verify-deny/scripts/mark.sh   # identical
diff <(tail -n +2 verify-allow/SKILL.md) <(tail -n +2 verify-deny/SKILL.md)
# differs only in: name, description, and the allowed-tools line
```

## Run the offline self-test (no API key, no network, no cost)

```bash
cd examples/skill-permission-suppression
python3 test_assert_transcript.py
```

Expected: 19 `ok` lines and `All 19 self-tests passed.` The first five run
`find_bash_call` / `judge` against the two real recorded transcripts; the rest
are one-field mutations of those real shapes, one per `Outcome`, per
`VerdictKind` (including `CONTRADICTION`), and per loud `TranscriptError`.

## Run the live end-to-end test (real API calls, ~$0.05)

```bash
cd examples/skill-permission-suppression
./run_e2e.sh
```

Prerequisites: the `claude` CLI on `PATH`, and either `ANTHROPIC_API_KEY` set or
an already-logged-in Claude Code session. Actual output from this build's run:

```
--- verify-allow: attempt 1/2 ---
PASS [SUCCEEDED]: allowed-tools rule matched the body command: Bash call ran with
no approval available, and the script's sentinel file exists
--- verify-deny: attempt 1/2 ---
PASS [DENIED]: control case denied as expected: without allowed-tools the
identical command was permission-denied and no sentinel file was written

run_e2e.sh: PASS -- allowed-tools suppressed the prompt for the bundled
script (verify-allow ran, sentinel written) and its absence did not
(verify-deny denied, no sentinel). Both signals agreed in both cases.
```

Exit code `0`, both skills passing on attempt 1 of 2.

## How the test works

`claude -p` has no way to ask a human anything, and headless mode's baseline is
`default` (Manual) permission mode, which requires approval for Bash. Per the
permission-modes doc, an unapproved call in that situation simply never
executes — "the action doesn't run and Claude keeps working." So the question
"was the prompt suppressed?" reduces to "did the script's side effect happen?",
with no prompt-answering machinery needed.

Per skill, `run_e2e.sh`:

1. `mktemp -d`s a **fresh** project dir *per attempt* and copies in that one
   skill under `.claude/skills/<name>/`. The sentinel's absence is only
   evidence if nothing earlier could have created it.
2. Runs `claude -p "/<skill> <sentinel-path>" --permission-mode default --model
   haiku --output-format stream-json --verbose`. Invoking the skill explicitly
   as `/<skill>` (plus `disable-model-invocation: true` in frontmatter) removes
   the separate, model-dependent question of whether Claude *chooses* to trigger
   the skill — that is not what this example tests.
3. Checks **both** signals: `assert_transcript.py`'s classification of the
   transcript, and `Path(sentinel).exists()` on the real filesystem.

Three things keep the comparison honest:

- **No `--bare`.** It skips skill auto-discovery, i.e. the thing under test.
- **The permission mode is asserted, not assumed.** Ambient user settings can
  set `defaultMode` to `auto`, under which a classifier could approve the Bash
  call for reasons that have nothing to do with `allowed-tools`.
  `run_e2e.sh` passes `--permission-mode default` explicitly *and*
  `assert_transcript.py` re-reads `permissionMode` from the transcript's
  `system`/`init` event, exiting `4` (invalid run) rather than judging if it is
  anything other than `default`.
- **Disagreement is a finding, not noise.** If the transcript says the command
  ran but no sentinel appeared (or vice versa), `judge()` returns
  `CONTRADICTION`, `run_e2e.sh` exits `3` immediately and does **not** retry.
  Retrying could hide the inconsistency behind a luckier second attempt, and
  picking one signal to trust is exactly the mistake this example exists to
  avoid. The consistency rule (`SUCCEEDED` ⟺ sentinel exists) is checked before
  and independently of which skill produced the transcript.

Exit codes: `0` pass, `1` failure (retryable), `3` transcript/filesystem
contradiction, `4` invalid run.

## The evidence

Both transcripts start with `"permissionMode": "default"` and
`"apiKeySource": "none"`, on `claude_code_version` 2.1.221, model
`claude-haiku-4-5-20251001` (resolved from the `haiku` alias).

**`verify-allow`** — the command the model issued (note `${CLAUDE_SKILL_DIR}`
expanded to the real per-attempt skill dir, and the rule matched it):

```
/private/tmp/.../.claude/skills/verify-allow/scripts/mark.sh /tmp/.../sentinel.txt
```

`fixtures/allow_transcript.jsonl` line 18 and line 24:

```json
{"tool_use_id": "toolu_01TjQ...", "type": "tool_result", "content": "(Bash completed with no output)", "is_error": false}
"permission_denials": []
```

…and `sentinel.txt` existed on disk afterwards, containing `ran at <UTC>`.

**`verify-deny`** — the same command, from the same body, with the
`allowed-tools` line removed. `fixtures/deny_transcript.jsonl` line 10 and
line 21:

```json
{"type": "tool_result", "content": "This command requires approval", "is_error": true, "tool_use_id": "toolu_01KEz..."}
"permission_denials": [{"tool_name": "Bash", "tool_use_id": "toolu_01KEz...", "tool_input": {"command": ".../verify-deny/scripts/mark.sh /tmp/.../sentinel.txt"}}]
```

…and no `sentinel.txt` was created. The model's final text was *"The command
requires approval to run… Please approve this command to proceed."* — to nobody,
in a session with no prompt channel. That is what the docs' "the action doesn't
run and Claude keeps working" looks like in `stream-json`.

Two independent denial signals therefore exist, and `assert_transcript.py`
requires them to agree: the `permission_denials` entry on the final `result`
event (the CLI's own accounting) and the `tool_result` block's `is_error` flag.
A `tool_result` error whose id is *not* in `permission_denials` is **not**
reported as `DENIED` — it raises `TranscriptError`, because that shape means a
broken script or environment, and calling that a permission finding would be the
worst possible way for this example to be wrong.

Also worth recording, since the research note left the shape open: a denied
headless tool call still produces a normal `tool_result` (there is no distinct
"denied" event type), and the final `result` event still reports
`"subtype": "success"` with `"is_error": false` — **the run as a whole does not
fail**. Anything asserting on the top-level result alone would have concluded
the deny case succeeded.

## Cost

Four live calls were made during this build (two exploratory captures that
became the fixtures, two from `run_e2e.sh`), totalling **$0.0968**
(`total_cost_usd`: $0.0231, $0.0235, $0.0244, $0.0258). `--bare` would be
cheaper but is unusable here, so every call loads full ambient context — the
same non-`--bare` situation `examples/mcp-connect-claude-code` documents at
$0.026/call. A normal `./run_e2e.sh` is 2 calls (~$0.05); worst case with
`MAX_ATTEMPTS=2` per skill is 4 calls (~$0.10), never unbounded. The offline
`test_assert_transcript.py` costs nothing.

## Scope: what this does and does not settle

Settled, from a primary source, on 2.1.221:

- The docs' flagship **single-token, space-suffix** form
  `Bash(${CLAUDE_SKILL_DIR}/scripts/x.sh *)` does suppress the Bash permission
  prompt for the matching bundled script, in a **project-level** skill, in a
  throwaway directory with no prior trust relationship (confirming the docs'
  "workspace trust doesn't gate this field").
- The suppression is attributable to `allowed-tools` specifically: removing only
  that line flips the outcome to denied.
- `disable-model-invocation: true` does not interfere with the grant (the
  research note flagged this interaction as undocumented; the allow case works
  with it set).

**Not** settled here, deliberately:

- **The two-token interpreter form**
  `Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/foo.py *)` that
  `examples/skill-script-execution` actually ships. This build tested the
  single-token form only, to keep the variable count at one. Still unverified.
- **Issue [#14956](https://github.com/anthropics/claude-code/issues/14956)'s
  colon syntax** (`Bash(cmd:*)`). Not re-litigated; a passing result here is not
  evidence about that syntax.
- **Whether the model chooses to trigger the skill** on its own — sidestepped by
  explicit `/name` invocation, and already scoped out in
  `knowledge/agent-skills.md` as not deterministically testable.
- **Personal (`~/.claude/skills/`) and plugin install locations.** Project scope
  only was tested.
- **Whether `$ARGUMENTS` is substituted in a `SKILL.md` body.** The body uses
  it, but the sentinel path is also present in the prompt (`/verify-allow
  <path>`), and the transcript does not reveal which route the model got the
  path from — it never echoes the expanded body. The test does not depend on the
  answer; do not read this example as confirming `$ARGUMENTS` in skills.
- **A live-exercised `CONTRADICTION` path.** Both live runs were consistent, so
  that branch is covered by mutation tests only, not by a real transcript.
