# Keeping user paths inside a root directory (research note)

## Question

A tool serves files from one directory and takes the file name from its
user. What is the smallest correct check that the resolved path cannot
leave that directory?

## Findings

- `..` segments, absolute paths and symlinks are the three classic ways
  out. `Path.resolve()` removes all three before the check, so the check
  must compare resolved paths.
- Comparing the two paths as strings is not enough: `/srv/data-old/x`
  starts with the string `/srv/data`, but it is not inside `/srv/data`.
  The comparison has to be by path components (`Path.is_relative_to`, or
  `os.path.commonpath`).

## Build proposal

`containment.py` with `resolve_under(root, user_path) -> Path` and
`EscapeError`, a README, and tests.

Acceptance criteria:

1. `resolve_under(root, user_path)` resolves `root / user_path` (following
   symlinks) and returns it when it is `root` itself or inside it.
2. It raises `EscapeError` (a `ValueError`) naming `user_path` when the
   resolved path is outside the resolved root, including:
   - a `..` escape (`../etc/passwd`);
   - an absolute `user_path` (`/etc/passwd`);
   - a symlink inside the root that points outside it;
   - a sibling directory whose name starts with the root's name: with
     root `/srv/data`, the user path `../data-old/secret.txt` must raise.
3. `user_path` may name a file that does not exist yet (the check is on
   the path, not the file).
4. An empty `user_path` raises `EscapeError` (it names no file).
5. Tests: `python3 -m unittest discover -v` from the repo root passes.

Out of scope: Windows drive letters, case-insensitive file systems, races
between the check and a later open.
