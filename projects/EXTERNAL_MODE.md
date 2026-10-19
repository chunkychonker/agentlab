# External mode (`external:<slug>`)

For working a repo agentlab does **not** own — a teammate's or a hackathon
project — by pull request only. Set `.pipeline/mode` to `external:<slug>`.

The night is `.pipeline/external_night.sh`. Before every cycle it resets a
checkout of the target to its base branch and writes **`logs/ext-context.md`**:
the checkout path, the target repo, the verify command, and the files you may
not touch. **Every phase reads that file first.** Its path and rules override
anything in your own definition that assumes the work lives in `~/agentlab`.

## What lives where

| Thing | Where |
| --- | --- |
| The target's code (what you change) | the checkout named in `logs/ext-context.md` (`~/agentlab-work/<slug>/`) |
| Plan, milestones, decisions log | `~/agentlab/projects/<slug>/PLAN.md` |
| Target config (repo, base, verify, protect) | `~/agentlab/projects/<slug>/target` |
| Platform-parity gate | `~/agentlab/projects/<slug>/verify.sh` |
| Research note | `~/agentlab/research/YYYY-MM-DD-<slug>-<milestone>.md` |

**Never copy the target's code into `~/agentlab`.** It is not ours to publish.

## PR size

External PRs are **feature-sized**: one complete, user-visible capability
with its tests. Typically 300–1000 changed lines across backend, UI and tests.
Not a one-line fix or a test file on its own. This overrides "small and
working beats big and broken" in the builder and researcher definitions. It
does not override "working": verify must still pass.

- The researcher proposes the **whole milestone**, and may bundle the next
  consecutive milestone when both share one theme (for example backend and
  the UI that shows it). The proposal says which milestones it covers.
- The builder finishes every acceptance criterion it was given. A milestone
  left half done is reported as `in-progress` with exactly what remains. It
  is never shipped as if done.
- The reviewer judges the PR by **one purpose**, not by size. A large diff
  serving one milestone (or one bundled theme) is fine. Unrelated changes in
  the same PR (drive-by refactors, reformatting, a second unrelated feature)
  are still a FAIL.
- Prefer behavior that fixes the product's core flow over polish. When a
  milestone touches routing or directions, test it against the real seed data,
  not only fixtures.

## Per phase

- **Researcher** — Follow your *Project mode* steps against `PLAN.md`, but
  read the target's code in the checkout, not under `projects/<slug>/`. The
  build proposal must not touch a protected or hot file listed in
  `logs/ext-context.md`. If the next milestone can only be done by touching
  one, mark it `blocked: needs <file>, hot/protected` and move to the next.
- **Builder** — Implement the milestone **in the checkout**, matching its
  existing conventions. Then run the verify command from
  `logs/ext-context.md` yourself and iterate until it prints `VERIFY: PASS`.
  The pipeline re-runs it as a hard gate after you finish, and also rejects any
  change to a protected or hot file. Update `PLAN.md` (in agentlab) as in
  Project mode. Do not commit; do not touch git in either repo.
- **Reviewer** — Review `git -C <checkout> diff` plus untracked files there.
  Read `logs/last-verify.txt` (the gate already passed; check that the tests
  it ran actually assert something). Write `logs/last-review.md` in agentlab
  as usual, with `Increment: <repo>:<paths>`.
- **Maintainer** — Only on PASS. In the checkout:
  1. Confirm `git -C <checkout> config user.name` / `user.email`, and if unset
     set them **repo-locally** to `Steve Ling` / `steveylingy@gmail.com`.
  2. `git switch -c agentlab/YYYY-MM-DD-<milestone-slug>`, commit (no AI
     co-author trailer), `git push -u origin HEAD`.
  3. `gh pr create -R <repo> --base <base>` with a body that has: what
     changed and why; how it was verified (paste the `VERIFY:` summary); and
     this checklist for the human, verbatim:
     ```
     ## Verify in C3 AI Studio before merging
     - [ ] In Studio, ask the agent to pull origin/<base> after merge (or pull this branch)
     - [ ] Desktop preview: the change works; Navigate West Entrance → 3335 still routes
     - [ ] Mobile preview: the change works at phone width
     ```
     (The checklist names this project's demo route; adapt it per project.)
  4. Write the PR number, only the number, to `~/agentlab/logs/last-pr.txt`.
  5. Mark the milestone `done <repo>#<PR>` in `PLAN.md` — **don't commit
     agentlab**; the pipeline does its bookkeeping commit.
  **Never merge, never force-push, never push to the base branch.**
