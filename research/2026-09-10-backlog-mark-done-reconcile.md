# Advancing a shipped backlog claim from `[building]` to `[done #N]`

**Date:** 2026-09-10
**Backlog item:** `## Health-check findings` — *"fix (health 2026-09-02): BACKLOG.md:24 "Parallel
specialist execution in the orchestrator" marked [building], shipped in PR #37 … never advanced to
[done #37]"* (and its identical sibling for PR #38). Marked `[researching]`.

## Question

When the nightly pipeline ships an increment, what advances its `BACKLOG.md` entry from
`- [building] <text>` to `- [done #N] <text>` — and if the answer is "nothing", what is the
smallest reconciler that closes that gap the same way `reconcile_stranded_claims` closed the
failed-cycle gap?

## Findings

### Nothing in the pipeline performs the `[building] → [done #N]` transition

Read of `.pipeline/run.sh` (lines 180–770) and `.pipeline/backlog.sh` (whole file), 2026-09-10:

- `run_cycle` (`run.sh:285`) clears `logs/last-review.md` / `logs/last-pr.txt`, then runs
  research → build → review → verdict-gate → maintain → auto-merge. The **builder** is the only
  phase that edits the claim: `agentlab-builder.md:51` — *"Update `BACKLOG.md`: mark the item
  `[building]`."* No later phase edits it back.
- The **maintainer** opens the PR and *"writes the PR number to `logs/last-pr.txt` and stops"*
  (`PIPELINE.md:48`, `agentlab-maintainer.md`). It never touches `BACKLOG.md`.
- **Auto-merge** (`run.sh:333–352`) is deterministic bash: `gh pr view … --json mergeable`, then
  `gh pr merge "$pr_num" --merge --delete-branch`. It logs `PR #N auto-merged` and `return 0`.
  It never touches `BACKLOG.md`.
- After the cycle, `run_cycle`'s caller runs `snapshot_dirty_main`, `reset_to_clean_main`,
  `scrub_artifacts`, then `reconcile_stranded_claims "after cycle $k"` (`run.sh:679`).
  `reconcile_stranded_claims` (`run.sh:564`) scans **only** `refs/heads/cycle/*-unshipped-*` and
  `refs/remotes/origin/cycle/*-unshipped-*` — the snapshot branches a *failed* cycle leaves — and
  its only rewrite is `- [ ] <key>` → `- [stranded <branch>] <key>` via `backlog_apply_stranded`
  (`backlog.sh:174`). It never looks at a *merged PR* and never touches a `[building]` line.
- Post-loop (`stock_backlog "post-loop"`, health, pipeline-observer) touches nothing relevant.

So a **successful** cycle merges its own `- [building] <text>` line onto `main` verbatim, and it
stays `[building]` forever. The pipeline-observer eventually notices and files a
`## Health-check findings` item (that is exactly finding 217/218), but no step *fixes* it.

### The intended state machine has this arrow, unenforced

`knowledge/pipeline-claim-lifecycle.md:9–21` draws:

```
[ ]  --researcher-->  [researching]  --builder-->  [building]
     --maintainer opens PR, human/auto merges-->   [done #N]
```

and notes the exclusion property *"is true only when the cycle ships … and it has no
enforcement."* That note's **Failure 1** is about a *failed* cycle silently releasing `[ ]`.
The `[building] → [done #N]` arrow on a *successful* cycle is a **separate** unenforced
transition, not yet written up. `reconcile_stranded_claims` (2026-08-13, PR #28) fixed Failure 1;
the symmetric fix for this one does not exist.

### Evidence it has bitten repeatedly

- `BACKLOG.md:8–12` — `[done #17]` carries the parenthetical *"marked done 2026-08-09 … backlog
  entry was stale."* Hand-corrected.
- `BACKLOG.md:66` — `[done #33]`; finding 218 calls this *"the 2026-08-16 PR #33 failure mode."*
- `BACKLOG.md:24` — `- [building] Parallel specialist execution…`; PR #37 merged 2026-09-02
  (`git log`, `gh pr view 37`), still `[building]`.
- `BACKLOG.md:36` — ``- [building] `thinking` blocks in the streaming accumulator…``; PR #38
  merged 2026-09-02, still `[building]`.
- Findings 217 and 218 in `## Health-check findings` are the pipeline-observer's filing of the
  last two.

At least four occurrences; every prior one was fixed by a human editing `BACKLOG.md` by hand.

### The fix mirrors an architecture the repo already has

`reconcile_stranded_claims` established the split this increment should copy exactly:

| Concern | Where | Tested by |
|---|---|---|
| decide the rewrite (pure, no I/O) | `.pipeline/backlog.sh` | `.pipeline/test_backlog.sh` (bash 3.2, `assert_eq`, `write_fixture`, case IDs C1–C23) |
| git/gh plumbing | `.pipeline/run.sh` | read-only greps of `run.sh` inside the same suite (C12, C23) |

Reusable, already-tested helpers in `backlog.sh` this increment can call unchanged:

- `backlog_claimed_line <diff>` (`backlog.sh:122`) — pulls the first `+- [building] ` /
  `+- [researching] ` line out of a unified diff of `BACKLOG.md`. Tested as C15/C16.
- `backlog_claim_key <line>` (`backlog.sh:145`) — strips the marker to the bare item text,
  **brackets escaped** so `[building]` is not read as a glob character class (the trap called out
  at `backlog.sh:70–72` and `pipeline-claim-lifecycle.md:70`). Tested as C17.

`backlog_apply_stranded` (`backlog.sh:174–216`) is the line-for-line structural template for a
new `backlog_mark_done`: read the file, rewrite in place only if there is one line to change,
distinct return codes so *"already handled"* is never reported as *"could not find"*, no temp file
left on any path.

### Recovering the shipped item's text after `--delete-branch`

`gh pr merge --merge` creates a **two-parent merge commit**; `--delete-branch` removes the branch
*ref* but the branch-tip commit stays reachable as the merge commit's second parent
([git-scm merge docs](https://git-scm.com/docs/git-merge),
[GitHub PR merges reference](https://docs.github.com/en/pull-requests/reference/pull-request-merges),
searched 2026-09-10). `gh pr view <n> --json headRefOid` returns that tip SHA
([gh pr view field list](https://cli.github.com/manual/gh_pr_view), 2026-09-10 — `headRefOid`,
`mergeCommit`, `mergedAt` are all present).

So the plumbing half can reproduce the PR's `BACKLOG.md` change with the **same technique
`reconcile_stranded_claims` already uses** — only the branch source differs:

```
oid=$(gh pr view "$pr_num" --json headRefOid -q .headRefOid)
base=$(git merge-base main "$oid")
git diff "$base" "$oid" -- BACKLOG.md   # contains '+- [building] <key>'
```

then `backlog_claimed_line` → `backlog_claim_key` → `backlog_mark_done BACKLOG.md <key> <pr_num>`.
This needs `main` (the merge commit) fetched locally first so `$oid` is a retained, reachable
object — `run_cycle` already pulls in `reset_to_clean_main`, but the reconcile should run before
that, so it must do its own `git fetch origin main` (or `git pull`) first.

**Alternative considered:** have the maintainer write the claimed item text to
`logs/last-claim.txt` next to `logs/last-pr.txt`. Simpler plumbing, but it adds a responsibility
to an agent def and a second handoff file to `rm -f` in `run_cycle`. The git-diff path needs
**no agent change** and reuses three already-tested helpers, so it is the recommendation; the
handoff file is the fallback if the object-retention detail proves fragile on the box.

### Industry precedent

Jira and Azure DevOps both ship "PR merged → transition work item to Done" automations, and both
guard idempotency with a condition (*"no other open PRs for this issue"*) rather than assuming the
rule fires once
([Atlassian devops-pr-merged template](https://www.atlassian.com/software/jira/automation-template-library/devops-pr-merged),
[Azure Boards auto-complete](https://learn.microsoft.com/en-us/azure/devops/boards/work-items/auto-complete-work-items-pull-requests?view=azure-devops),
searched 2026-09-10). The reconciler-with-distinct-return-codes shape below is the same idea.

### Not relevant here

This increment touches no Anthropic API/SDK code — pure `BACKLOG.md` text plus bash pipeline
plumbing — so the `claude-api` skill is not engaged.

## Build proposal

A pipeline-hygiene increment in **`.pipeline/`** (not a new `examples/` directory) — the same
place PR #26 and PR #28 landed. It extends `.pipeline/backlog.sh`, `.pipeline/run.sh` and
`.pipeline/test_backlog.sh`, backfills two stale `BACKLOG.md` lines, and extends one knowledge
note. Its self-test is `bash .pipeline/test_backlog.sh`.

### Layer 1 — Intent

Give the pipeline one step that rewrites a backlog item from `- [building] <text>` to
`- [done #N] <text>` on `main` once its PR has auto-merged, so a shipped increment's queue entry
stops reading as in-progress — the symmetric partner to `reconcile_stranded_claims`.

**Out of scope:**
- PRs merged by a human *after* auto-merge declined (conflict / `MERGEABLE=UNKNOWN`). A periodic
  `gh pr list --state merged` sweep with text↔PR matching is a separate, larger follow-up item.
- Any change to how `[ ]` → `[researching]` → `[building]` happens.
- `reconcile_stranded_claims` and `backlog_apply_stranded` — disjoint concern (failed cycles,
  `[ ]` → `[stranded]`), left untouched.
- The `## Health-check findings` filing mechanism (`backlog_file_health_finding`).
- Squash/rebase merge strategies — auto-merge uses `--merge`, and that is assumed.

### Layer 2 — Behavioral spec

**`backlog_mark_done <path> <key> <pr_num>`** — pure, in `backlog.sh`, no I/O beyond `<path>`:

- **Inputs:** `path` = `BACKLOG.md` path; `key` = exact item text (everything after the marker),
  matched **literally** (no globbing — same bracket-escape discipline as `backlog_claim_key`);
  `pr_num` = digits only, no `#`.
- **Output:** the single line `- [building] <key>` becomes `- [done #<pr_num>] <key>`, in place.
  No other line changes. On any "nothing to do" path the file is left byte-identical and its
  mtime untouched (so `git status` stays clean).
- **Invariants:** rewrites at most one line; idempotent (a second call is a no-op); never leaves a
  `<path>.*.$$` temp file on any path, success or failure.
- **Failure modes / return codes** (mirroring `backlog_apply_stranded` — distinct codes because
  `run.sh` logs them differently and *"already done"* must never read as *"could not find"*):
  - `0` — rewritten: `- [building] <key>` → `- [done #<pr_num>] <key>`
  - `1` — `<path>` is not a readable **and** writable regular file, OR `pr_num` does not match
    `^[0-9]+$`, OR the `mv` of the rebuilt file failed. Nothing on stdout; message on stderr; no
    temp file left.
  - `2` — no line equals `- [building] <key>` (item reworded, removed, or never reached
    `[building]`). File untouched. A human must look.
  - `3` — nothing to do: a line `- [<other>] <key>` already exists that is not `[building]`
    (already `[done #M]`, `[stranded <branch>]`, `[researching]`). File untouched.
- **Acceptance (offline test cases, bash 3.2, extend `test_backlog.sh`; new section "Shipped
  claims (C24–C32)" after "Stranded claims (C15–C23)"):**
  - **C24** — fixture has `- [building] <k>` → rc 0; that line now `- [done #123] <k>`; every
    other line byte-identical.
  - **C25** — feed C24's output back in → rc 3; file byte-identical (idempotent).
  - **C26** — `<k>` absent entirely → rc 2; file untouched.
  - **C27** — line already `- [done #99] <k>` → rc 3; file untouched (no double-mark, no
    renumber).
  - **C28** — `pr_num = "abc"` → rc 1; file untouched.
  - **C29** — `<k>` contains backticks, brackets, parens (real BACKLOG shape, e.g. the
    ``[building] `thinking` blocks…`` line) → matched literally → rc 0.
  - **C30** — `chmod 000` path → rc 1; `ls <dir>` shows no leftover temp file. (Skip when running
    as root, like C11b.)

**`reconcile_shipped_claim <pr_num>`** — git/gh plumbing, in `run.sh`, called once from
`run_cycle` immediately after `gh pr merge` succeeds and before that function returns:

- Resolves `oid = gh pr view <pr_num> --json headRefOid -q .headRefOid`; `git fetch origin main`;
  `base = git merge-base main "$oid"`; `diff = git diff "$base" "$oid" -- BACKLOG.md`; then
  `backlog_claimed_line "$diff"` → `backlog_claim_key` → `backlog_mark_done BACKLOG.md <key>
  <pr_num>`. On rc 0: `git add BACKLOG.md && git commit -m 'chore(backlog): mark shipped claim
  done (#<pr_num>)' && git push origin main`.
- **Always returns 0.** A `gh`/`git`/network failure, an empty diff, or `backlog_mark_done` rc
  1/2/3 is logged loudly and the night continues — identical posture to
  `reconcile_stranded_claims` (any uncommitted edit is rescued by `snapshot_dirty_main`).
- **Ordering:** inside `run_cycle`, after the `PR #N auto-merged` log line, before the caller's
  `snapshot_dirty_main`/`reset_to_clean_main` — so the edit is on `main` before the next cycle's
  researcher reads `BACKLOG.md`, and so the caller then carries a clean `main` forward.
- **Acceptance:**
  - **C31** (wiring — read-only grep of `run.sh`, same style/caveat as C12 and C23): a
    `reconcile_shipped_claim` definition exists, and `run_cycle` calls it on a line that comes
    *after* the successful `gh pr merge` / `PR #$pr_num auto-merged` line and *before* the
    `snapshot_dirty_main "cycle $k of $TS"` call.
  - **C32** (extend C13): `bash -n` clean on `run.sh`, `backlog.sh`, `test_backlog.sh`.
  - **Integration (documented in `test_backlog.sh` as a comment block, not run — the suite has no
    network):** fixture `- [building] X`, a fake `gh` on `PATH` returning a known SHA, and a real
    local diff → the line becomes `- [done #<n>] X`; a second run logs rc 3 and pushes nothing.

**One-time backfill (same PR):**
- `BACKLOG.md:24` `- [building] Parallel specialist execution in the orchestrator.` →
  `- [done #37]`
- `BACKLOG.md:36` ``- [building] `thinking` blocks in the streaming accumulator.`` →
  `- [done #38]`
- The two `## Health-check findings` lines (217 `[researching]` — this cycle's claim — and 218):
  resolved by this increment. 217 advances via the new `reconcile_shipped_claim` after this PR
  merges; mark 218 `[done #<this PR>]` in-PR with a one-line note, or leave both to the
  reconciler — reviewer's call. Either way the subject is closed.

**"It works" — whole-increment acceptance:**
1. `bash .pipeline/test_backlog.sh` → `=== summary: N passed, 0 failed ===`, including C24–C32.
2. `bash -n .pipeline/run.sh .pipeline/backlog.sh .pipeline/test_backlog.sh` — clean.
3. On the resulting tree, `grep -c '^- \[building\] ' BACKLOG.md` returns 0 (both stale entries
   backfilled, nothing else regressed). *Caveat:* only meaningful between cycles — a live run
   legitimately has this cycle's own claim at `[building]`; the test asserts on fixtures, not the
   live file.
4. `knowledge/pipeline-claim-lifecycle.md` has a new dated subsection for this gap + fix, and
   `knowledge/INDEX.md`'s `[[pipeline-claim-lifecycle]]` line mentions it; all wikilinks resolve.

### Layer 3 — Interfaces (no bodies)

`.pipeline/backlog.sh`:

```sh
# Named once — run.sh logs it, the self-test asserts on it, neither restates the literal.
BACKLOG_DONE_MARKER_PREFIX="done #"

# Rewrite the item whose text is <key> in <path> from '- [building] <key>' to
# '- [done #<pr_num>] <key>'. Reconciler: decides from the file's current contents,
# idempotent, rewrites in place only when there is exactly one line to change, and
# leaves no temp file on any path. <pr_num> is digits only (no '#').
#
# 0 rewritten
# 1 <path> not a readable+writable regular file, or <pr_num> !~ ^[0-9]+$, or mv failed
# 2 no '- [building] <key>' line — reworded / removed / never [building]; a human looks
# 3 nothing to do — a '- [<other>] <key>' line already exists (done / stranded / researching)
backlog_mark_done () { :; }   # <path> <key> <pr_num>
```

`.pipeline/run.sh`:

```sh
# Advance this cycle's claim from '[building]' to '[done #<pr_num>]' on main now that
# its PR merged. git/gh plumbing only — the decision is backlog_mark_done in backlog.sh.
# Always returns 0: a failure is logged and the night continues, same posture as
# reconcile_stranded_claims (snapshot_dirty_main rescues any uncommitted edit).
reconcile_shipped_claim () { :; }   # <pr_num>
```

Call site — in `run_cycle`, the successful-merge tail:

```sh
  echo "PR #$pr_num auto-merged (clean, no conflicts)." | tee -a "$LOG"
  reconcile_shipped_claim "$pr_num"      # <-- new line
  return 0
```

`.pipeline/test_backlog.sh`: new section "Shipped claims (C24–C32)" after "Stranded claims
(C15–C23)", same harness (`assert_eq`, `write_fixture`-shaped fixtures, read-only `run.sh` greps
for C31), summary line unchanged.

## Open questions

1. **Object retention after `--delete-branch`.** The git-diff path assumes `headRefOid` is a
   locally reachable, non-gc'd object once `main` is fetched. It should be (the maintainer pushed
   it from this same clone; the merge commit references it as `^2`), but I have not verified the
   retention window on the box. If it proves fragile, fall back to a `logs/last-claim.txt` handoff
   written by the maintainer (one line in `agentlab-maintainer.md`, one more `rm -f` in
   `run_cycle`).
2. **PRs merged outside auto-merge.** If auto-merge declines and a human merges later,
   `reconcile_shipped_claim` never runs for that PR and the item stays `[building]` until the
   observer re-files it. Deferred to the `gh pr list --state merged` sweep noted as out of scope.
3. **Backfilling pre-fix stragglers generally.** Only #37/#38 are known-stale now and are
   hand-corrected. A general sweep matching `[building]` item text to a merged PR's branch or
   research-note name is more robust but needs network in the loop and fuzzy matching — same
   deferral.
4. **Marking 217 vs 218.** Whether both health-finding lines get `[done #<this PR>]` in-PR or via
   the new reconciler is left to the reviewer; both are resolved by this increment regardless.
5. **`--merge` assumption.** Auto-merge uses `gh pr merge --merge`, so a two-parent merge commit
   is guaranteed. If that ever changes to `--squash`, `^2` disappears and the git-diff path
   breaks — the handoff-file fallback would then be mandatory. Worth a one-line comment in
   `reconcile_shipped_claim` stating the dependency.
