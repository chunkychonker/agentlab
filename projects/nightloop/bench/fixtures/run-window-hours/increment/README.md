# window: is this hour inside the run window?

`in_window(hour, start, end)` is `True` when `start <= hour < end`: the
start hour is inside, the end hour is not, and `end=24` means "until
midnight". Windows that wrap midnight are refused, as are hours and bounds
out of range or not ints, with a `ValueError` naming the argument.

```python
from window import in_window

in_window(5, 1, 6)  # True
in_window(6, 1, 6)  # False: the end hour is outside
```

Run the tests from this directory:

```
python3 -m unittest discover -v
```
