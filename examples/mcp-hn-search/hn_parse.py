"""The pure core: mapping raw HN Algolia API responses to caller-facing
summaries. No imports beyond the stdlib, no I/O, no `httpx` -- per the
repo's "core logic never imports I/O" rule, this file is fully testable
with plain dict fixtures and nothing else.

Intent: strip Algolia-internal noise (`_highlightResult`, `exhaustive*`,
etc. -- a duplicate, HTML-highlighted copy of every field, irrelevant to an
LLM caller) and cap the unbounded `children` comment tree before either
reaches a tool's return value.
"""

import html

# Deliberate cap, stated as an invariant: summarize_item never returns more
# than this many comments regardless of how many children the raw item has.
MAX_TOP_COMMENTS = 5

_HIT_FIELDS = ("objectID", "title", "url", "author", "points", "num_comments", "created_at")
_ITEM_FIELDS = _HIT_FIELDS


def summarize_hits(raw: dict) -> list[dict]:
    """Map raw['hits'] (a /search or /search_by_date response) to a list of
    plain dicts containing only the fields useful to a caller.

    Invariant: an empty `hits` list returns `[]`, not an error -- a
    zero-match search is a valid, successful API response, and this
    function must not conflate "no results" with "failure."
    """
    return [{field: hit.get(field) for field in _HIT_FIELDS} for hit in raw["hits"]]


def summarize_item(raw: dict) -> dict:
    """Map a raw /items/:id response to a summary dict with the story's
    fields plus a capped, HTML-unescaped list of top-level comments.

    Invariant: `top_comments` never has more than `MAX_TOP_COMMENTS`
    entries, regardless of how many `children` the raw item carries (a
    popular story's full comment tree can run to thousands of nodes).
    """
    summary = {field: raw.get(field) for field in _ITEM_FIELDS}
    children = raw.get("children") or []
    top_comments = []
    for child in children[:MAX_TOP_COMMENTS]:
        text = child.get("text")
        top_comments.append({
            "author": child.get("author"),
            "text": html.unescape(text) if text else text,
        })
    summary["top_comments"] = top_comments
    return summary
