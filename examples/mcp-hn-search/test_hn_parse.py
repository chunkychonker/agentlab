"""Offline, pure self-test for hn_parse.py -- no `httpx` import, no I/O at
all. Fixtures below mirror the response shapes confirmed live in
research/2026-08-06-mcp-hn-search.md: a /search hit carrying the
`_highlightResult` noise every real hit has, and an /items/:id response
modeled on the real items/1 (pg/sama) thread's shape, extended to 8
children to exercise the 5-comment cap.

Run: python3 test_hn_parse.py
"""

import hn_parse

SEARCH_RESPONSE_FIXTURE = {
    "hits": [
        {
            "objectID": "44444444",
            "title": "Claude does X",
            "url": "https://example.com/claude-x",
            "author": "Philpax",
            "points": 128,
            "num_comments": 42,
            "created_at": "2026-08-05T12:00:00.000Z",
            "created_at_i": 1785000000,
            "story_id": 44444444,
            "_tags": ["story", "author_Philpax"],
            "_highlightResult": {
                "title": {"value": "<em>Claude</em> does X", "matchLevel": "full"},
            },
        }
    ],
    "nbHits": 1,
    "page": 0,
    "nbPages": 1,
    "hitsPerPage": 2,
    "processingTimeMS": 3,
    "query": "claude",
    "params": "query=claude&tags=story&hitsPerPage=2",
    "exhaustiveNbHits": True,
}

ITEM_RESPONSE_FIXTURE = {
    "objectID": "1",
    "title": "Y Combinator",
    "url": None,
    "author": "pg",
    "points": 61,
    "num_comments": 8,
    "created_at": "2006-10-09T18:21:51.000Z",
    "children": [
        {
            "author": f"commenter{i}",
            "text": f"Comment number {i} &amp; more",
            "parent_id": 1,
            "type": "comment",
        }
        for i in range(8)
    ],
}


def test_summarize_hits_strips_highlight_result_and_maps_fields() -> None:
    result = hn_parse.summarize_hits(SEARCH_RESPONSE_FIXTURE)
    assert len(result) == 1, result
    hit = result[0]
    assert "_highlightResult" not in hit, hit
    assert hit == {
        "objectID": "44444444",
        "title": "Claude does X",
        "url": "https://example.com/claude-x",
        "author": "Philpax",
        "points": 128,
        "num_comments": 42,
        "created_at": "2026-08-05T12:00:00.000Z",
    }, hit
    print("ok  summarize_hits strips _highlightResult and maps only the useful fields")


def test_summarize_hits_empty_is_not_an_error() -> None:
    result = hn_parse.summarize_hits({"hits": []})
    assert result == [], result
    print("ok  summarize_hits({'hits': []}) returns [] without raising")


def test_summarize_item_caps_top_comments_at_five() -> None:
    result = hn_parse.summarize_item(ITEM_RESPONSE_FIXTURE)
    assert len(result["top_comments"]) == hn_parse.MAX_TOP_COMMENTS, result["top_comments"]
    assert result["top_comments"][0] == {"author": "commenter0", "text": "Comment number 0 & more"}, (
        result["top_comments"][0]
    )
    print("ok  summarize_item on an 8-child fixture returns exactly 5 top_comments, HTML-unescaped")


def main() -> int:
    test_summarize_hits_strips_highlight_result_and_maps_fields()
    test_summarize_hits_empty_is_not_an_error()
    test_summarize_item_caps_top_comments_at_five()
    print("\nAll 3 self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
