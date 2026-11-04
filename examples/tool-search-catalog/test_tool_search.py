"""Offline self-test for the tool-search catalog. No API key, no network, stdlib only.

Run:
    python test_tool_search.py

Each test maps to an acceptance criterion in
research/2026-11-04-tool-search-deferred-tools.md (Build proposal, criteria 1-5).
Criterion 6 (live token measurement) is measure.py and needs a key.
"""

from __future__ import annotations

import copy

import recorded
from agent import Reply, ToolSearchFailed, UnexpectedStop, run_agent
from blocks import discovered_tools, followup_user_turn, pending_client_tool_uses, search_failures
from catalog import HOT_TOOLS, make_catalog
from local_search import SearchError, search
from measure import describe_saving
from toolset import SearchVariant, ToolsetError, build_tools, validate_tools


def _raises(exc_type, fn, *args, **kwargs) -> str:
    """Call fn; return the exception message if it raised exc_type, else fail."""
    try:
        fn(*args, **kwargs)
    except exc_type as exc:
        return str(exc)
    raise AssertionError(f"expected {exc_type.__name__} from {fn.__name__}{args}")


# --------------------------------------------------------------------------- #
# Criterion 1: the tools array
# --------------------------------------------------------------------------- #

def test_build_tools_shape() -> None:
    for variant in SearchVariant:
        tools = build_tools(make_catalog(40), HOT_TOOLS, variant)
        assert len(tools) == 41, "search tool + every catalog tool, none dropped"
        search_tools = [t for t in tools if t.get("type") == variant.tool_type]
        assert search_tools == [{"type": variant.tool_type, "name": variant.tool_name}]
        assert tools[0] == search_tools[0], "search tool first"
        deferred = [t for t in tools if t.get("defer_loading") is True]
        assert len(deferred) == 40 - len(HOT_TOOLS)
        hot = [t for t in tools if t.get("name") in HOT_TOOLS]
        assert len(hot) == len(HOT_TOOLS) and all("defer_loading" not in t for t in hot)
        validate_tools(tools)
    assert SearchVariant.REGEX.tool_type == "tool_search_tool_regex_20251119"
    assert SearchVariant.BM25.tool_type == "tool_search_tool_bm25_20251119"
    catalog = make_catalog(40)
    snapshot = copy.deepcopy(catalog)
    build_tools(catalog, HOT_TOOLS, SearchVariant.REGEX)
    assert catalog == snapshot, "build_tools must not mutate the catalog"
    print("ok  build_tools: one search tool first, 37 of 40 deferred, hot tools plain, valid")


def test_validate_tools_rejects_documented_400s() -> None:
    good = build_tools(make_catalog(12), HOT_TOOLS, SearchVariant.REGEX)
    deferred_search = [{**good[0], "defer_loading": True}, *good[1:]]
    all_deferred = [{**t, "defer_loading": True} for t in make_catalog(12)]
    defer_and_cache = [*good[:-1], {**good[-1], "cache_control": {"type": "ephemeral"}}]
    duplicate = [*good, dict(good[1])]
    messages = {
        _raises(ToolsetError, validate_tools, deferred_search),
        _raises(ToolsetError, validate_tools, all_deferred),
        _raises(ToolsetError, validate_tools, defer_and_cache),
        _raises(ToolsetError, validate_tools, duplicate),
    }
    assert len(messages) == 4, f"each rule needs its own message: {messages}"
    assert "not in catalog" in _raises(ToolsetError, build_tools, make_catalog(12), frozenset({"get_wether"}),
                                       SearchVariant.REGEX)
    print("ok  validate_tools: 4 invalid toolsets -> 4 distinct ToolsetErrors; unknown hot name rejected")


# --------------------------------------------------------------------------- #
# Criterion 2: the local model of the regex search
# --------------------------------------------------------------------------- #

def test_local_search() -> None:
    catalog = make_catalog(40)
    assert search(catalog, "weather") == ["get_weather"]
    assert search(catalog, "get_.*_data") == ["get_stock_data", "get_sales_data"]
    assert search(catalog, "WEATHER") == ["get_weather"], "case-insensitive"
    assert search(catalog, "ticker") == ["get_stock_data"], "argument names and descriptions are searched"
    assert len(search(catalog, "_")) == 5, "default limit is 5 (every name has an underscore)"
    assert search(catalog, "_", limit=2) == ["read_file", "send_email"], "limit caps, catalog order"
    assert search(catalog, "teleport") == [], "no match is an empty list, not an error"
    too_long = search(catalog, "a" * 201)
    bad_regex = search(catalog, "(")
    assert isinstance(too_long, SearchError) and too_long.error_code == "invalid_tool_input"
    assert isinstance(bad_regex, SearchError) and bad_regex.error_code == "invalid_tool_input"
    assert isinstance(search(catalog, "a" * 200), list), "exactly 200 chars is allowed"
    print("ok  local search: substring, regex, case, arg fields, limit, empty, invalid_tool_input")


# --------------------------------------------------------------------------- #
# Criterion 3: reading the blocks and building the reply turn
# --------------------------------------------------------------------------- #

def test_blocks() -> None:
    content = recorded.search_then_call()
    assert discovered_tools(content) == ["get_weather"]
    pending = pending_client_tool_uses(content)
    assert [b["id"] for b in pending] == [recorded.CLIENT_ID]
    turn = followup_user_turn(content, {recorded.CLIENT_ID: "41F"})
    ids = [block["tool_use_id"] for block in turn["content"]]
    assert ids == [recorded.CLIENT_ID] and recorded.SERVER_ID not in ids
    assert all(block["type"] == "tool_result" for block in turn["content"])
    _raises(ValueError, followup_user_turn, content, {recorded.CLIENT_ID: "41F", recorded.SERVER_ID: "x"})
    _raises(ValueError, followup_user_turn, content, {})
    assert search_failures(content) == []
    assert search_failures(recorded.empty_search_then_answer()) == [], "an empty match is not a failure"
    assert discovered_tools(recorded.empty_search_then_answer()) == []
    print("ok  blocks: discovered [get_weather]; only the toolu_ id is answered; srvtoolu_ answer refused")


# --------------------------------------------------------------------------- #
# Criteria 4-5: the loop, with a fake Send replaying recorded turns
# --------------------------------------------------------------------------- #

class FakeSend:
    """Replays scripted Replies and records a deep copy of every request."""

    def __init__(self, *replies: Reply):
        self._replies = list(replies)
        self.requests: list[dict] = []

    def __call__(self, request: dict) -> Reply:
        self.requests.append(copy.deepcopy(request))
        return self._replies.pop(0)


def test_loop_echoes_turn_and_resends_full_tools() -> None:
    tools = build_tools(make_catalog(40), HOT_TOOLS, SearchVariant.REGEX)
    first = recorded.search_then_call()
    send = FakeSend(Reply("tool_use", first), Reply("end_turn", recorded.final_answer()))
    result = run_agent(send, tools, "What's the weather in Madison?")

    assert result.text == "It is 41F and overcast in Madison." and result.requests == 2
    assert result.discovered == ("get_weather",)
    first_req, second_req = send.requests
    assert second_req["tools"] == first_req["tools"] == tools, "same full tools array every request"
    assert sum(1 for t in second_req["tools"] if t.get("defer_loading") is True) == 37
    assistant = second_req["messages"][1]
    assert assistant == {"role": "assistant", "content": recorded.search_then_call()}, \
        "assistant turn echoed unchanged, search blocks included"
    results = second_req["messages"][2]["content"]
    assert [r["tool_use_id"] for r in results] == [recorded.CLIENT_ID]
    assert '"temp_f": 41' in results[0]["content"]
    print("ok  loop: turn 1 echoed unchanged, same 41 tools resent, tool_result only for toolu_")


def test_loop_fails_loudly_on_search_error() -> None:
    tools = build_tools(make_catalog(40), HOT_TOOLS, SearchVariant.REGEX)
    for code in ("invalid_tool_input", "unavailable", "too_many_requests"):
        send = FakeSend(Reply("end_turn", recorded.search_error(code)))
        try:
            run_agent(send, tools, "q")
        except ToolSearchFailed as exc:
            assert exc.failure.error_code == code and code in str(exc)
        else:
            raise AssertionError(f"{code}: expected ToolSearchFailed")
        assert len(send.requests) == 1, "no further request after a failed search"
    send = FakeSend(Reply("pause_turn", recorded.search_then_call()))
    assert "pause_turn" in _raises(UnexpectedStop, run_agent, send, tools, "q")
    print("ok  loop: search error raises ToolSearchFailed(error_code); unknown stop_reason raises")


def test_loop_rejects_invalid_toolset_before_sending() -> None:
    send = FakeSend()
    _raises(ToolsetError, run_agent, send, [{**t, "defer_loading": True} for t in make_catalog(12)], "q")
    assert send.requests == [], "nothing sent"
    print("ok  loop: invalid toolset raises ToolsetError before any request")


def test_describe_saving() -> None:
    assert describe_saving(1000, 250) == "deferred is 750 tokens smaller (75.0% of 1000)"
    assert describe_saving(1000, 1000).startswith("no difference")
    assert "LARGER" in describe_saving(1000, 1100)
    _raises(ValueError, describe_saving, 0, 10)
    print("ok  measure: describe_saving reports smaller / no difference / LARGER")


if __name__ == "__main__":
    tests = [
        test_build_tools_shape,
        test_validate_tools_rejects_documented_400s,
        test_local_search,
        test_blocks,
        test_loop_echoes_turn_and_resends_full_tools,
        test_loop_fails_loudly_on_search_error,
        test_loop_rejects_invalid_toolset_before_sending,
        test_describe_saving,
    ]
    for test in tests:
        test()
    print(f"\nAll {len(tests)} self-tests passed.")
