# URL slugs from titles (research note)

## Question

Notes and posts need a URL-safe name derived from their title. What is a
small, predictable rule that never yields an empty or ragged slug?

## Findings

- Unicode NFKD normalisation splits accented letters into a base letter
  and a combining mark, so dropping the non-ASCII marks turns "Café" into
  "Cafe" instead of "Caf".
- A slug that ends in a hyphen after truncation looks broken in a URL; cut
  first, then strip.
- An empty slug is a collision waiting to happen; refuse it.

## Build proposal

`slug.py` with `slugify(text, max_len=60) -> str`, a README, and tests.

Acceptance criteria:

1. NFKD-normalise `text`, drop every non-ASCII character, lowercase.
2. Every run of characters outside `a-z0-9` becomes one `-`; leading and
   trailing `-` are removed.
3. The result is at most `max_len` characters, cut at `max_len` and then
   stripped of trailing `-`.
4. `ValueError` if the result would be empty (for example `"!!!"` or
   `"日本"`), or if `max_len` is not an int >= 1 (a bool is not an int).
5. Tests: `python3 -m unittest discover -v` from the repo root passes.

Out of scope: transliterating non-Latin scripts, uniqueness against
existing slugs.
