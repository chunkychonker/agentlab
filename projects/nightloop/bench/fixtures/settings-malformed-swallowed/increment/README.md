# settings: a fail-loud JSON settings loader

`load_settings(path)` returns a frozen `Settings`:

- no file at `path`: the defaults (`retries=3`, `verbose=False`, `name="tool"`);
- a JSON object: the defaults, overridden key by key;
- anything else (an unreadable file, malformed JSON, a non-object, an
  unknown key, a value of the wrong type or range): `SettingsError`
  naming the file.

```python
from pathlib import Path
from settings import load_settings

settings = load_settings(Path("tool.json"))
```

Run the tests from this directory:

```
python3 -m unittest discover -v
```
