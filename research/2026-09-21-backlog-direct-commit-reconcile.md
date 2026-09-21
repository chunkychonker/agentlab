# 2026-09-21 — closing a backlog finding that a direct commit fixed, not a PR

## Question

The topmost unclaimed `[ ]` item in `BACKLOG.md` (line 236, filed by the health
check on 2026-09-02) names "Background tasks still running after 600s;
terminating" as the direct cause of two lost nights. Is this still open, and
if not, what's the real gap it points at?

## Findings

**No open PR covers it.** `gh pr list --state open` returns `[]` (checked
2026-09-21) — nothing to skip to.

**The literal cause is already fixed on `main`, by a direct commit, not a
PR.** `git log --oneline -1 832134b`:

```
832134b fix(pipeline): wait for background tasks instead of killing them at 600s
```

Its body names this exact backlog item as the incident: "Five nights lost
whole cycles to this... the health check filed the error text verbatim and
the text names its own fix, which was never applied." The fix —
`export CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0` — is live in
`.pipeline/run.sh:58` right now, ahead of `run_phase`'s definition (verified
by reading the file directly, not trusting the commit message alone). This
commit is a direct child of `main`'s history (`git log --oneline main` shows
it inline, no merge commit, no `cycle/*` branch), authored by Steve Ling, not
attributed to any `gh pr view` result — confirmed no PR #43–#49 touches
`.pipeline/run.sh` for this change (`gh pr list --state all` lists #44's
subject as the run-log classifier, #45 as MCP prompts, #46–49 as the salvage
batch; none is this fix).

**The natural next increment is also already done, just unshipped.**
`research/2026-09-17-pipeline-run-log-classifier.md` (merged as PR #44,
2026-09-17) built `.pipeline/run_log.sh`'s `classify_run_log` and explicitly
deferred "wiring `classify_run_log` into `run.sh`'s pipeline-observer phase
call" to a later cycle. `git branch -a` shows `cycle/2026-09-20-unshipped-022445-1`
— a stranded branch from last night's killed cycle — whose diff against `main`
does exactly that wiring (`.claude/agents/agentlab-pipeline-observer.md`,
`.pipeline/run.sh`, `.pipeline/run_log.sh`, `.pipeline/test_run_log.sh`, plus
`research/2026-09-20-pipeline-run-log-classifier-wiring.md`, none of which
exist on `main` — confirmed via `ls research/` on `main` returning nothing for
that date and `git diff main cycle/2026-09-20-unshipped-022445-1 --stat`
showing ~1,080 lines added). Per this repo's own convention (`BACKLOG.md`'s
"Stranded work" section: "salvaging it is a human's call"), re-deriving that
same wiring today would be a same-day duplicate of real, sitting work — the
exact mistake the researcher's own instructions warn about for `examples/`
names, applied here to a `.pipeline/` increment instead.

**So this backlog line has no fresh code gap left in its literal reading.**
What it does have: it is not the only stale finding of this shape. A second
direct commit lands right next to the first one, same night, same author, no
PR:

```
b116b0b chore(pipeline): one cycle per night, and close three resolved claims
```

Its own body closes three *other* `BACKLOG.md` lines by hand (`:150`, `:216`,
`:217`) — proving the author already knows this reconciliation has to happen
by hand when there's no PR to key off — but leaves this one open, and leaves
its own fix's effect (cutting cycles 2 → 1, which is the fix for essentially
every "shipped 0/2, ... session-limit" finding lower in the same section: `no
run log for 2026-09-04`, `run-2026-09-03_134704`, `run-2026-09-07_020001`,
`run-2026-09-08_020001`, and others) equally unreconciled. That is not one
stale line; reading the section against these two commits, it's a small
cluster — evidence the gap is systemic, not a one-off oversight worth
patching by hand again.

**Why the existing reconciler can't do this.**
`knowledge/pipeline-claim-lifecycle.md` documents three failure modes for a
`BACKLOG.md` claim and how each was closed: Failure 1 (a failed cycle silently
releases its claim, fixed by `reconcile_stranded_claims`), Failure 2
(replenishment measuring a backlog it didn't fill, fixed by reordering),
Failure 3 (a shipped claim never marked `[done #N]`, fixed 2026-09-10 by
`reconcile_shipped_claim <pr_num>`, which the note's own "what it still does
not cover" section already flags as incomplete for anything that reaches
`main` outside its one call site). Read `backlog_mark_done`
(`.pipeline/backlog.sh:418`) directly: it is keyed on a **PR number**
(`gh pr view <n> --json mergeCommit`) and matches the item's **exact,
byte-identical text** after `- [building] `. A direct commit has neither: no
PR number to look up, and a maintenance fix's commit message is prose about
the *fix*, not a verbatim copy of the multi-sentence `BACKLOG.md` line it
closes (line 236 alone is ~450 characters). This is a fourth, distinct failure
mode the existing three-part taxonomy doesn't name: **a claim resolved by a
commit with no PR has no reconciliation path at all**, automated or
previously-manual — Failure 3's five historical occurrences were each fixed by
a hand-written `chore(backlog)` commit before the automated version existed;
this one has no such bridge yet, and 832134b/b116b0b show it's already being
needed in practice.

**Prior art for the missing half: git already has a structured way to attach
machine-readable metadata to a commit message.**
[`git-interpret-trailers`](https://git-scm.com/docs/git-interpret-trailers)
(git-scm.com docs, current) parses `key: value` lines at the end of a commit
message — the same mechanism `Co-Authored-By:` trailers already use in this
repo's own commits. `git interpret-trailers --parse <file>` (an alias for
`--only-trailers --only-input --unfold`) extracts exactly those lines in
machine-friendly form, with no custom text-parsing needed on the read side.
This repo doesn't use custom trailers for anything yet, but the tool is stock
git, already present wherever `run.sh` runs.

## Build proposal

**What it is.** One new function in `.pipeline/backlog.sh`,
`backlog_mark_done_by_commit`, the direct-commit sibling of the existing
`backlog_mark_done` — same file, same pure/offline-testable convention, same
temp-file-and-rename write pattern. It closes the literal gap found above:
given a short, human-chosen substring and a commit SHA, rewrite the one
unresolved `BACKLOG.md` item containing that substring to `[done <sha>]`.

**Where it goes.** `.pipeline/backlog.sh` (existing file, ~600 lines today —
append after `backlog_mark_done`, which it deliberately mirrors) and
`.pipeline/test_backlog.sh` (existing file, cases resume at `C42` — `C1`
through `C41` are the current highest case names, confirmed by
`grep -oE '"C[0-9]+[a-z]?"' .pipeline/test_backlog.sh | sort -u`). No new file,
no `examples/` directory — this is a `.pipeline/` increment, the same category
as `run_log.sh` (PR #44) and `network_retry.sh` (PR #49).

### Why substring match, not `backlog_mark_done`'s exact match

`backlog_mark_done` matches `- [building] <key>` where `<key>` is the item's
**entire** text, because the builder that calls it already has that exact
string (it wrote the line). A commit message trailer cannot reasonably quote
a multi-sentence `BACKLOG.md` item verbatim — the caller only has a short
phrase describing what it fixed. So matching has to be substring (`grep -F`),
which makes **uniqueness** a real question a full-text match never has to
ask. This function answers it by refusing to guess: 2+ unresolved matches is
a distinct failure code, not "first wins."

### Interface (stub — no body; layer 4 is the builder's)

```bash
# BACKLOG_DONE_BY_COMMIT_PREFIX="done "   # note: no '#' — this marker is a
#   git SHA, not a PR number, and must not be misread as one by eye or by
#   any future grep keyed on '#'.

# backlog_mark_done_by_commit <path> <substr> <sha>
#
# Sibling of backlog_mark_done for the gap knowledge/pipeline-claim-lifecycle.md
# calls Failure 4: work that lands on main as a direct maintenance commit (no
# PR, so reconcile_shipped_claim's `gh pr view` lookup has nothing to key off)
# never gets its BACKLOG.md item rewritten, and stays open even after the
# named cause is fixed. Two real, dated instances on this repo's own history:
# commit 832134b (2026-09-20) fixes the exact text of BACKLOG.md's
# "Background tasks still running after 600s" finding, and b116b0b
# (2026-09-20, same night) fixes several "session limit" findings by cutting
# cycles/night 2 -> 1 — neither is attached to a PR, and both findings are
# still `[ ]` today.
#
# Matching is substring (grep -F over UNRESOLVED items only — any marker
# except '[done ...]'), not backlog_mark_done's exact-line match, for the
# reason above. The caller picks <substr> stable and specific enough to be
# unique among today's unresolved items; uniqueness is enforced here, not
# assumed.
#
# Failure modes (same code shape as backlog_mark_done, plus the new ambiguous
# case a substring match makes possible that an exact match never could):
#   0  rewritten: exactly one unresolved item contained <substr>; it now
#      reads '- [done <sha>] <original item text, unchanged>'
#   1  <path> is not a readable/writable regular file, or <sha> is not
#      7-40 lowercase hex characters (a git abbreviated-or-full SHA — digits
#      only would silently accept a PR number here, re-creating the exact
#      confusion this function exists to keep separate from
#      backlog_mark_done's '#<pr_num>' marker). No temp file left on any
#      failure path.
#   2  no item at all -- resolved or not -- contains <substr>: nothing to
#      reconcile, most likely a stale or misspelled substring
#   3  already done: no UNRESOLVED item contains <substr>, but a '[done ...]'
#      one does -- a second run after an earlier pass already handled it
#      (idempotent, same posture as backlog_mark_done's own C25)
#   4  ambiguous: 2+ unresolved items contain <substr> -- the caller must
#      re-run with a longer, more specific substring. A silently wrong
#      rewrite is worse than a loud no-op (CLAUDE.md Engineering Protocol §4:
#      "Fail fast and loudly").
backlog_mark_done_by_commit () { : ; }
```

### Self-test cases (offline, extends `test_backlog.sh`'s existing fixture
style — temp files, `assert_eq` on return code + resulting file content, no
network, no key)

1. One unresolved item (`- [ ]`) contains the substring → rewritten to
   `- [done <sha>] <original text>`, rc 0.
2. Same call run a second time → rc 3, file byte-identical to case 1's result
   (idempotent, mirrors `backlog_mark_done`'s `C25`).
3. Substring appears in an already-`- [researching]` or `- [building]` item
   (not just a bare `- [ ]`) → still matched and rewritten — proves the match
   isn't restricted to one marker the way `backlog_mark_done` is restricted to
   `[building]`.
4. Substring matches zero items, resolved or not → rc 2, file untouched.
5. Substring matches two distinct *unresolved* items → rc 4, file untouched.
6. Substring matches one item already `[done #17]` and zero unresolved items
   → rc 3, file untouched, the existing PR-based marker preserved verbatim
   (proves the two marker styles coexist without clobbering each other).
7. Malformed `<sha>` (empty, uppercase, contains a non-hex character, 6 chars,
   41 chars) → rc 1, file untouched, for each case.
8. Missing or unwritable `<path>` → rc 1.
9. A regression fixture built from this cycle's real motivating case: a
   synthetic line reproducing `BACKLOG.md`'s actual line 236 text, matched by
   the substring `"Background tasks still running after 600s; terminating."`
   (the literal quoted clause already inside the finding) → rewritten
   correctly. Grounds the test in the real backlog line, not only synthetic
   ones.

"It works" = `bash .pipeline/test_backlog.sh` exits 0, all of `C1`–`C41` still
pass unchanged, plus the new cases from `C42` on, runtime still under a
second.

### Explicitly out of scope

- **Parsing real commit trailers and wiring this into `run.sh`.** Reading
  `git interpret-trailers --parse` output from recent commit messages,
  extracting `(substr, sha)` pairs, and calling
  `backlog_mark_done_by_commit` for each from a pre-loop step in `run.sh`
  (alongside `reconcile_stranded_claims`) is the natural next increment,
  deliberately deferred — the same split Failure 1's and Failure 3's fixes
  both use: the decision is a pure, offline-tested function in `backlog.sh`;
  the git plumbing that finds commits and SHAs stays in `run.sh`, exercised
  only by real runs. Building both in one cycle risks exactly the
  half-shipped, stranded shape `cycle/2026-09-20-unshipped-022445-1` is
  already sitting in for the run-log classifier.
- **Retroactively closing the two motivating findings** (`BACKLOG.md`'s
  "Background tasks still running after 600s" line and the "session limit"
  cluster near it). That needs a hand-written `chore(backlog)` commit today —
  the same manual bridge every one of Failure 3's five historical occurrences
  needed before its automated reconciler existed — but it is a bookkeeping
  edit, not a feature, and mixing it into this PR would violate "one intent
  per change" (CLAUDE.md §6/§5). Leave it for a human or a future cycle to do
  by hand, exactly as `b116b0b` already did for three unrelated lines.
- **Choosing or enforcing the trailer key name** (a commit-msg hook, a
  template, or documentation telling humans to write it). This increment only
  defines the `BACKLOG.md`-side contract a future caller must satisfy.
- Any change to `run.sh`, `test_gates.sh`, `reconcile_shipped_claim`, or
  `reconcile_stranded_claims`.

## Open questions

- Whether a `Closes-backlog-item: <substr>` (or similarly named) trailer
  discipline is one Steve is actually willing to adopt for direct maintenance
  commits going forward — the mechanism is inert if nobody writes the
  trailer. 832134b and b116b0b both already carry careful, multi-paragraph
  bodies, so one more line is a small ask, but this is a process question,
  not a code one, and out of this researcher's scope to decide.
- What makes a good `<substr>` in practice. This note's own regression
  fixture (case 9) uses the literal quoted clause already embedded in a
  health-filed finding (`"Background tasks still running after 600s;
  terminating."`), which happens to be stable because `health_findings`/
  `pipeline_findings` findings often carry a verbatim quote from the log they
  describe. Whether that pattern holds for hand-filed, non-health backlog
  items (which have no such quoted anchor) is unconfirmed — worth checking
  against a few more real lines before leaning on it as the *recommended*
  convention rather than just a permitted one.
- Whether 7-character abbreviated SHAs stay unambiguous at this repo's
  current commit volume (low hundreds) is not something this function needs
  to answer — it accepts whatever length (7–40) the caller passes — but a
  future wiring step should probably default to `git rev-parse --short`'s
  output rather than a hardcoded length.

## Knowledge base

Extended `knowledge/pipeline-claim-lifecycle.md` with a new "Failure 4"
section (Failures 1–3 left untouched, dated additions only) recording the
gap, the two motivating commits, and the `backlog_mark_done_by_commit`
design; updated `knowledge/INDEX.md`'s summary line for
`[[pipeline-claim-lifecycle]]` from "three places" to "four."
