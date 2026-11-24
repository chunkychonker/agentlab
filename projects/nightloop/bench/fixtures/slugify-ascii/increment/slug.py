"""URL slugs from titles: ASCII, lowercase, hyphen-separated, never empty."""

from __future__ import annotations

import re
import unicodedata

_NOT_SLUG = re.compile(r"[^a-z0-9]+")
_SEPARATOR = "-"


def slugify(text: str, max_len: int = 60) -> str:
    """The slug for ``text``; ``ValueError`` if it would be empty or ``max_len`` is not an int >= 1."""
    if isinstance(max_len, bool) or not isinstance(max_len, int) or max_len < 1:
        raise ValueError(f"max_len must be an int >= 1, got {max_len!r}")
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = _NOT_SLUG.sub(_SEPARATOR, ascii_text.lower()).strip(_SEPARATOR)
    slug = slug[:max_len].rstrip(_SEPARATOR)
    if slug == "":
        raise ValueError(f"{text!r} has no ASCII letters or digits to make a slug from")
    return slug
