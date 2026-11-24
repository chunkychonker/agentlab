# slug: URL slugs from titles

`slugify(text, max_len=60)` turns a title into a URL-safe slug:
NFKD-normalised, ASCII only, lowercase, runs of other characters collapsed
to one `-`, no leading or trailing `-`, at most `max_len` characters. It
raises `ValueError` when nothing is left (`"!!!"`) or `max_len` is not a
positive int.

```python
from slug import slugify

slugify("Café au lait, 2026!")  # "cafe-au-lait-2026"
```

Run the tests from this directory:

```
python3 -m unittest discover -v
```
