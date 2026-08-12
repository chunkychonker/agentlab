# Testing bash orchestration on this box (bash 3.2)

The pipeline's orchestrator is a shell script, so anything worth trusting in
`.pipeline/run.sh` needs to be testable without a network, an API key, or a real
`claude -p` call. This note records the environment constraint that shapes how,
and the injection pattern that makes it possible.

## The constraint: bash 3.2, and only bash 3.2

Verified 2026-08-12 on this machine:

```
which -a bash        -> /bin/bash        (the only one; no Homebrew bash)
/bin/bash --version  -> GNU bash, version 3.2.57(1)-release (arm64-apple-darwin25)
which bats           -> not found
which shellcheck     -> not found
```

macOS still ships bash 3.2 (the last GPLv2 release). Both `#!/bin/bash` and the
documented `bash .pipeline/run.sh` invocation resolve to it here.

**Do not use** in any pipeline script: `declare -A` (associative arrays),
`mapfile`/`readarray`, `${var,,}` / `${var^^}`, `&>>`, or `|&`. All are bash 4+.
Safe and already used: the `=~` regex test, `$(( ))`, `local`, `$(...)`, and
`set -uo pipefail`.

## The pattern: inject the side effect as a command name

The reason shell orchestration usually goes untested is that its interesting
decisions are welded to expensive actions (`claude -p`, `git push`). Separate
them the same way the Python examples in this repo do — pure decision inward,
side effect at the edge — by passing the **name of a function** and calling it
indirectly. Verified working in bash 3.2, including exit-code propagation:

```bash
runner() { local action="$1"; shift; "$action" "$@"; }

fake() { echo "called with: $*"; return 0; }
runner fake 6          # -> called with: 6

boom() { return 3; }
runner boom; echo $?   # -> 3     (the callee's code, not the wrapper's)
```

A test then injects a fake action; production injects the one that shells out.
This is the shell equivalent of injecting `sleep`/`jitter` into a retry policy
(see [[tool-failure-taxonomy]]) or a fake client into an agent loop (see
[[tool-use-loop]]).

**Make the fake do real work.** A fake that only increments a counter lets the
test assert on something the test itself configured. A fake that genuinely
mutates the fixture file forces the code under test to re-read state and decide
again — which is what proves idempotence rather than asserting it.

## Don't reach for bats

[bats-core](https://github.com/bats-core/bats-core) is alive (v1.14.0,
2026-07-21) but is not installed here, and the project posted a
[call for maintainers](https://github.com/orgs/bats-core/discussions/1023) in
Nov 2024 citing burnout. For a dozen assertions it buys sugar in exchange for a
`brew install` on a machine that runs unattended under launchd.

The house pattern is already set by `eval/run_reviewer_eval.sh`: plain bash,
`set -uo pipefail`, one line printed per case, exit code as the verdict.

## Two gotchas worth remembering

- `grep -c PATTERN file` with **zero matches prints `0` and exits 1**. Under
  `set -o pipefail` in a `$(...)` capture that makes the whole assignment fail,
  so `|| true` is required — and removing it as "dead code" silently breaks the
  empty case only.
- A function can only be called after its definition line has been *executed*.
  In a long top-to-bottom script, moving a call earlier than the helper it uses
  fails at runtime, on exactly the nights the branch is taken. Check placement
  against definition order, not against reading order.

## Related

- [[pipeline-claim-lifecycle]] — the orchestration logic this is used to test
- [[tool-failure-taxonomy]] — the same inject-the-side-effect idea in Python
