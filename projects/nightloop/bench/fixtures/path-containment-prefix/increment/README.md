# containment: keep a user-supplied path inside a root directory

`resolve_under(root, user_path)` resolves `root / user_path`, following
symlinks, and returns it if it is inside `root`. Otherwise it raises
`EscapeError` (a `ValueError`): for `..` escapes, absolute paths, symlinks
that point outside, and an empty `user_path`. The file need not exist.

```python
from pathlib import Path
from containment import resolve_under

path = resolve_under(Path("/srv/data"), "reports/2026.csv")
```

Run the tests from this directory:

```
python3 -m unittest discover -v
```
