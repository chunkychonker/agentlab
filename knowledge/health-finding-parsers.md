# The health/pipeline finding parsers: a wire contract enforced by a comment, not a check

`.pipeline/health.sh` (`health_findings`) and `.pipeline/pipeline_health.sh`
(`pipeline_findings`) turn a markdown report an agent wrote into backlog items a
human never asked for individually. Both read: neither writes, files, or
touches git (that split is deliberate — see
[[pipeline-claim-lifecycle]] for the sibling split on the filing side). This
note is about the contract between what the *agent's own instructions* teach
it to write and what the *parser* actually accepts, and the bug that
contract's silent drift produced.

## The bug (found and fixed 2026-09-12)

Both parsers' section-body cases carry this comment, word-for-word in each
file:

> A `'(none)'` placeholder needs no special case; it does not begin with `'- '`.

That is true only if the producer never bullets it. `.claude/agents/
agentlab-pipeline-observer.md`'s own worked example for an empty section is:

```
## Phase failures
- (none)
```

— dash-bulleted. The parser's `plain`-section case matches any `'- '*` line,
strips the marker, and files whatever is left if it's non-empty — so `- (none)`
survives stripping as the four characters `(none)` and gets filed as a
backlog item with a real subject and no actionable content whatsoever. This
happened for real on 2026-09-02: `BACKLOG.md` picked up
`fix (health 2026-09-02): (none)`, traced in
`research/2026-09-12-health-finding-none-placeholder.md` back to exactly this
mismatch. `.pipeline/health.sh`'s `wikilinks`/`backlog` sections have the
identical `'- '*` shape and the identical risk, unconfirmed live only because
no incident happened to exercise it yet — `agentlab-health.md`'s instruction
is ambiguous rather than actively wrong (it never shows a worked `(none)`
example either way).

One section is structurally immune by construction: `health_findings`'s
`examples` case matches only `'- FAIL '*`, so a bulleted `(none)` can never
satisfy it. Same idea as `pipeline_findings`'s `outcomes` case
(`'- ABORTED '*`/`'- PARTIAL '*`). The vulnerability is specific to the
generic `'- '*` sections, not universal to either parser.

The fix: an exact-match guard (`body` after marker-stripping equals the
literal string `(none)`, not merely starts with it — a prefix filter would
swallow a real finding that happens to start with that word) in every generic
`'- '*` section of both parsers, **and** correcting the template in
`agentlab-pipeline-observer.md` so the model stops being taught the form that
breaks its own downstream parser. Fixing only the parser and leaving the
template would leave the next section someone adds to either report format
exposed to the same trap by imitation.

## The generalizable lesson

**A parser's safety comment ("X needs no special case because the producer
never writes Y") is a claim about a *different file* — the thing that
generates the input — and it needs the same suspicion as a hardcoded example
in a README.** The two files here (an agent's markdown instructions and a bash
parser) are never edited in the same commit, have no shared test, and disagree
about a one-character question (dash or no dash) for months before a finding
made it concrete. This is the same shape [[doc-transcript-drift]] documents
for READMEs pasting program output — *"any invariant that spans two files
which are never edited together is invisible to diff-scoped review"* — applied
to a machine-to-machine contract instead of a human-facing doc. The fix in
both cases is the same: a test that feeds the *producer's own documented
example* through the *actual parser*, not a test that only exercises forms the
test-writer assumed were the only ones in use (`test_gates.sh`'s pre-fix
fixtures for the healthy case used bare `(none)`, never the dashed form the
agent's own template specifies — so the suite's own author made the identical
assumption the bug depended on).

## Related

- [[pipeline-claim-lifecycle]] — the sibling contract gap on the *filing* side
  of the same pipeline (a claim state transition with no enforcing step,
  rather than a report shape with no enforcing check)
- [[doc-transcript-drift]] — the same "invariant spans two files never edited
  together" shape, for human-facing docs instead of an agent-to-parser
  contract
- [[bash-3.2-testable-scripts]] — the test harness (`test_gates.sh`) both
  parsers are verified under, and its own two gotchas (the `grep -c` exit-1
  trap and definition-before-call ordering) worth reading alongside this one
