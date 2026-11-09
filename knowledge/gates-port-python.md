# Porting the bash gates to Python

Durable facts for [[pipeline-claim-lifecycle]]-adjacent gate work (nightloop m2).

- The verdict gate is fail-closed: exactly one `^VERDICT:` line at column 0 with
  word `PASS` or `FAIL`. Empty file is MISSING, non-empty without a valid line is
  MALFORMED. Match the whole captured word, not a prefix (`PASSED` is MALFORMED).
- In Python, split lines on `\n` only. `str.splitlines()` also splits on
  `\x0b`, `\x0c`, `\x85`, ` `, ` `, which would let a smuggled line
  count as column 0.
- Freshness compares the file mtime (float) against an integer floor with
  `>=`. Do not truncate the mtime to int first. The floor is the phase's start,
  not the run's, so cycle 1's artifact cannot satisfy cycle 2.
- STALE is distinct from MISSING: the 2026-09-01 failure had a full `research/`
  of old notes. Prune `.venv`, `__pycache__`, `node_modules`, `.git`, or build
  noise forges freshness.
- Keep the core pure (text or mtimes in, enum out); do file I/O in a shell.

See `research/2026-11-09-nightloop-gates-library.md`.
