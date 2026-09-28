# Agent Skills through the raw Messages API (not Claude Code)

Every other `examples/skill-*` directory uses Claude Code's filesystem Skills
(`.claude/skills/`). This one uses the **other** Skills product. It uploads a
Skill to Anthropic through `POST /v1/skills`, attaches it to a
`messages.create()` call through `container={"skills": [...]}` plus the code
execution tool, and then checks from the response transcript that Claude
actually read `SKILL.md` inside the sandboxed container. Matching on the
Skill's name and description alone doesn't count.

From the research note:
[`research/2026-09-28-skill-api-container.md`](../../research/2026-09-28-skill-api-container.md).

## Status: offline-verified, live run NOT yet performed

This build ran without an `ANTHROPIC_API_KEY`, so **no real API call has been
made**. What is verified:

- The 38 offline self-tests below pass. They cover the frontmatter validator,
  the canary-leak precondition, and the transcript parser.
- `run_live.py` was driven through the real `anthropic` SDK 1.8.0 with a
  mocked HTTP transport (a throwaway harness, not shipped here). The SDK
  accepted the exact request shape. The upload multipart carried
  `hello-skill/SKILL.md`. **No `anthropic-beta` header was sent on any
  request.** Cleanup (list versions, delete each, delete the Skill) ran on
  both the success path and a forced `messages.create` 400.

What is **not** verified: that the real API accepts these calls without a beta
header, and that Claude really opens `SKILL.md` in the container. That is
exactly what one `python3 run_live.py` with a key settles. Until someone runs
it, the beta-header question in `knowledge/agent-skills.md` stays "docs-only,"
and this README does not claim otherwise. The fixtures in `fixtures/` are
**synthetic**, built from the SDK's block types, not recorded responses.

## How the proof works: a canary

`skills/hello-skill/SKILL.md` tells Claude to open any house-style greeting
with the codeword `VERMILION-OTTER-4471`. That codeword appears **only in the
body**:

- It is not in `name` or `description`. The API places those in the system
  prompt up front, so they could be answered without any file read.
- It is not in the prompt (`"Please write a house-style greeting for Ada."`).

`check_experiment_design` enforces both conditions before any upload. So the
codeword can only reach Claude's reply through one route: a file read in the
container. `load_verdict` returns `LOADED` only when all three of these hold:

1. a `server_tool_use` (`bash_code_execution` / `text_editor_code_execution`)
   whose input names `SKILL.md`,
2. its paired result (matched on `tool_use_id`) succeeded and its output
   contains the canary, and
3. a `text` block **after** that result contains the canary.

Otherwise it returns `NO_SKILL_READ` or `CANARY_NOT_IN_REPLY`. Malformed
responses raise `TranscriptError` instead of being counted as "not loaded".

## Files

| File | One job |
|------|---------|
| `skills/hello-skill/SKILL.md` | The Skill that gets uploaded. The canary is in its body. |
| `frontmatter.py` | Pure. Parses SKILL.md into a `SkillMd`, which cannot exist unless it meets the API's upload constraints (`name` ≤64, `[a-z0-9-]`, no "anthropic"/"claude"; `description` non-empty, ≤1024, no XML tags). |
| `experiment.py` | Pure. The canary, the prompt, and `check_experiment_design` (canary only in the body). |
| `load_evidence.py` | Pure. `load_verdict(response, "SKILL.md", canary) -> Verdict` and `skill_was_loaded(...) -> bool`. |
| `run_live.py` | Imperative shell. Reads the key, validates, uploads, calls, saves the transcript, deletes the Skill. |
| `test_*.py` | 38 offline tests, stdlib only. |
| `fixtures/*.json` | Synthetic responses: loaded / no code execution / read but no canary in the reply. |

## Run the offline self-test (no key, no network, nothing to install)

```bash
cd examples/skill-api-container
python3 -m unittest discover -v
```

Actual result from this build: `Ran 38 tests ... OK`.

## Run the live check (real API calls; costs a little)

```bash
cd examples/skill-api-container
pip install -r requirements.txt        # anthropic>=1.8.0,<2
export ANTHROPIC_API_KEY=sk-ant-...
python3 run_live.py
```

The script makes these calls:

1. `client.skills.create`. The new `skill_id` is printed to stderr
   immediately, so you can delete it by hand if a later step dies.
2. One `client.messages.create` on `claude-sonnet-5` with
   `code_execution_20250825`. The raw response is saved to
   `transcripts/<UTC timestamp>-live.json`.
3. Cleanup in a `finally` block. It lists and deletes each version, then
   deletes the Skill.

Exit code 0 means `LOADED`. Exit code 1 means the call ran but the transcript
doesn't prove a read, and the verdict is printed. Without a key the script
prints `SKIPPED (not a pass)` and exits 0 with no network activity.

**Cost:** token cost for one Sonnet 5 call at up to 2048 output tokens ($2/$10
per MTok), which comes to a few cents at most. Code execution is billed by
container time with a 5-minute minimum, but the docs list 1,550 free hours per
organization per month. So this run is cheap, **not guaranteed free**, if your
org has used that allowance elsewhere.

**After a live run:** commit the transcript, then update
`knowledge/agent-skills.md`'s "Beta-header status" line with the result. If
the calls succeed, the result is "no beta header needed, verified live."

## Known limits

- `pause_turn` (a long server-tool turn) raises after the transcript is saved.
  Continuing a paused turn, reusing a container, and pre-built
  `pptx`/`xlsx`/`docx`/`pdf` Skills are out of scope (research note §5).
- The frontmatter reader is intentionally minimal. YAML block scalars and
  nested values are rejected as "unsupported by this validator" rather than
  guessed at.
- `load_verdict` is strict. If Claude reads the file in pieces and the canary
  line comes from a command that doesn't name `SKILL.md`, the verdict is
  `NO_SKILL_READ`. That can produce a false negative, never a false positive,
  and the saved transcript shows what happened.
- `claude-sonnet-5` was chosen as the cheapest model in
  `knowledge/anthropic-models.md` that I'm confident supports code execution.
  Haiku 4.5 support for `code_execution_20250825` was not checked. It's a
  one-line `MODEL` change if it does.
- Deleting versions before the Skill is defensive. The SDK docstring doesn't
  say whether `skills.delete` cascades, and deleting versions first is correct
  either way.
