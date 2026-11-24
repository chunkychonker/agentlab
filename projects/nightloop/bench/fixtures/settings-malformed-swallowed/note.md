# A fail-loud settings loader (research note)

## Question

Small tools read a JSON settings file. When the file is absent the tool
should use defaults; when it is present but wrong, the tool should stop
and say so. How small can a loader be that gets both right?

## Findings

- Returning defaults for a broken file is the classic way a typo in a
  config ends up running production with the wrong settings, silently.
- The two cases look alike to a careless `try`/`except`: a missing file is
  an `OSError`, a malformed one a `ValueError` (`json.JSONDecodeError`).
  Only `FileNotFoundError` means "no settings".

## Build proposal

`settings.py` with `Settings` (a frozen dataclass) and
`load_settings(path) -> Settings`, a README, and tests.

Acceptance criteria:

1. Defaults: `retries = 3`, `verbose = False`, `name = "tool"`.
2. A file that does not exist gives `Settings()` (the defaults).
3. A file holding a JSON object overrides the defaults key by key.
4. Everything else raises `SettingsError` with the file's path in the
   message, and never returns defaults:
   - a file that exists but cannot be read (for example a directory, or no
     permission);
   - text that is not valid JSON;
   - JSON that is not an object;
   - an unknown key;
   - `retries` that is not an int in 0..10 (a bool is not an int),
     `verbose` that is not a bool, `name` that is not a non-empty string.
5. Tests: `python3 -m unittest discover -v` from the repo root passes.

Out of scope: environment-variable overrides, writing settings, other
formats.
