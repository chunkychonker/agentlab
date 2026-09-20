# Retrying a flaky network preflight: what to steal from curl, what not to

`.pipeline/run.sh`'s nightly preflight checks `api.anthropic.com` and
`github.com` reachability once and aborts the whole run on failure. Two real
nights (`logs/run-2026-08-31_114702.log`, `logs/run-2026-09-11_020004.log`)
were lost this way to what was presumably a transient VPN/DNS blip — each a
full 0/2 night. This note records what's durable from researching the fix:
`curl`'s own retry flags don't fit, and this repo already has a formula and a
testing idiom that do.

## `curl --retry` does not cover this failure shape by default

`check_reachable` does `curl -sS -I --connect-timeout 5 --max-time 20 -o
/dev/null <url>` — a HEAD request. A VPN-down box fails this with a DNS
resolution failure or a connection that never establishes, **not** an HTTP
error code. curl's built-in `--retry` only retries "a timeout, an FTP 4xx
response code or an HTTP 408/429/500/502/503/504/522/524" by default
([`retry.md`](https://github.com/curl/curl/blob/master/docs/cmdline-opts/retry.md),
fetched 2026-09-18) — a refused connection needs `--retry-connrefused`
explicitly, and a DNS failure needs the broadest flag,
[`--retry-all-errors`](https://github.com/curl/curl/blob/master/docs/cmdline-opts/retry-all-errors.md)
(curl ≥7.71.0, 2020 — long-stable, low staleness risk, unlike anything in the
fast-moving agent/LLM space). curl's own docs call `--retry-all-errors` "the
sledgehammer of retrying," say **"do not use this option by default,"** and
recommend handling targeted retry logic in the calling script instead — which
is exactly the shape this repo already uses elsewhere (see below), so that's
what a network-preflight retry should do too, not a curl flag.

curl's own default backoff, for reference, is 1s doubling to a 10-minute cap
(`--retry-delay` overrides to a fixed delay; `--retry-max-time` caps the total
retry budget) — the same *shape* (exponential, capped) as the formula below,
just with curl's own numbers.

## This repo already has the formula and the injection pattern — reuse both

The Python side already implements the general backoff shape:
`examples/tool-error-policy/policy.py:191`,
`backoff_delay(attempt, *, base, cap, jitter) -> min(base * 2**(attempt-1),
cap) * jitter`. A bash port for the network preflight drops `jitter` — jitter
exists to desynchronize *multiple concurrent* callers hitting a shared
resource, and a single unattended nightly cron job has no peers to
desynchronize from, so there's nothing for it to protect against here.

For testing the retry **loop** itself (not just the pure decision/delay
functions) offline, the move is the same one [[bash-3.2-testable-scripts]]
already documents generally, applied here concretely: override `check_reachable`
and `sleep` with fake functions before calling the loop, so a test proves the
attempt count and the zero-sleeps-on-first-success case without touching a
real network or waiting in real time — the same trick `.pipeline/test_gates.sh`
already uses (N0's `declare -f check_reachable` verbatim extraction from
`run.sh`, S1-S7's fake `stash_strays` side effects).

## A message change is safe here specifically because of how the log gets read

Any change to the `NETWORK UNREACHABLE ... Aborting.` message text is safe to
make (e.g. adding an attempt count) *because* [[pipeline-run-log-shapes]]
already establishes the classifier never matches `run.sh`'s own wording — it
reads the log's **last content line** and checks only whether it ends in the
literal string `Aborting.` (vs. `=== done` vs. a bare, nothing-after-it phase
header). Keep that ending and the sentinel substring, and the message body
itself is free to change. This is the general lesson: before editing any
string a downstream parser might key on, check *how* it's actually consumed
(here, "last line + suffix", never a full-string match) rather than assuming
worst-case coupling.

## A concrete second instance of "log lines can be about a different night"

Confirmed live 2026-09-18: `grep -l "NETWORK UNREACHABLE" logs/run-*.log`
matches `logs/run-2026-09-02_134705.log`, but that night's own preflight
passed — the match is the pipeline-observer phase quoting `run-2026-08-31`'s
closing line into *its own* night's report table. [[pipeline-run-log-shapes]]
already documents this pattern in the abstract; this is a confirmed concrete
case of it, worth remembering as a standing gotcha whenever grepping
`logs/run-*.log` for a phrase: a hit does not mean *that night* produced it.

## Related

- [[bash-3.2-testable-scripts]] — the inject-the-side-effect-as-a-function-name
  pattern this note's loop-testing approach is a direct application of
- [[pipeline-run-log-shapes]] — the log-ending taxonomy and the
  never-match-the-message-text classification contract this note leans on,
  plus the same log-quoting gotcha in the abstract
- [[tool-failure-taxonomy]] — the Python-side sibling of this note's retry
  formula, and the general retry/report/abort taxonomy
- Dated research: `research/2026-09-18-network-preflight-retry.md`
