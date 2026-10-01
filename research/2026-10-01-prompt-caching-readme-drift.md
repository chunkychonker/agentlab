# Prompt-caching README: repairing real transcript drift

Date: 2026-10-01. Mode: demo. Backlog item: `fix (health 2026-09-21): examples/prompt-caching-tool-loop/ — README transcript drift: line 24 ...` (marked `[researching]`).

Why this item: no plain `[ ]` feature item remains in the Coding agents / Skills / MCP sections (all `[done #N]` or `[stranded ...]`), `gh pr list --state open` is empty, and most other `[ ]` entries are run-log noise (session limits, missing logs) that is not buildable. 2026-09-30's note explicitly left this one unclaimed as "a separate one-line README fix". Stranded-branch topics were skipped to avoid duplicating built work. Note: the `hn-search` MCP server failed to connect (missing `examples/mcp-hn-search/.venv/bin/python3`); no web research was needed anyway, as this is an internal defect and the primary source is the repo itself.

## Question

Is the health finding real, and what is the smallest change that makes `examples/prompt-caching-tool-loop/README.md` true again and keeps it from rotting the same way?

## Findings

No external sources (internal defect; everything below was run today, 2026-10-01).

- Confirmed real. From `examples/prompt-caching-tool-loop/`:
  `python3 ../readme-transcript-check/check_transcript.py . -- python3 test_placement.py` prints `DRIFT`: README block (lines ~125-152) stops at the "a full request spends exactly the four breakpoints" line and says `All 23 self-tests passed`; the suite now emits 7 more `ok` lines (the TTL tests added by PR #48) and `All 30 self-tests passed`.
- Cause: the PR #48 (1-hour TTL) change grew `test_placement.py` from 23 to 30 assertions and `test_report.py` from 17 to 31, updated the file table (it already says "30 assertions" / "31 assertions"), but not the pasted transcripts. Same failure mode as `knowledge/doc-transcript-drift.md`: code self-counts, README hardcodes.
- A second, unchecked block has drifted too. The README's second transcript (the `test_report.py` block, ends `All 17 self-tests passed`, README line ~183) is stale against today's `All 31 self-tests passed`. `check_transcript.py` takes exactly one marked block per README (only `Expected output:` above the first block carries the marker), so the sweep cannot see the second. Hand-diffing is the only way today. The README says as much ("shown for reading").
- Not drift: the live transcript (captured 2026-08-31, ~line 205-220) is a billed run and is correctly out of scope; do not touch. The 3,667-token figures belong to that run.
- Related caveat in the README prose, line ~89-92 region and the cost paragraph, still refers to the default marker; those stay accurate (the default is byte-identical, per the test).

## Build proposal

Intent: make the README's two offline self-test transcripts byte-true to what the suites print today, and make the second one verifiable so it cannot silently rot again. Out of scope: any code change to `placement.py`/`report.py`/`main.py`; the live transcript; changing `check_transcript.py`'s one-marked-block-per-README rule (a separate, larger change to a different example).

Where: edit `examples/prompt-caching-tool-loop/README.md` only. No new directory.

Steps:
1. Replace the `test_placement.py` "Expected output" block with the real current stdout (`python3 test_placement.py`, 30 `ok` lines + `All 30 self-tests passed with no key and no network.`).
2. Replace the `test_report.py` block with the real current stdout (31 lines + `All 31 ...`). Generate both by running the commands, not by hand-editing; paste verbatim.
3. Make the second block verifiable without touching the checker: since the sweep checks one marked block, do NOT add a second `Expected output` marker (that would trigger `AmbiguousTranscript`, which refuses the whole README). Instead, remove the drift hazard from prose: keep the existing sentence that tells the reader how to check, and add a `<!-- transcript-check: skip — second suite, shown for reading; the placement suite above is the checked block -->`-style note ONLY if the directive syntax in `examples/readme-transcript-check/README.md` (line ~150) applies to a block without a marker; read that README first and use whatever form it documents. If no form applies, leave the block as plain fenced text and just state in the README that it was regenerated on 2026-10-01 with the command.
4. Add a one-line comment to the README near the file table noting that the counts in the table must equal the `All N` lines (they do today: 30 and 31) so a future editor sees the coupling.

Acceptance criteria (all checkable):
- `cd examples/prompt-caching-tool-loop && python3 ../readme-transcript-check/check_transcript.py . -- python3 test_placement.py` exits 0 and reports MATCH (it printed DRIFT before).
- Running `python3 test_report.py > /tmp/out.txt` and diffing against the README's second fenced block (extract with a short shell/python one-liner) shows zero differences.
- `grep -n "All 23\|All 17" examples/prompt-caching-tool-loop/README.md` returns nothing.
- `git diff --stat` shows only `examples/prompt-caching-tool-loop/README.md` (plus the backlog/mark-done housekeeping the pipeline does).
- The sweep over the whole repo (`examples/readme-transcript-check/sweep.py`, run as its README documents) no longer lists `prompt-caching-tool-loop` as DRIFT, and lists no new finding.
- `python3 test_placement.py` and `python3 test_report.py` still pass (no code touched).

Failure modes: pasting a transcript from a different Python version or a run with wall-clock-dependent lines (the suites assert sub-second runtime; the output lines themselves are static, verified today, so they should be deterministic); accidentally adding a second marker line (check with the checker, which refuses it).

## Open questions

- Whether the `transcript-check: skip` directive is valid on a block with no `Expected output` marker above it (the readme-transcript-check README documents it as sitting above the marker line); the builder must read it and choose, per step 3.
- Whether to extend `check_transcript.py` to handle N marked blocks per README, which is the real fix for the second-block hazard. Deliberately not proposed today: it is a distinct intent in a different example, and could be a future backlog item.
