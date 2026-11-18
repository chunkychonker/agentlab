# nightloop milestone 6: claim tracking

Date: 2026-11-18. Mode: project:nightloop. Milestone 6 (`not-started` -> `in-progress`).

## Question

What is the smallest Python port of agentlab's backlog claim lifecycle
(`[ ]` -> `[researching]`/`[building]` -> `[done #N]`, plus stranded-claim
recovery) that keeps the bash version's hard-won behaviour, and where should the
pure decisions stop and the git/gh edge start?

## Findings

No web research this cycle. Milestone 6 is a port of mechanisms that exist in this
repo, and the primary sources are local. I did not invent any external API.
`gh pr view --json mergeCommit` is the one external fact, and it comes from the
repo's own working code at `run.sh:659` (verified live on PRs #37, #38 and #41 on
2026-09-10, per `knowledge/pipeline-claim-lifecycle.md`).

Sources read (all local, current as of 2026-11-18):
- `.pipeline/backlog.sh`: the decisions (`backlog_count_unclaimed`,
  `backlog_claimed_line`, `backlog_claim_key`, `backlog_apply_stranded`,
  `backlog_claimed_item`, `backlog_file_stranded`, `backlog_mark_done`).
- `.pipeline/run.sh:647` (`reconcile_shipped_claim`) and `:1304`
  (`reconcile_stranded_claims`): the git/gh plumbing.
- `knowledge/pipeline-claim-lifecycle.md`: the three failures and the traps.
- `projects/nightloop/nightloop/cycle/ports.py`, `git_runner.py`: the shapes this
  must fit.

Behaviour that must survive the port (each one was a real bug in bash):
1. **Identity is the item text, matched literally**, as a whole-line string
   comparison. Python gets this free: use `==` and `str.startswith`, never `re`
   built from item text. The bash glob-class trap (`${line#- [building] }`) cannot
   occur, but a test should still use an item containing `[building]`, backticks and
   brackets, because a regex port would reintroduce it.
2. **Only `- [ ] ` at column 0 counts as unclaimed.** Indented lines and every
   other marker never count.
3. **Stranded claim, item on main:** `- [ ] K` becomes `- [stranded <branch>] K`.
   Any other marker with the same text means leave it alone (code 3). No such line
   means not found (code 2).
4. **Stranded claim, item never on main:** the researcher wrote the topic and its
   marker in one edit. Append `- [stranded <branch>] <item>` plus its indented
   continuation lines under a `## Stranded work (unshipped branches)` section, made
   on first use. Dedupe against every marker, including `[done #N]`, so salvaged
   work is not resurrected.
5. **Mark done:** `- [building] K` becomes `- [done #N] K`. PR number is digits only.
   Idempotent. Any other marker with the same text is left alone.
6. **Shipped claim is recovered from the merge commit's first-parent diff**, not
   the merge base (the merge base of a merged PR is its own head, giving an empty
   diff). The stranded claim is recovered from `merge-base main <ref>`..`<ref>`.
7. **Reconcilers never abort the night, never open a PR, never delete a branch.**
   In bash this is "always return 0". In Python that must not become a swallowed
   exception (Protocol section 4): return a typed per-item result and let the
   caller log it.

Differences worth deciding explicitly:
- The text claim only has two claimed markers, `[researching]` and `[building]`.
  Keep that set as one named constant.
- bash `backlog_claimed_line` takes the first claim line in a diff and refuses to
  guess among several. Keep that: one cycle claims one item.
- Operate on `str` in, `str` out in the pure core; only a thin `BacklogFile` reads
  and writes (atomically: temp file + `os.replace`) so a no-op leaves the file's
  mtime alone and `git status` clean.

## Build proposal

### 1. Intent

Add `nightloop/claims/`: the backlog claim lifecycle as pure text-in/text-out
functions, plus a small edge that reads claims out of git and writes the file.
Out of scope: wiring it into `run_night` or a CLI (milestone 7 owns that, so the
orchestrator is untouched in this change); the replenishment/health-finding half of
`backlog.sh`; the agents' own `[ ]` -> `[building]` edits (agents do those, the
library only recognises them); a periodic sweep of merged PRs for missed mark-done.

### 2. Behavioural spec

Pure core (`claims/backlog_text.py`, `claims/claim_diff.py`, no imports of I/O):
- `count_unclaimed(text) -> int`: lines starting `"- [ ] "` exactly.
- `claim_from_diff(diff) -> Claim | None`: first added line starting `+- [researching] `
  or `+- [building] `; `Claim(key, item)` where `key` is the first line's text and `item`
  is `key` plus the following added lines that start with a space or tab (stop at the
  first line that does not). No claim returns `None` (normal, not an error).
- `apply_stranded(text, claim, branch) -> Edit`, `mark_done(text, key, pr) -> Edit`.
  `Edit` is a sum type: `Rewritten(text)`, `AlreadyHandled`, `ItemNotFound`. Rewriting
  preserves every other byte of `text`, including a final line without a newline.
- `file_stranded(text, claim, branch) -> Edit`: the append variant. `AlreadyHandled`
  if any `- [..] <key>` line exists.
- `ItemNotFound` from `apply_stranded` is what makes the reconciler fall through to
  `file_stranded`; the fall-through lives in `reconcile`, not in `apply_stranded`.

Invariants:
- I1: every function is idempotent: applying it to its own `Rewritten` output returns
  `AlreadyHandled`.
- I2: a result never changes any line other than the claim's (and continuation lines
  appended by `file_stranded`).
- I3: `count_unclaimed` drops by exactly one after a `Rewritten` from either stranded
  function when the item was `- [ ]`, and is unchanged for `mark_done`.
- I4: an empty `branch`, empty key, or non-digit/zero-leading-garbage PR number raises
  `ValueError` at the function boundary (a typed `PrNumber` value type is better than
  a `str`: builder's choice, but not an unvalidated `str`).

Edge:
- `BacklogFile(path)`: `read() -> str`, `write(text)` (atomic). Missing/unreadable
  raises `OSError`.
- `ClaimSource` port (declared in the core): `unshipped_claims() -> list[UnshippedBranch]`
  and `merged_claim(pr) -> Claim | None`. `GitClaimSource(git: GitRunner, gh: Gh, backlog_path)`
  implements it: lists `refs/heads/cycle/*-unshipped-*` and
  `refs/remotes/origin/cycle/*-unshipped-*` via `for-each-ref`, diffs `merge-base main <ref>`
  against the ref for the backlog path; for a PR it calls `gh pr view <n> --json mergeCommit`
  then `git diff <mc>^1 <mc> -- <backlog>`.
- `reconcile_stranded(source, backlog) -> list[StrandedOutcome]` and
  `reconcile_shipped(source, backlog, pr) -> ShippedOutcome`. Each outcome is a typed
  value (one of: marked, filed, already-handled, no-claim, item-not-found, unreadable
  source). Environment errors (`OSError`, git/gh cannot start) propagate; a bad ref
  or empty diff is an outcome, never an exception. Neither commits or pushes: the
  caller does that (milestone 7), so the library has no push failure mode.

Failure domain: a bug here can mark the wrong item or leave one `[building]`. It cannot
lose work, because it never touches branches or pushes.

### 3. Interfaces (stubs, no bodies)

```python
@dataclass(frozen=True)
class Claim: key: str; item: str          # item startswith key; key non-empty
class Edit: Rewritten(text: str) | AlreadyHandled() | ItemNotFound()

def count_unclaimed(text: str) -> int
def claim_from_diff(diff: str) -> Claim | None
def apply_stranded(text: str, claim: Claim, branch: str) -> Edit
def file_stranded(text: str, claim: Claim, branch: str) -> Edit
def mark_done(text: str, key: str, pr: PrNumber) -> Edit

class ClaimSource(Protocol):
    def unshipped_claims(self) -> Sequence[UnshippedBranch]   # ref, Claim | None
    def merged_claim(self, pr: PrNumber) -> Claim | None
```

### 4. Acceptance criteria (the tests)

Port the bash cases as tests against the spec, not the bash internals
(`.pipeline/test_backlog.sh` C15-C39 is the checklist):
- C-count: `- [ ]` counts; indented, `[building]`, `[done #3]`, `[stranded x]` do not.
- C-literal: an item with `[building]`, backticks and parentheses is found and
  rewritten; a different item sharing a prefix is untouched.
- C-stranded: `[ ]` -> `[stranded <ref>]`; already stranded/done/claimed -> `AlreadyHandled`;
  absent -> `ItemNotFound`.
- C-file: absent item appended under the section (created once; a second call is
  `AlreadyHandled` and does not duplicate the heading); continuation lines kept;
  an item already `[done #N]` is not resurrected.
- C-done: `[building]` -> `[done #N]`; second call `AlreadyHandled`; `[researching]`
  or `[ ]` with that text -> `AlreadyHandled`; absent -> `ItemNotFound`; bad PR number
  -> `ValueError`.
- C-diff: first claim wins when two are added; only indented added lines extend the
  item; a diff with no claim -> `None`.
- No trailing newline in the file survives a rewrite unchanged.
- Idempotence (I1) as a property over all five functions.
- Edge test on a temp git repo with a local bare `origin`: a real `cycle/..-unshipped-..`
  branch whose commit edits `BACKLOG.md`; `reconcile_stranded` marks main's text and
  leaves no stray temp file. A second run changes nothing. A merged-PR case uses a fake
  `Gh` returning a merge commit that exists in the temp repo.
- Purity test (like `test_cycle_purity.py`): `claims/` core modules import no I/O module.
- Mutation checks, each must fail a test: match by `startswith` instead of equality,
  diff from merge-base instead of first parent for merged PRs, skip the dedupe in
  `file_stranded`, drop the continuation lines.

Run: `cd projects/nightloop && python3 -I -m unittest discover -v` (existing 291 tests
must still pass; stdlib only).

## Open questions

- Whether the live `gh pr view --json mergeCommit` field name still matches the current
  `gh` release was not re-checked this cycle (no shell, and the builder's publish
  guard blocks `gh`). The fake `Gh` will encode the shape `run.sh` uses; a human live
  check belongs with milestone 5's still-outstanding one.
- Does `nightloop.ship.gh.Gh` already expose a generic `run`, or does it need a
  `merge_commit(pr)` method? Builder should read `ship/gh.py` first and add the
  narrowest method.
- Open PR #101 is a pipeline fix unrelated to this milestone; no overlap.
