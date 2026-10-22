# TardyAI milestone 7 (resume): ship the room-entry work that is stuck behind PR #6

Date: 2026-10-22. Mode: external:tardyai (kushsarora/TardyAI). Checkout `/Users/steeb/agentlab-work/tardyai`, pristine at origin/main 2373c9b. No web research: this is a local-state cycle. The hn-search MCP was down this session (missing venv python); it was not needed.

## Question

Milestone 7 was designed and built on 2026-10-21 but never shipped. Is the work still recoverable, is it still valid against main, and what unblocks the PR?

## Findings

1. **The work is not lost.** The pipeline preserved it locally as branch `cycle/2026-10-21_020002-unshipped-1` (one commit, `7f9bb63`, parent = current main 2373c9b). It touches 10 files, +679/-55: `NavRouter.js`, `NavRouter.c3typ`, `NavRoute.c3typ`, `vite.config.mts`, `tardyApi.ts`, `RoomSearch.tsx`, `RoomSearch.test.tsx`, and the node tests `room-entry.test.js`, `all-rooms.test.js`, `steps.test.js`. It applies cleanly: main has not moved since.
2. **Why it never shipped.** `verify.sh` fails on main in exactly one step, "routes stay in corridors", because PR #6 (data and generator fix for 5 edges) is still OPEN (updated 2026-10-20, mergeable, no reviews). Every milestone fails the hard gate until #6 merges, so skipping to milestone 8 would not help.
3. **The work is compatible with #6.** In a throwaway worktree I merged the WIP branch with `agentlab/2026-10-20-doorway-links-corridors`: no conflicts, `node --test` passes, and `walkability.py` reports `OK (1358 edges stay in corridors)`. So the all-rooms sweep still passes on #6's regenerated data. The worktree was removed; the checkout is untouched.
4. **Do not stack.** The 2026-10-19 decision (no stacked PRs; GitHub does not retarget because TardyAI keeps merged branches) still holds, and #6's data diff would pollute M7's single-purpose PR. Do not revert that decision.
5. The 2026-10-21 research note's spec and acceptance criteria stand unchanged. Read `research/2026-10-21-tardyai-room-entry-matching.md`, which this note defers to.

## Build proposal

Covers milestone 7 only, same scope as the 2026-10-21 note (matcher, candidates UI, all-rooms guard, wording regression tests). No new code design.

Builder steps, in order:
1. In the checkout, restore the WIP: `git checkout cycle/2026-10-21_020002-unshipped-1 -- pkgbc231600` (or `git cherry-pick -n 7f9bb63`). Do not commit.
2. Check PR #6: `gh pr view 6 -R kushsarora/TardyAI --json state`. 
   - If MERGED and the checkout was synced to the new main: run verify; it should print `VERIFY: PASS`. Re-run the all-rooms sweep (it is the guard against #6's data change) and update the PR-body numbers (route/step counts).
   - If still OPEN: run verify and confirm the only failing step is "routes stay in corridors" with the same 5 edges. Then report milestone 7 as `in-progress`, blocked on #6 merging. Do not copy #6's data into this change and do not weaken the gate.
3. Acceptance criteria are the 6 listed in the 2026-10-21 note, unchanged. Protected files stay untouched (none are in the WIP diff).

Out of scope: `RoomFinder.*`, seed data, the "mapped access point" copy, milestone 8.

Needed from the human (not the pipeline): merge or review kushsarora/TardyAI#6. That is the single blocker for this and every later milestone.

## Open questions

- Whether the pipeline's maintainer will ship at all while verify is red. If not, #6 must merge first; I could not confirm the maintainer's behaviour here.
- Studio-only checks (search action behaviour for floor-hint queries) remain on the PR checklist, as before.
