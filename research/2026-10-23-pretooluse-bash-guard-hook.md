# PreToolUse hook that guards Bash (2026-10-23)

## Question
What exactly is Claude Code's PreToolUse hook contract (stdin payload, block signalling, config), and what is the smallest honest deny-list guard we can build and test offline?

## Findings
Primary source: [Claude Code hooks reference](https://code.claude.com/docs/en/hooks) (fetched 2026-10-23; current). I read characters 0-200000 of ~274k; the last ~74k was not read. The hn-search MCP server failed to connect this session, so no HN reception data. One WebSearch for community gotchas returned only secondary blogs.

- **stdin payload** (one JSON object): `session_id`, `prompt_id`, `transcript_path`, `cwd`, `permission_mode`, `hook_event_name: "PreToolUse"`, `tool_name`, `tool_input`, `tool_use_id`. For Bash, `tool_input` = `{command, description?, timeout?, run_in_background?}`.
- **Two ways to block, and they differ:**
  1. `exit 2`: tool call blocked; **stderr** is shown to Claude as the reason. If JSON is also printed with a blocking decision, its reason wins. With exit 2, JSON cannot override the block (even `allow` does not let it through).
  2. `exit 0` + stdout JSON: `{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"..."}}`. `hookEventName` is required. `permissionDecision` values: `allow | deny | ask | defer`. For `deny`, the reason is shown to Claude. Stdout must be only that one JSON object (whitespace around is fine).
- **Gotcha, exit 1 is NOT a block.** "Exit code 1 without valid JSON output is a non-blocking error"; the action proceeds. Policy hooks must use exit 2 (or JSON deny). Crashes/timeouts of a PreToolUse command hook also don't block, unless `"onFailure": "block"` is set (documented as v2.1.295+; not verified on a real install). So a guard script that crashes on bad input fails open by default; our script should catch everything and map internal errors to a deny itself.
- **Silence is not approval**: exit 0 with no output means "no decision", the normal permission flow continues. Our guard should only ever deny or stay silent; never emit `allow` (that could skip a user's prompt). Hook `allow` never overrides deny/ask permission rules; multiple hooks: deny > defer > ask > allow.
- Deprecated top-level `decision`/`reason` (`approve`/`block`) on PreToolUse; use `hookSpecificOutput`.
- **Config** (`.claude/settings.json`, `.claude/settings.local.json`, or `~/.claude/settings.json`): `hooks.PreToolUse[].{matcher:"Bash", hooks:[{type:"command", command:"...", args:[]}]}`. Matcher of only letters/digits/`_`/`-`/`,`/`|` is exact-match; other chars make it an unanchored JS regex. Optional `if` field (permission-rule syntax) is documented as best-effort, so do the matching inside the script, not in `if`.
- Docs advise using the permission system (deny rules) for hard enforcement; hooks are the programmable layer. Whether a hook deny holds under `bypassPermissions` is **not stated** in the sections I read; blogs ([example](https://pasqualepillitteri.it/en/news/1832/claude-code-dangerously-skip-permissions-pretooluse-hooks-2026), secondary, unverified) claim it does.
- **Classifier hazard**: substring/glob matching conflates `rm -rf node_modules` with `rm -rf /`. Compound forms (`ls && rm -rf ~`, `bash -c "..."`, `$(...)`, `sudo`, `rm -fr`, `rm -r -f`, `rm --recursive --force`, `/*`, `$HOME`, `~/`) must be handled or explicitly documented as not covered. A tokenizing classifier (shlex split on `;`, `&&`, `||`, `|`, newlines, then inspect each simple command) is a reasonable small step, and the README must state it is a guardrail against mistakes, not a security boundary.

## Build proposal
**Directory:** `examples/pretooluse-bash-guard/` (checked: not in `ls examples/`, no open PR other than #62, which is unrelated pipeline work).

**Intent.** Show Claude Code's deterministic veto over what the model does. Out of scope: Edit/Write guards, `updatedInput` rewriting, `ask`/`defer`, http hooks, security-boundary claims, a live Claude session in the test.

**Files** (Python 3 stdlib only, no deps; or bash+jq not needed):
- `classify.py`: pure `classify(command: str) -> Verdict` where `Verdict = Allow | Deny(reason: str)` (frozen dataclasses). No I/O.
- `hook.py`: shell. Reads stdin, parses JSON, calls `classify`, emits output. Pure helper `decide(payload_text: str) -> tuple[int, str, str]` returning `(exit_code, stdout, stderr)` so the test needn't spawn processes (one subprocess smoke test also).
- `fixtures/*.json`: recorded payloads in the documented shape.
- `test_guard.py` (unittest) and `settings.example.json` showing the registration (matcher `"Bash"`, `command` pointing at `hook.py`).
- `README.md`: contract, deny-list, known bypasses, how to install.

**Behavior.**
- Deny-list (named constants, defined once): (1) `rm` with recursive flag (any of `-r -R -rf -fr --recursive`, split flags too) whose target resolves to `/`, `/*`, `~`, `~/`, `~/*`, `$HOME`, `${HOME}`; (2) `git push` with `--force`/`-f`/`--force-with-lease`/`+main` refspec targeting `main` (or `master`) explicitly, or a bare force push while... keep to explicit `main`/`master` in args; document that bare `git push -f` is not covered (can't know the branch statelessly).
- Commands are split on `;`, `&&`, `||`, `|`, newline; leading `sudo`/`env VAR=x` stripped; `bash|sh -c "<string>"` recursed one level. Unparseable quoting (shlex error) -> Deny (fail closed, reason says so).
- Allow = exit 0, empty stdout (no decision, NOT `allow` JSON).
- Deny = exit 0 + the `hookSpecificOutput` deny JSON with a reason naming the rule and a safer alternative (or exit 2 + stderr; pick JSON and also assert on one exit-2 variant is unnecessary: choose one, JSON deny, documented).
- Malformed stdin JSON, non-Bash `tool_name`, or missing `tool_input.command`: malformed -> deny with reason (fail closed, since exit 1 would fail open); non-Bash tool -> silent allow.

**Acceptance criteria (test_guard.py).**
1. `rm -rf /`, `rm -fr ~`, `rm -r -f $HOME`, `rm --recursive --force /*`, `sudo rm -rf /`, `ls && rm -rf ~`, `bash -c 'rm -rf /'` each yield deny JSON that parses, has `hookEventName == "PreToolUse"`, `permissionDecision == "deny"`, non-empty reason.
2. `git push --force origin main`, `git push -f origin master`, `git push origin +main` deny.
3. Allowed with empty stdout and exit 0: `rm -rf node_modules`, `rm -rf ./build`, `rm file.txt`, `git push origin feature`, `git push --force origin feature`, `ls ~`, `echo "rm -rf /"` (quoted literal; document if the tokenizer can't tell and instead choose to deny, but the test must pin whichever is chosen).
4. Malformed JSON stdin denies; non-Bash tool allows silently; stdout on deny is exactly one JSON object.
5. Smoke test: `subprocess.run([python, hook.py], input=fixture)` returns the same as `decide`.
6. README lists at least three known non-covered bypasses (e.g. `python -c "shutil.rmtree('/')"`, variable indirection, `find / -delete`) and verified-vs-unverified claims (bypassPermissions behavior untested).

**Failure domain.** The hook only runs locally per tool call; if it crashes, Claude Code proceeds (fail open) unless `onFailure: block`, hence the catch-all in `hook.py`.

## Open questions
- Does a hook deny hold under `--dangerously-skip-permissions`/`bypassPermissions`? Not confirmed in official text I read; builder may run a live check only if cheap, else record as unverified.
- `onFailure: "block"` availability (docs say v2.1.295+); not tested.
- Last ~74k chars of the hooks page unread.
