# Transcript sweep: choosing the right command in a multi-command run block

Date: 2026-09-30. Mode: demo. Backlog item: `fix (health 2026-09-21): examples/readme-transcript-check/ — sweep.py command_script() returns scripts[-1] ...`

Why this item: BACKLOG.md had no plain `[ ]` feature item left (only `[done]`, `[stranded ...]`, and health findings), and no open PRs exist (`gh pr list --state open` is empty). Most remaining `[ ]` health findings are non-buildable (no run log, session limit). This one is a concrete, reproducible defect in code we own. The other buildable one (prompt-caching-tool-loop README drift, line 292) is a separate one-line README fix and stays unclaimed (one intent per change).

## Question

How should `sweep.command_script()` pick which script's output a README's marked transcript documents when the run block above it names more than one script?

## Findings

No web research needed: this is an internal defect, and the primary source is the repo itself (read today, none of it stale).

- `examples/readme-transcript-check/sweep.py::command_script` (about line 261): finds the single marked block, takes the fenced block immediately preceding it, and returns `scripts[-1]`, the last `*.py` token.
- Its docstring claims the rule was "verified by hand against all fifteen transcript-bearing READMEs". True at the time; every one of them has one script in the run block.
- `examples/context-editing-preview/README.md` lines 46-52 break it:
  ```
  python test_preview.py           # clear_tool_uses_20250919
  python test_preview_thinking.py  # clear_thinking_20251015
  ```
  followed by the marker line `` `test_preview.py` — Expected output: `` and the 25-test transcript. The sweep runs `test_preview_thinking.py` and diffs it against `test_preview.py`'s transcript: a false DRIFT. It also files itself into the backlog via the health agent, so it wastes nightly cycles.
- The marker line is the only text that binds transcript to command. In the 15+ other READMEs the marker line is bare (`Expected output:`) or prose, and none names a `.py` file except this one (checked with `grep -n 'Expected output' examples/*/README.md`). Some marker lines are longer prose (`mcp-prompts:29`, `mcp-connect-claude-code:33,55`) and contain no `.py` token.
- Note the block also has inline `# comments`; tokens are whitespace split, so a comment mentioning a `.py` name could be picked up. Not an issue today, but the fix should ignore text after `#` on a line.

Design options considered:
1. Marker-line disambiguation (chosen): among the `.py` tokens in the run block, if the marker line (the nearest non-blank line above the transcript fence) contains the basename of exactly one of them, use that one; otherwise keep the existing "last token" rule. Additive: every README that behaved before behaves identically, since bare marker lines name nothing.
2. Refuse when more than one script is present (new `Ambiguous`-like outcome). Safer but turns a checkable README into a permanent non-check.
3. Run every script and match any. Wrong: passing by accident hides drift.

Fallback nuance: if the marker line names a `.py` file that is NOT in the run block, do not silently use it; fall through to last-token (or raise `CommandNotFound`; builder to pick and record). Recommend the fall-through, since the run block is the authority on what is runnable.

## Build proposal

Intent: make `sweep.command_script` return the script the marker line names when the run block has several, so `context-editing-preview` sweeps as `ok` instead of a false DRIFT. Out of scope: the `prompt-caching-tool-loop` README drift (line 292), README rewording, supporting non-`.py` commands, running multiple scripts per README.

Where: edit in place, `examples/readme-transcript-check/sweep.py` and `test_sweep.py`, plus a README line. No new dir, no new dependency.

Interface (signature unchanged, so no consumer breaks):
```python
def command_script(readme_text: str) -> str: ...   # same failure mode: CommandNotFound
def _marker_line(lines: Sequence[str], transcript_open: int) -> str: ...  # pure helper
def _strip_comment(line: str) -> str: ...  # drop text from first " #" / leading "#"
```

Behavior:
- Inputs: README text. Output: one script name.
- Candidates = `.py` tokens in the preceding fenced block after comment stripping.
- If the marker line contains the basename of exactly one candidate (token-boundary match, so `test_preview.py` does not match inside `test_preview_thinking.py`; the reverse is also handled since the marker for the second would name the longer one), return it.
- If it names more than one candidate or none, fall back to `candidates[-1]` (existing rule).
- No candidates: `CommandNotFound` as before.

Acceptance criteria (test first, per protocol section 6; the failing test reproduces the bug):
1. Fixture with run block `python a.py` / `python a_extra.py`, marker `` `a.py` — Expected output: `` returns `a.py`. Fails on current code (returns `a_extra.py`).
2. Substring trap: marker naming `test_preview.py` with candidates `test_preview.py` and `test_preview_thinking.py` returns `test_preview.py`; marker naming `test_preview_thinking.py` returns the longer one.
3. All existing `test_sweep.py` tests still pass unchanged (bare-marker READMEs give the last token).
4. A `.py` token in an inline `# comment` is not a candidate.
5. Real-file test: `command_script(context-editing-preview/README.md) == "test_preview.py"`.
6. End-to-end: `python3 sweep.py --only context-editing-preview` (or equivalent flag; check `_parse_args`) reports it OK, not DRIFT. It needs a scratch venv only if the example has `requirements.txt`; it does, so this step may need network for pip. If unavailable, criterion 5 stands as the offline proof and the README should say so.
7. Update the docstring rule and README of `readme-transcript-check` to describe the marker-line tie-break; update `knowledge/doc-transcript-drift.md` (already extended by the researcher).

Failure modes: a marker line naming two candidates falls back to last token (documented); no behavior change for READMEs with one script.

## Open questions

- Whether `context-editing-preview`'s second script (`test_preview_thinking.py`) has its own transcript block; the README appears to have only one marked block, so its output stays unchecked. Not in scope.
- Exact sweep CLI flag for a single example was not confirmed (builder to read `_parse_args`).
- The hn-search MCP server was unavailable this cycle; not needed for an internal fix.
