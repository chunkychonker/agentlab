# Docs that cannot drift (nightloop m12, researched 2026-11-25)

- A tool's docs rot at the seams: subcommand names, module paths, exit codes,
  links. Guard them with a test that derives the truth from code (argparse
  subcommands, `ExitCode`, `Path.exists`), not a second hand-kept list. Same
  lesson as [[doc-transcript-drift]]: diff-scoped review cannot see it.
- Incident history in docs needs a cited source path per row, or the literal
  words "no recorded incident". Sourced incidents found 2026-11-25: stale
  research shipped 2026-09-01 (freshness gate), stranded claim 2026-08-12 and
  five shipped-but-`[building]` PRs (claim reconcilers) — see
  [[pipeline-claim-lifecycle]], [[gates-port-python]]. No dated origin was
  found for the verdict gate or clear-handoff rule.
- Say what is not ported: nightloop has no verify gate, fix round, CI wait or
  auto-merge; those are agentlab-only (`PIPELINE.md`).
- Layout: Diátaxis (tutorial / how-to / reference / explanation) is a useful
  rule of thumb; don't mix types in one page.
