# npm ci fails on peer-dep lock drift

- `npm ci` errors with EUSAGE ("Missing: X from lock file") when `package.json` and the lock disagree. `npm install` repairs this silently, which hides the drift. Hence use `npm ci` as the build gate.
- A common cause is a lock generated before a peer dependency was added to the tree. Examples are `@testing-library/dom` (peer of `@testing-library/react@16`) and `@mui/material` (peer of `mui-chips-input`). Nothing in `src/` imports them directly.
- Safe fix: `npm install --package-lock-only` only adds the missing entries and moves no existing version. Test it on a copy in `/tmp` first, then check with `npm ci --dry-run`.
- Expect harmless churn from newer npm (`libc` arrays, `dev` flags).
- Seen in: [[tardyai-external-project]], first reproduced 2026-10-19.
