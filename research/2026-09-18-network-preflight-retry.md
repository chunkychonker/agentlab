# Network preflight retry: give a VPN/DNS blip a few seconds before losing the whole night

## Question

`BACKLOG.md:213` (topmost unclaimed item, demo mode): when `.pipeline/run.sh`'s
network preflight finds `api.anthropic.com` or `github.com` unreachable, should
it retry with backoff before aborting the entire night, and if so, what's a
safe, offline-testable shape for that retry — in this repo's own established
bash idiom, not a hand-wave?

## Mode / claim check

`.pipeline/mode` reads `demo`. `BACKLOG.md` top-to-bottom scan (`grep -n '^- \['`)
confirms every item above line 213 is `[done #N]` or `[stranded cycle/...]`;
213 is genuinely the topmost `[ ]`. `gh pr list --state open` shows three open
PRs — #40 (context-editing vs prompt-caching trade), #39 (pipeline schedule
window), #36 (`feat/phase-postconditions`, gate each phase on its artifact).
Read both #39's and #36's PR bodies in full: neither touches the network
reachability check itself. #39 adds a time-of-day gate *before* the network
preflight (unrelated decision); #36 adds an artifact-freshness postcondition
*after* a phase runs (unrelated failure mode — background-task timeouts, not
network). No collision. `git branch -a` / `examples/` listing show no other
in-flight or merged work on this either. Environment note: the `hn-search` MCP
server was unavailable this session (missing venv); not a material gap here —
this is bespoke internal pipeline infra, not a public library with
practitioner discussion to mine, so WebSearch/WebFetch against curl's own docs
(below) is the right primary source anyway.

## Findings

### The current check has no retry, and two real nights have already paid for it

`.pipeline/run.sh:87-95` (read directly, not from memory):

```bash
CONNECT_TIMEOUT_S=5
RESPONSE_TIMEOUT_S=20
check_reachable () {
  curl -sS -I --connect-timeout "$CONNECT_TIMEOUT_S" --max-time "$RESPONSE_TIMEOUT_S" -o /dev/null "$1"
}
if ! check_reachable "https://api.anthropic.com" || ! check_reachable "https://github.com"; then
  echo "NETWORK UNREACHABLE (api.anthropic.com / github.com) — check VPN. Aborting." | tee -a "$LOG"
  exit 1
fi
```

One shot, no retry, immediate `exit 1` — the whole night (both cycles) is
lost. `grep -rn "retry\|backoff\|sleep" .pipeline/*.sh` (excluding tests)
turns up nothing for this check; the repo's only other `sleep` is an unrelated
5×`sleep 2` poll of `gh pr view --json mergeable` inside the auto-merge gate
(`run.sh:419-423`).

Checking every real run log (`logs/run-*.log`, 52 files) for genuine
occurrences — and this needed care, see the gotcha below —
turns up exactly two clean, post-fix, sole-line aborts:

- `logs/run-2026-08-31_114702.log` — entire log is one line, the abort message,
  nothing before or after.
- `logs/run-2026-09-11_020004.log` — same shape, exactly one line.

Both are already named in `BACKLOG.md` (line 213, the item claimed here, and
line 228, a later duplicate-shaped finding for the 09-11 night). A third,
`logs/run-2026-08-07_131432.log`, is the same one-line shape but predates
PR #31 (merged 2026-08-15T17:17:40Z per `gh pr view 31 --json mergedAt`), which
fixed a *different* bug in this same check — `--max-time 5` used to bound the
whole GET transfer rather than just the connection, so a reachable-but-slow
link on 2026-08-15 read as unreachable (`run.sh:79-86` documents this
directly, and `test_gates.sh`'s N2/N3 cases reproduce it with a local
slow-drip HTTP server). 2026-08-07 can't be attributed to a real VPN outage
with confidence — it may be the same pre-fix false positive — so it is not
counted as evidence either way.

**Net: 2 confirmed genuine aborts in the ~5 weeks since the last fix to this
check, each a full 0/2 night**, out of 52 total run logs on disk. Not frequent,
but each occurrence is a total loss, and the fix is cheap and structurally
safe to add (bounded, and true no-ops for the healthy majority of nights — see
acceptance criteria below).

**Gotcha found while counting:** `grep -l "NETWORK UNREACHABLE" logs/run-*.log`
also matches `logs/run-2026-09-02_134705.log`, but reading that file shows its
*own* preflight passed fine — the phrase there is the pipeline-observer phase
*quoting* the 08-31 night's closing line back into its own night's report
table. This is the exact phenomenon `knowledge/pipeline-run-log-shapes.md`
already documents in the abstract ("the pipeline-observer phase reads *old*
`logs/run-*.log` files and echoes their closing lines... so a line sitting
mid-file is routinely about a different night") — this is a second, concrete,
confirmed instance of it, added to that note below.

### There is no retry logic anywhere in `.pipeline/` to imitate — but there is a formula to imitate

The Python side of the repo already has this exact shape, one layer up:
`examples/tool-error-policy/policy.py:191` —

```python
def backoff_delay(attempt: int, *, base: float, cap: float, jitter: float) -> float:
    """min(base * 2 ** (attempt - 1), cap) * jitter"""
```

with `jitter` a required, injected parameter (`policy.py:177`,
`jitter_factor`), justified there by the classic thundering-herd concern
(many callers retrying the same upstream in sync). `knowledge/INDEX.md`
already records this as the "testing retry/backoff logic offline: inject
`sleep` and `jitter` as parameters" cross-cutting pattern.

### `curl`'s own `--retry` family was considered and rejected for this

Fetched directly from curl's own doc source (2026-09-18; curl's cmdline-opts
docs are the canonical source, and `--retry-all-errors` has been stable since
7.71.0 in 2020, so this is a low-churn area unlike the agent/LLM ecosystem —
not flagging it as possibly stale):

- [`docs/cmdline-opts/retry.md`](https://github.com/curl/curl/blob/master/docs/cmdline-opts/retry.md) —
  by default `--retry` only retries "a timeout, an FTP 4xx response code or an
  HTTP 408, 429, 500, 502, 503, 504, 522 or 524 response code," with 1s→10min
  doubling backoff (`--retry-delay` overrides to a fixed delay,
  `--retry-max-time` caps the total retry budget).
- [`docs/cmdline-opts/retry-all-errors.md`](https://github.com/curl/curl/blob/master/docs/cmdline-opts/retry-all-errors.md) —
  the only flag that would also cover a DNS failure or a straight-up refused
  connection (this box's actual failure shape — `check_reachable` uses
  `-I`/HEAD against a live TLS endpoint, so a VPN-down failure is a DNS or TCP
  problem, not an HTTP 5xx). curl's own docs describe this flag as "the
  sledgehammer of retrying," explicitly warn **"do not use this option by
  default,"** flag the duplicate-data risk when output is piped/redirected,
  and recommend handling retry logic in the calling shell script instead for
  anything more targeted than "retry absolutely everything."

Two things follow: (1) `--retry-connrefused` alone would not be enough — it
only adds refused-connection to the retryable set, not DNS failure, and this
box's actual failure mode is unconfirmed between the two; (2) curl's own
authors point at exactly the pattern this repo already uses elsewhere
(hand-rolled retry in the calling script, pure decision separated from the
side effect) — so this increment follows that, not `--retry-all-errors`.
`-o /dev/null` means the duplicate-data caveat doesn't apply here regardless,
but the "handle it yourself" recommendation stands on its own.

### The existing log-classification contract, read directly, constrains the message text

`.claude/agents/agentlab-pipeline-observer.md`'s classifier and
`knowledge/pipeline-run-log-shapes.md` (read in full) both establish: a run
log's fate is read from its **last content line** — `=== done ...` (OK/PARTIAL)
vs. a line ending in `Aborting.` (ABORTED-with-message) vs. a bare
`--- phase: NAME (model: MODEL) ---` with nothing after it (ABORTED-silent).
Critically, that same note states the reason string "is built only from
[the phase-header and done markers] plus the verbatim last line — **never by
matching `run.sh`'s own failure wording**" — matching prose would be
coupling-by-meaning between files that are never edited together. This means
the abort message can be safely enriched (attempt count, elapsed backoff) as
long as it (a) remains the log's last line and (b) still ends in the literal
string `Aborting.` — nothing downstream parses substrings of the message
itself.

### Existing offline-test idiom to reuse, not reinvent

`.pipeline/test_gates.sh`'s N0-N5 cases (`test_gates.sh:640-728`) already test
`check_reachable` itself, by extracting it **verbatim** out of `run.sh` via
`declare -f` after sourcing the constants (`eval "$(sed -n
'/^CONNECT_TIMEOUT_S=/,/^}/p' "$RUN_SH")"`) rather than duplicating the
function's source in the test file — the comment there is explicit: "so
extracting the function alone would leave it undefined under `set -u`."
`knowledge/bash-3.2-testable-scripts.md`'s general pattern (inject the side
effect as a function name; verified working in bash 3.2, exit codes included)
is the one to follow for the retry **loop** specifically: a test overrides
`check_reachable` and `sleep` with fakes before extracting/calling the loop
function, so the loop's control flow is provable without touching the real
network or waiting in real time — the same trick S1-S7 already use for
`stash_strays` and N0-N5 use for `check_reachable`.

## Build proposal

**Where:** `.pipeline/` — this is pipeline-hygiene code, the project's own
infrastructure, same category as PRs #26, #28, #31, #36, #39 (all live in
`.pipeline/`, not `examples/`).

### Layer 1 — Intent

When the network preflight finds either host unreachable, retry a small,
bounded number of times with exponential backoff before aborting, so a
VPN-reconnect or DNS blip that clears within roughly a minute doesn't cost the
whole night. **Out of scope:** retrying the `gh auth status` / `git remote
get-url origin` checks that follow (no incident in 52 run logs shows either
flapping independently of the network check); re-invoking `run.sh` later in
the same launchd slot or waking a sleeping Mac (PR #39's deliberately-deferred
`pmset` territory); touching `check_reachable`'s own `-I`/timeout contract
(PR #31's fix, must stay byte-identical since N0-N5 depend on extracting it
verbatim); anything beyond the preflight step (in-run SDK retries are a
separate, already-documented layer — `knowledge/sdk-retry-behavior.md`).

### Layer 2 — Behavioral spec

- **Inputs:** the boolean pass/fail of `check_reachable` against both hosts,
  observed up to `NETWORK_MAX_ATTEMPTS` times.
- **Outputs:** either the preflight proceeds (some attempt within the budget
  succeeded — unchanged downstream behavior) or `run.sh` still does exactly
  what it does today: print a line ending in `Aborting.` and `exit 1`.
- **Invariants:**
  - The retry **decision** and the backoff **delay** are pure integer
    functions — no `curl`, `sleep`, or `date` inside them — mirroring
    `worktree_disposition`'s "echo a word, return code mirrors it" contract
    and the Python sibling's `min(base * 2**(attempt-1), cap)` formula (no
    jitter needed here: jitter exists to desynchronize multiple concurrent
    callers hitting a shared resource, and this is a single unattended cron
    job with no peers to desynchronize from).
  - A first-attempt success is byte-for-byte the same code path as today: zero
    added `sleep`, zero added log lines, zero added wall-clock cost on a
    healthy night (the common case, ~50 of the last 52 nights).
  - `check_reachable` itself stays unmodified — N0-N5 already prove its
    contract; this increment only wraps it in a loop.
- **Failure modes:** malformed inputs to either pure function (`attempt < 1`,
  non-numeric, `cap < base`) fail loudly — message to stderr, return code `2`
  (distinct from the real `0`/`1` outcomes so a caller can't mistake a
  programming error for a legitimate `GIVE_UP`) — per Protocol §4, no silent
  defaulting. Exhausting all attempts falls through to the existing `exit 1`
  abort, unchanged in kind.
- **Acceptance criteria** (concrete, checkable, all offline — no network, no
  key):
  1. `bash .pipeline/test_gates.sh` exits 0, 0 failed, with new cases added
     alongside N0-N5 (same file, same numbering convention).
  2. Pure-function cases for the retry decision: attempt < max →
     `RETRY`/return 0; attempt ≥ max → `GIVE_UP`/return 1; malformed
     (`attempt=0`, non-numeric `max_attempts`) → return 2.
  3. Pure-function cases for the backoff delay: at least three attempt values
     reproducing `min(base * 2**(attempt-1), cap)` exactly, including one
     attempt number high enough to prove the cap actually caps (even though
     `NETWORK_MAX_ATTEMPTS=3` means production never reaches it — the
     function's contract is tested on its own terms, not just at the one call
     site); malformed (`base<=0`, `cap<base`) → return 2.
  4. A loop-level case using the extraction-plus-fake-injection idiom
     (`declare -f` after sourcing, same as N0): with a fake `check_reachable`
     that fails once then succeeds, the loop proceeds after exactly 2
     attempts and calls a fake `sleep` exactly once, with the expected delay
     value; with a fake that always fails, the loop gives up after exactly
     `NETWORK_MAX_ATTEMPTS` attempts, its last emitted line still ends in
     `Aborting.`, and the fake `sleep` is called exactly
     `NETWORK_MAX_ATTEMPTS - 1` times — never more, never fewer.
  5. Succeeding on the very first attempt calls the fake `sleep` **zero**
     times — the criterion that matters most in practice, since most nights
     the network is already fine.
  6. Worst-case added time before giving up is small and bounded: with the
     proposed defaults (`NETWORK_MAX_ATTEMPTS=3`, `NETWORK_BASE_DELAY_S=15`,
     `NETWORK_CAP_DELAY_S=60`) that's two sleeps (15s + 30s = 45s) plus up to
     two extra rounds of the already-bounded `check_reachable` calls (each
     capped at `RESPONSE_TIMEOUT_S=20`s per host today) — on the order of a
     couple of minutes total, trivial against a lost night and small enough
     not to meaningfully compete with PR #39's daytime-drift concern.

### Layer 3 — Interfaces (signatures only; bodies are the builder's job)

New file `.pipeline/network_retry.sh` (bash 3.2 only — no `declare -A`,
`mapfile`, `${var,,}`, per `knowledge/bash-3.2-testable-scripts.md`; sourced,
pure, no I/O, added as one more entry in `run.sh`'s existing
`for lib in backlog verdict health pipeline_health preflight; do . ...; done`
loop):

```bash
# Tunables — a single place to retune if a future run log shows a "gave up
# after N attempts" line recurring (see Open questions).
NETWORK_MAX_ATTEMPTS=3       # 1 initial probe + up to 2 retries
NETWORK_BASE_DELAY_S=15
NETWORK_CAP_DELAY_S=60

# Should the attempt that just failed (1-based) be followed by a retry?
# Prints RETRY or GIVE_UP; return code mirrors (0=RETRY, 1=GIVE_UP), same
# echo+return-code contract as worktree_disposition in preflight.sh.
# Failure modes: attempt<1, max_attempts<1, or either non-numeric -> message
# to stderr, return 2.
network_retry_decision () { local attempt="$1" max_attempts="$2"; ...; }

# Seconds to wait before the retry that follows failed attempt `attempt`
# (1-based). min(base * 2**(attempt-1), cap), integer seconds only (bash 3.2
# has no float arithmetic).
# Failure modes: attempt<1, base<=0, cap<base, or non-numeric -> message to
# stderr, return 2.
network_backoff_delay_s () { local attempt="$1" base="$2" cap="$3"; ...; }
```

`run.sh:87-95` — replace the current single-shot `if ! check_reachable ... ;
then abort; fi` with a small loop built from these two functions plus the
existing real `check_reachable` and a real `sleep`, logging each retry
(`tee -a "$LOG"`) so a future cycle can see empirically whether 3 attempts was
enough. `check_reachable` itself and its two constants
(`CONNECT_TIMEOUT_S`/`RESPONSE_TIMEOUT_S`) stay byte-identical — N0-N5's
verbatim extraction depends on that.

`.pipeline/test_gates.sh` — new assertions immediately after N0-N5, same
`assert_eq`/`pass`/`fail` helpers already in use, no new test framework.

## Open questions

- **True blip duration is unmeasured.** Nothing on this box (no VPN client
  log, no packet capture) is accessible from here to say whether the
  2026-08-31 / 2026-09-11 outages were 10-second reconnects or multi-hour
  drops. The proposed defaults (3 attempts, 15s/30s backoff) are a bounded,
  low-cost guess, not a measured optimum — safe either way (worst case it
  just doesn't help and the night is lost exactly as today, plus ~1 minute),
  but genuinely unconfirmed. Acceptance criterion 6's logging is designed to
  produce the evidence a future cycle would need to retune this.
- **Whether the `gh auth status`/`git remote` checks ever flap alone** —
  no evidence either way in 52 run logs; left out of scope on that basis, but
  the same shape would transfer if a future finding shows one flapping.
- **Root cause of the VPN gating itself** is outside this repo's visibility
  and outside what code can fix; this increment only shortens the box's own
  tolerance for a transient failure, it cannot prevent one.
