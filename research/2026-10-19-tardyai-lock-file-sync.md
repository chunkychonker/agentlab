# TardyAI milestone 1: lock file in sync (2026-10-19)

## Question
Why does `npm ci` fail in `pkgbc231600/ui/react` at `main` = ed7c81c, and what is the smallest change that makes `verify.sh` get past it?

## Findings
Everything below was reproduced locally on 2026-10-19 (node v24.5.0, npm 11.5.1). It came from running commands on a copy in `/tmp`, not from web sources, and no web research was needed for this milestone.

- `npm ci` aborts with EUSAGE: `package.json` and `package-lock.json` are out of sync. The lock file was last touched in the baseline commit `6d21d7a`.
- It lists about 18 missing entries. They are all transitive of two peer dependencies that the lock never recorded:
  - `@testing-library/dom@10.4.2` is the peer of `@testing-library/react@16`. It also pulls in `@types/aria-query`, `dom-accessibility-api`, `lz-string` and `pretty-format@27`.
  - `@mui/material@5.18.0` and `@mui/icons-material@5.18.0` are the peers of `mui-chips-input@2.1.x`, which declares `^5.0.0`. They pull in `@mui/system`, `@mui/utils`, `@mui/types`, `@mui/styled-engine`, `@mui/private-theming`, `@popperjs/core`, `react-transition-group` types and others.
  - Nothing in `src/` imports `@mui` or `@testing-library/dom` directly. Those packages are needed only as peers.
- Control experiment: I copied `package.json` and `package-lock.json` to `/tmp/lk`, then ran `npm install --package-lock-only`.
  - Afterwards `npm ci --dry-run` succeeds (1335 packages).
  - The diff against the current lock is 413 added lines and a few removed lines. The removals are only `"libc": [...]` arrays and some `"dev": true` flags, which is npm-version churn.
  - No existing top-level version moved.
- Neither `package.json` nor `package-lock.json` is a protected or hot file in `logs/ext-context.md`. The milestone is safe to take.
- `.npmrc` contains only `fund=false`. There is no registry override, so the `resolved` URLs stay on `registry.npmjs.org`.

Decision to note: this adds no new direct dependency. The lock file simply records peers that npm already resolves on `npm install`. That complies with the "no new runtime deps" decision in `PLAN.md`.

## Build proposal
Intent: make `npm ci` succeed on a fresh checkout by regenerating `package-lock.json` from the unchanged `package.json`. Out of scope: any `package.json` edit, version bumps, adding tests, and fixing any later `verify.sh` step beyond what the lock change unblocks.

Where: only `pkgbc231600/ui/react/package-lock.json` in the checkout (`/Users/steeb/agentlab-work/tardyai`).

Procedure for the builder:
1. In `pkgbc231600/ui/react`, run `npm install --package-lock-only --no-audit --no-fund`. Do not use `rm -rf node_modules` or delete the lock; regenerating from scratch would drift versions.
2. Check `git diff --stat` touches only `package-lock.json`.
3. Check that no existing entry's `version` changed. A script that compares the `packages` map before and after, ignoring added keys, is enough. `git diff` should show additions plus the `libc`/`dev` metadata churn only.
4. Run `bash projects/tardyai/verify.sh /Users/steeb/agentlab-work/tardyai`.

Acceptance criteria:
- `npm ci --no-audit --no-fund` exits 0 in a clean `node_modules`.
- `package.json` is byte-identical to `main`.
- The lock diff shows no version change to any package already in the lock.
- `verify.sh`: the `npm ci` step passes, and the rest reports honestly. Later steps (build, tests, bundle) may surface new failures. If one does, record it in `PLAN.md` as a finding. Only fix it in this PR if it is caused by the lock file itself.
- The PR checklist says Studio will install from this lock, so ask the human to pull and confirm the preview still loads.

Failure modes: if npm resolves a peer to a version that breaks `tsc` or the lint step, pin nothing yourself. Report it and stop, because the fix would need a decisions-log entry.

## Open questions
- Whether Studio's Node and npm versions match the local ones is unknown. The `libc` metadata churn comes from npm 11, so lock output may differ slightly under Studio's npm. This is harmless for `npm ci`, but it is unverified.
- Whether `build` and `test` pass after `npm ci` is unknown until the builder runs `verify.sh`.
