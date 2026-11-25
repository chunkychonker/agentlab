# nightloop milestone 12: docs (README, quickstart, architecture)

Date: 2026-11-25. Mode: project:nightloop. Milestone 12 (`not-started` -> `in-progress`).

## Question

What is the smallest docs increment that gives nightloop a README, a quickstart and an architecture page that explains each gate and the incident behind it, **and that cannot drift from the code** (the failure `knowledge/doc-transcript-drift.md` records)?

Milestone choice: 7c (pending-PR ledger) is still `not-started` above this one. Its own entry says building it is the owner's call and the decisions log (2026-11-24) records no owner decision, so it is skipped again; 12 depends on nothing in it. No open PRs exist (research-context.md), so there is no duplicate to avoid.

## Findings

State of the project's docs today (read 2026-11-25):
- `projects/nightloop/` has **no README**. Docs exist per feature only: `docs/github-actions.md`, `docs/reviewer-benchmark.md`, `plugin/README.md`. A newcomer cannot find what the tool is, how to install it, or what `run` does.
- CLI surface to document (from PLAN.md, m7/m9/m10/m11): `run`, `run --dry-run`, `report`, `schedule {install,uninstall,status}`, `schedule-gha`, `bench`. Exit codes live in `cli/exit_codes.py` (0 ok, 1 environment, 2 usage/config, 3 refused, 4 usage-limited, 5 no metrics, 6 job running, 7 ungraded, 8 bench stale, 9 bench never run). Package is `nightloop` 0.0.1, `requires-python >=3.11`, script `nightloop = nightloop.cli.main:main` (pyproject.toml).
- `python3 -I` cannot import the package from the source tree (PLAN m7), so the quickstart must say `cd projects/nightloop && python3 -m nightloop ...` or `pip install -e .`.

Incident per gate: sourced from this repo's own records, not invented. A gate whose incident I could not source is marked so.

| Gate (nightloop module) | Incident | Source |
|---|---|---|
| Freshness postcondition (`gates/freshness.py`) | 2026-09-01: the 600 s background-task ceiling killed the researcher, the CLI still exited 0, build ran on the previous night's note and the cycle shipped stale input. | `PIPELINE.md` "Phase postconditions"; `knowledge/gates-port-python.md` |
| Verdict parse, fail-closed (`gates/verdict.py`) | The bash `verdict.sh` fails closed on a missing or unreadable verdict. I found **no dated incident** for it in the files read. The 2026-11-12/13 health findings (BACKLOG.md) show verdict MISSING after permission refusals, which is the case it exists for, but that is a later event, not the origin. | `PIPELINE.md` line ~50; BACKLOG health 2026-11-16 |
| Clear handoff files first | A stale PASS would authorise the wrong cycle (rationale only, no dated incident found). | `knowledge/cycle-orchestration-gates.md` |
| Recovery to a unique branch | Failed cycles' dirt must not leak into the next; 2026-08-12 compaction work was carried to an `unshipped` branch. | `knowledge/pipeline-claim-lifecycle.md` line 31 |
| Stranded-claim reconcile | 2026-08-12: cycle 1 researched and built, the claim read `[ ]` again, the next night would have rebuilt it. Fixed 2026-08-13. | `knowledge/pipeline-claim-lifecycle.md` |
| Shipped-claim reconcile | PRs #33 (2026-08-16), #37, #38 (2026-09-02), #41 (2026-09-09) merged and left `[building]`; fixed 2026-09-10. | same |
| Verify gate (agentlab only; **not ported**) | Before 2026-11-15 only agent-chosen test runs existed in a cycle. | `PIPELINE.md` "The verify gate" |
| Usage-limit stop | Session-limit deaths on many 2026-09 nights (BACKLOG health findings). | `knowledge/cycle-orchestration-gates.md` |

Two consequences for the docs:
1. Do not claim nightloop has a verify gate; agentlab's has one and nightloop's m1-m11 do not. The architecture page must say so (honesty about what is ported).
2. The "incident" column must carry a source path and, where none exists, say "no recorded incident". Fabricated history is the main risk of this milestone.

Doc-structure guidance: Diátaxis (https://www.diataxis.fr/start-here/, a long-standing, widely used framework; date of page not shown) separates tutorial / how-to / reference / explanation and warns that mixing them confuses readers. Mapping: README = what and why plus links; quickstart = tutorial; architecture = explanation; the per-feature pages already present = how-to/reference. Use it as a layout rule only; no need to adopt it wholesale.

Drift is the real engineering problem. `knowledge/doc-transcript-drift.md` and `examples/readme-transcript-check/` show that pasted output rots and diff-scoped review cannot see it. So any command output in the new docs should be (a) reproducible offline (`run --dry-run`, `--help`) and (b) checked by a test, following the pattern m10 already uses (a golden file pinned byte for byte).

## Build proposal

**Intent.** Give nightloop a README, `docs/quickstart.md` and `docs/architecture.md`, plus a test that fails when the docs name something that no longer exists. Out of scope: any code change to nightloop behaviour, a docs site, a diagram toolchain, adoption on a second repo (m13), the 7c ledger, writing the live-run transcripts (those are human steps and must be labelled as such).

**Files** (all under `projects/nightloop/`):
- `README.md`: what it is (one paragraph from PLAN Goal), status ("never run live"), install, a 5-line quickstart pointer, command table, exit-code table, links to every doc.
- `docs/quickstart.md`: tutorial. Steps: install; write a `nightloop.toml` (start from `examples/agentlab.nightloop.toml` or `plugin.nightloop.toml`); `nightloop run --dry-run`; read the plan; install the plugin; one real night as a flagged human step; `nightloop report`.
- `docs/architecture.md`: the night as a sequence (research, build, review, ship), then one section per gate with: what it checks, what it does on failure, the incident (table above, with sources), and the test file that pins it. Includes an explicit "not in nightloop yet" list (verify gate, fix round, CI wait, auto-merge, 7c).
- `tests/test_docs.py`: the drift guard (below).

**Behavioral spec / invariants.**
- D1. Every relative link in README and `docs/*.md` resolves to an existing file (anchors excluded or checked, builder's choice, stated).
- D2. Every `nightloop <subcommand>` named in the docs is a real argparse subcommand (obtain the set from `cli/main.py`'s parser, not a hand list).
- D3. The exit-code table in the README equals `ExitCode` (names and values), generated or compared, not retyped unchecked.
- D4. Every gate section in architecture.md names a module path that exists and a test file that exists.
- D5. Every incident row cites a path that exists in the repo, or says exactly "no recorded incident".
- D6. The quickstart's dry-run output block (if included) equals real `main(["run","--dry-run",...])` output against a fixed temp repo, byte for byte, or is omitted. No hand-typed output.
- D7. The README states "has not been run live" until a human records otherwise, and lists the unverified human steps by linking to the existing known-limits sections rather than copying them.
- D8. No docs file is empty and no TODO/placeholder text remains.

**Failure modes.** A renamed subcommand, moved module, or changed exit code makes `test_docs.py` fail naming the doc and the stale reference. A missing doc file fails D1 or D4. Tests only read files; no network, no model, no key.

**Interfaces.** `test_docs.py` helpers only: `links(md_text) -> list[str]`, `subcommands_in(md_text) -> set[str]`, `exit_code_table(md_text) -> dict[str,int]`. Pure functions over text, with file reads in the test methods. Stdlib only (`re`, `pathlib`, `argparse` introspection or `ExitCode`).

**Acceptance criteria ("it works").**
1. `cd projects/nightloop && python3 -I -m unittest discover -v` passes, with the 803 existing tests unchanged and the new docs tests added.
2. Mutation checks, each fails at least one new test: rename a module named in architecture.md; change an `ExitCode` value; add `nightloop frobnicate` to the quickstart; break a relative link; delete an incident row's source path.
3. `nightloop run --dry-run --repo <tmp> --config examples/plugin.nightloop.toml` is run exactly as the quickstart writes it, and exits 0.
4. A reader checklist in the PR body: README answers "what, for whom, status, install, first command" in the first screen.

## Open questions

- No dated incident was found for the verdict gate or the clear-handoff rule; the builder should grep `git log`/`logs/` for one before writing, and otherwise write "no recorded incident". I did not search git history (no shell).
- Whether the docs should live in the README's Diátaxis shape or stay as flat pages is a taste call; the proposal assumes flat.
- `pip install -e .` was recorded as working in PLAN (m7) but the Homebrew 3.12 `plistlib` problem (m9 (f)) means the quickstart should name 3.11 or 3.13 as the tested interpreters.
- Date of the Diátaxis page could not be confirmed; it is used as a layout rule only.

Sources:
- https://www.diataxis.fr/start-here/
- Repo: `PIPELINE.md`, `knowledge/pipeline-claim-lifecycle.md`, `knowledge/gates-port-python.md`, `knowledge/cycle-orchestration-gates.md`, `knowledge/doc-transcript-drift.md`, `projects/nightloop/PLAN.md`
