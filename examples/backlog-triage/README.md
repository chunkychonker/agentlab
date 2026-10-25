# Backlog triage: closing stale health findings

On 2026-10-25 every feature item in this repo's `BACKLOG.md` was `[done]`. All 47
remaining `- [ ] ` items were health findings filed automatically by
`.pipeline/run.sh`, and most of them were old one-off observations: per-run
outcomes ("`run-2026-09-13_020004` shipped 0/2"), "no run log for <date>",
"phase X exited non-zero, Nx". Nothing ever closes those. That has two effects:

- `backlog_count_unclaimed` counts 47, so `backlog_ensure_stocked` never runs the
  replenish phase. The backlog says it's stocked while holding no buildable work.
- The researcher takes the topmost `[ ]`, so cycles land on noise. The 2026-10-25
  cycle claimed "no run log for 2026-08-17 through 2026-08-28", which can't be
  acted on because a log can't be produced after the fact.

This is a deterministic classifier. It reads a `BACKLOG.md`, sorts the unclaimed
health findings into historical observations and actionable items, reports the
raw and effective unclaimed counts, and can optionally close the historical ones
in place. Python 3.10+ standard library only. No key, no network, no model call.

From the research note:
[`research/2026-10-25-backlog-stale-health-triage.md`](../../research/2026-10-25-backlog-stale-health-triage.md).

## Design (layers 1-3)

- **Intent:** separate stale health findings from actionable ones and report the
  effective unclaimed count. Out of scope: editing `.pipeline/backlog.sh`,
  changing the replenish logic, any model call, closing non-health items, and
  judging whether a fix works.
- **Spec:**
  - Only lines matching `- [ ] fix (health YYYY-MM-DD): <body>` at column 0 are
    candidates. Every other `- [ ] ` line is always actionable.
  - The kind comes from the body's leading shape. The first match wins:
    a path as the first token (`examples/foo/`, `BACKLOG.md:216`) gives `other`.
    A `run-YYYY-MM-DD_HHMMSS` prefix gives `run-outcome`. A `no run log for`
    prefix gives `gap-night`. `phase '...' exited non-zero` gives `phase-exit`.
    An `Nx`/`N×` count anywhere gives `repeated-message`. Anything else is `other`.
  - `newest` is the latest real `YYYY-MM-DD` in the body. The filing date is
    excluded, and dates inside run ids count.
  - An item is **historical** iff its kind is not `other`, `newest` exists, and
    `today - newest > max_age_days` (default 7). A recognised kind with no body
    date stays actionable, so the rules err toward keeping an item.
  - `--apply` replaces only the `- [ ] ` prefix with
    `- [done by hand YYYY-MM-DD: stale <kind>] `. Every other byte, including
    CRLF and continuation lines, is unchanged. It writes only when at least one
    item is historical, via a temp file and a rename. A second `--apply`
    changes nothing.
  - A bad `--today`, a negative `--max-age-days`, or an unreadable or non-UTF-8
    file exits 2 with a message on stderr and writes nothing.
- **Interfaces:**
  - `classify.classify(line, line_no, today, max_age_days=7) -> Verdict`, where
    `Verdict = Actionable | Historical`. `Historical.newest` is a `date`, never
    `None`, and `Historical(kind=Kind.OTHER, ...)` raises. So the two illegal
    states (closing an `other`, closing a dateless item) can't be constructed.
    This replaces the note's single `Verdict(historical: bool, newest: date | None)`,
    which could represent both.
  - `backlog_lines.parse_unclaimed(text) -> list[tuple[int, str]]`,
    `backlog_lines.apply(text, verdicts, today) -> str`. `apply` raises
    `ValueError` if a verdict's line is no longer `- [ ] ` (stale verdicts).
  - `report.render_report(verdicts, today, max_age_days) -> str`
  - `triage.triage(text, today, max_age_days=7) -> list[Verdict]`, `triage.main(argv) -> int`.
    `main` is the only I/O. `today` is always passed in and never read from
    the clock.

| File | What it is |
|------|-----------|
| `classify.py` | Pure classifier for one line (kind, newest date, verdict). |
| `backlog_lines.py` | Pure reader/rewriter of the `- [ ] ` marker. |
| `report.py` | Pure renderer for the text report. |
| `triage.py` | CLI entry point: arguments, file read, atomic write. |
| `fixtures/health-findings-2026-10-25.md` | The real `## Health-check findings` section of `BACKLOG.md` as committed on `main` on 2026-10-25 (47 `[ ]` lines). |
| `test_triage.py` | Offline self-test, one check per acceptance criterion. |

## Marker compatibility with `.pipeline/backlog.sh`

`backlog_has_unresolved` treats every `- [` line except `- [done ` as still
queued, and that suppresses re-filing. A closed line starts with `- [done `, so
a genuine recurrence of the same finding re-files as new information, which is
the behavior the pipeline intends. The marker has no `#N`, so the health check's
`[done #N]` PR-number test ignores it. It also extends the `[done by hand
YYYY-MM-DD]` convention humans already use in the backlog. The self-test checks
all three properties against the fixture.

## Run the self-test (no API key, no network, no dependencies)

```bash
cd examples/backlog-triage
python3 test_triage.py
```

Expected output:

```
ok  the 2026-08-17..28 'no run log' finding is a historical gap-night, newest 2026-08-28
ok  a 3-day-old run outcome is actionable at max age 7 and historical at max age 2
ok  an examples/ transcript-drift finding is OTHER and never historical, however old
ok  a non-health '- [ ] ' item is always actionable and apply leaves it byte-identical
ok  a recognised kind with no date in its body stays actionable (fail safe)
ok  apply rewrites only the marker, keeps CRLF, skips [done #N]/[researching]/indented, is idempotent
ok  real backlog fixture: 47 raw unclaimed, 38 historical, 9 actionable at 2026-10-25
ok  closed lines leave backlog_has_unresolved, count as 9 unclaimed, and add no '[done #'
ok  CLI: dry run writes nothing, bad input exits 2, --apply closes 38 then is a no-op
ok  the README's CLI block is byte-identical to the real CLI output

All 10 self-tests passed.
```

## Run it on a backlog

Dry run (the default) prints the report and writes nothing. From this directory:

```bash
python3 triage.py fixtures/health-findings-2026-10-25.md --today 2026-10-25
```

CLI output on the committed fixture:

```
backlog triage (today 2026-10-25, max age 7 days)
unclaimed: 47 raw, 38 historical, 9 actionable

historical by kind:
  gap-night           9
  run-outcome        17
  phase-exit          6
  repeated-message    6

actionable:
  L60   run-outcome       newest 2026-10-19 is 6d old (<= 7)
        - [ ] fix (health 2026-10-23): run-2026-10-19_145923 — external:tardyai opened 0/1, b...
  L61   run-outcome       newest 2026-10-19 is 6d old (<= 7)
        - [ ] fix (health 2026-10-23): run-2026-10-19_160319 — log ends at "--- phase: cycle ...
  L62   run-outcome       newest 2026-10-19 is 6d old (<= 7)
        - [ ] fix (health 2026-10-23): run-2026-10-19_161057 — external:tardyai opened 0/1, b...
  L63   run-outcome       newest 2026-10-21 is 4d old (<= 7)
        - [ ] fix (health 2026-10-23): run-2026-10-21_020002 — external:tardyai opened 0/1, "...
  L64   run-outcome       newest 2026-10-22 is 3d old (<= 7)
        - [ ] fix (health 2026-10-23): run-2026-10-22_020001 — external:tardyai opened 0/1, "...
  L65   repeated-message  newest 2026-10-19 is 6d old (<= 7)
        - [ ] fix (health 2026-10-23): "You've hit your session limit" in build phase — 2× on...
  L66   repeated-message  newest 2026-10-22 is 3d old (<= 7)
        - [ ] fix (health 2026-10-23): "VERIFY: FAIL (1): routes stay in corridors" — 2× on 2...
  L67   run-outcome       newest 2026-10-19 is 6d old (<= 7)
        - [ ] fix (health 2026-10-23): run-2026-10-19_145923 phase 'cycle 1/1: build' — exite...
  L68   run-outcome       newest 2026-10-19 is 6d old (<= 7)
        - [ ] fix (health 2026-10-23): run-2026-10-19_161057 phase 'cycle 1/1: build' — exite...

dry run: pass --apply to close 38 historical item(s)
```

The self-test checks this block against the real output, byte for byte.

To close the historical items in place, add `--apply`. Try it on a copy:

```bash
cp fixtures/health-findings-2026-10-25.md /tmp/BACKLOG.md
python3 triage.py /tmp/BACKLOG.md --today 2026-10-25 --apply   # "applied: closed 38 item(s)"
python3 triage.py /tmp/BACKLOG.md --today 2026-10-25 --apply   # "nothing to apply"
```

**This cycle did not run `--apply` on the repo's real `BACKLOG.md`.** Whether to
do that is a human's call (see the note's open questions).

## What the numbers say

At 2026-10-25 with the 7-day default, 38 of the 47 unclaimed items are
historical, so the effective unclaimed count is **9**. All 9 come from the
2026-10-23 health run and are 3 to 6 days old (tardyai session limits and the
`routes stay in corridors` verify failure).

Classifications worth knowing about:

- `"its item is no longer in BACKLOG.md ..." ... 3x on 2026-09-12, -13, -14`
  is classed historical even though its text says the message keeps printing.
  That's safe because closing it doesn't hide anything. If the message recurs,
  health re-files it with new dates, since `[done ` lines are excluded from the
  dedupe.
- `phase 'cycle 1/1: build' (2026-09-21) exited 0 but failed ... 1x in-window`
  doesn't match the `exited non-zero` shape. Its `1x` makes it
  `repeated-message`, and it's historical. The underlying blind spot (a build
  landing only under `.pipeline/` goes STALE) is documented in `PIPELINE.md`.

## Limits and follow-up

- The 7-day threshold is a judgment call and isn't derived from data. The CLI
  exposes it as `--max-age-days`.
- The rules work on text shape. A health finding phrased in a new way falls
  through to `other` and stays actionable, so a wording change makes the tool
  keep items rather than close them.
- **Follow-up (a `.pipeline/` change for a human to review):** have the pipeline
  use this triage directly. `backlog_count_unclaimed` would count only
  actionable items, or a `run.sh` pass would run `--apply` before
  `backlog_ensure_stocked`. Either way a stale backlog would trigger replenish
  again. This example stays under `examples/` because `increment_built` only
  looks there and under `projects/` (`PIPELINE.md`).
