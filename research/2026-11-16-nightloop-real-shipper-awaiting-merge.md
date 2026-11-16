# nightloop m5 (real shipper): already built, awaiting merge

Date: 2026-11-16. Mode: project:nightloop. No web research this cycle.

## Question

Which nightloop milestone should this cycle research, given the open PRs?

## Findings

- The topmost milestone that is not done is m5, the real shipper. `PLAN.md` listed it `not-started`.
- `logs/research-context.md` shows open PR #93, "feat(nightloop): real gh shipper and branch-safe cycle recovery (m5)". Its branch is `cycle/2026-11-15-nightloop-real-shipper`. The title covers both parts of m5: the shipper, and recovery that catches commits left on local `main`.
- A second research note for m5 would repeat that PR. The repo has been bitten by this before (PRs #5, #6 and #11).
- I did not skip to m6 (claim tracking). The plan forbids skipping ahead of an unfinished predecessor without a decisions-log entry. m6 would also edit `PLAN.md` and the cycle code, so it would conflict with #93 when merged.
- Not verified: I have no shell here, so I could not read #93's branch. I judged that it covers m5 from its title alone.

## Build proposal

None this cycle. Building anything now would either duplicate #93 or conflict with it.

To unblock, a human should do one of these:

1. Merge #93. The next cycle then resumes at m6 (claim tracking). m6 builds on the `Shipped(pr)` type, which carries the PR number the `[done #N]` mark needs.
2. Reject #93 and say why. Then the next cycle re-researches m5.

## Open questions

- Whether #93 meets the m5 spec in `PLAN.md`: a failed push, PR creation or check gives `NotShipped`; the review record goes in the PR body; tests use a local bare `origin` and a fake `gh`. That is for the reviewer and the human who merges.
- Whether `PLAN.md` should be allowed to move on to the next milestone while the previous one's PR is open. That would be a pipeline policy decision. For now the researcher waits.
