"""Offline self-test for the token-budget guard. No API key, no network.

Run:
    python test_guard.py

Each test maps to an acceptance criterion in
research/2026-10-16-token-budget-guard.md (Build proposal, criteria 1-4).
"""

from __future__ import annotations

from types import SimpleNamespace

import agent
from budget import BudgetExceeded, check_budget


# --------------------------------------------------------------------------- #
# Criterion 1: the pure decision
# --------------------------------------------------------------------------- #

def test_check_budget_table() -> None:
    assert check_budget(400, 500, turn=1) is None, "under the limit"
    assert check_budget(500, 500, turn=2) is None, "exactly at the limit is allowed"
    assert check_budget(501, 500, turn=3) == BudgetExceeded(turn=3, counted_tokens=501, limit=500)
    assert check_budget(300, 500, turn=1, reserve=200) is None, "reserve landing on the limit"
    assert check_budget(301, 500, turn=4, reserve=200) == BudgetExceeded(
        turn=4, counted_tokens=301, limit=500
    ), "reserve pushes it over"
    print("ok  check_budget: under / at / one-over / reserve boundary")


def test_check_budget_rejects_invalid_args() -> None:
    bad_calls = [
        dict(counted_tokens=-1, limit=500, turn=1),
        dict(counted_tokens=0, limit=0, turn=1),
        dict(counted_tokens=0, limit=-5, turn=1),
        dict(counted_tokens=0, limit=500, turn=0),
        dict(counted_tokens=0, limit=500, turn=1, reserve=-1),
    ]
    for kwargs in bad_calls:
        try:
            check_budget(**kwargs)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {kwargs}")
    print("ok  check_budget raises ValueError on invalid arguments")


# --------------------------------------------------------------------------- #
# Fake client: scripted counts and responses, records call order
# --------------------------------------------------------------------------- #

def _tool_use_response(n: int):
    return SimpleNamespace(
        stop_reason="tool_use",
        content=[
            SimpleNamespace(
                type="tool_use", id=f"toolu_{n}", name="calculator", input={"expression": f"{n} + {n}"}
            )
        ],
    )


def _final_response(text: str):
    return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)])


class FakeMessages:
    """count_tokens returns scripted counts; create returns scripted responses.

    ``log`` records ("count" | "create", number of messages in the request) in
    call order, so tests can assert counting happens before each send and on
    the same request.
    """

    def __init__(self, counts, responses, count_error: Exception | None = None):
        self._counts = list(counts)
        self._responses = list(responses)
        self._count_error = count_error
        self.log: list[tuple[str, int]] = []

    def count_tokens(self, **kwargs):
        assert "max_tokens" not in kwargs, "count_tokens does not accept max_tokens"
        self.log.append(("count", len(kwargs["messages"])))
        if self._count_error is not None:
            raise self._count_error
        return SimpleNamespace(input_tokens=self._counts.pop(0))

    def create(self, **kwargs):
        assert kwargs["max_tokens"] == agent.MAX_TOKENS
        self.log.append(("create", len(kwargs["messages"])))
        return self._responses.pop(0)

    def kinds(self) -> list[str]:
        return [k for k, _ in self.log]


def _client(counts, responses, count_error=None):
    return SimpleNamespace(messages=FakeMessages(counts, responses, count_error))


# Real requests carry MAX_TOKENS of reserve; offset the fake counts by it so
# the scenario reads in the proposal's numbers (100, 400, 900 vs limit 500).
R = agent.MAX_TOKENS


# --------------------------------------------------------------------------- #
# Criterion 2: climbing counts stop the loop on the right turn, unsent
# --------------------------------------------------------------------------- #

def test_stops_on_turn_three_without_sending() -> None:
    client = _client(
        counts=[100, 400, 900],
        responses=[_tool_use_response(1), _tool_use_response(2), _final_response("never sent")],
    )
    result = agent.run_guarded_agent(client, "q", token_limit=500 + R)

    assert result == BudgetExceeded(turn=3, counted_tokens=900, limit=500 + R), result
    assert client.messages.kinds() == ["count", "create", "count", "create", "count"]
    assert client.messages.kinds().count("create") == 2
    print("ok  climbing counts stop on turn 3; the over-budget request is never sent")


# --------------------------------------------------------------------------- #
# Criterion 3: generous limit runs to completion, count-before-create each turn
# --------------------------------------------------------------------------- #

def test_generous_limit_returns_final_text() -> None:
    client = _client(
        counts=[100, 400, 900],
        responses=[_tool_use_response(1), _tool_use_response(2), _final_response("done: 2 and 4")],
    )
    result = agent.run_guarded_agent(client, "q", token_limit=100_000)

    assert result == "done: 2 and 4", result
    assert client.messages.log == [
        ("count", 1), ("create", 1),
        ("count", 3), ("create", 3),
        ("count", 5), ("create", 5),
    ], client.messages.log  # each count measures the same request its create sends
    print("ok  generous limit: final text, one count before each create, same request")


# --------------------------------------------------------------------------- #
# Criterion 4: a failing meter fails closed
# --------------------------------------------------------------------------- #

def test_count_tokens_error_propagates_and_nothing_is_sent() -> None:
    boom = ConnectionError("count_tokens unreachable")
    client = _client(counts=[], responses=[_final_response("never sent")], count_error=boom)
    try:
        agent.run_guarded_agent(client, "q", token_limit=100_000)
    except ConnectionError as exc:
        assert exc is boom
    else:
        raise AssertionError("expected the count_tokens error to propagate")
    assert client.messages.kinds() == ["count"], client.messages.log
    print("ok  count_tokens error propagates; create is never called (fail-closed)")


# --------------------------------------------------------------------------- #
# Invariant kept from the original loop: max_turns still raises
# --------------------------------------------------------------------------- #

def test_max_turns_still_raises() -> None:
    client = _client(counts=[10] * 3, responses=[_tool_use_response(n) for n in range(3)])
    try:
        agent.run_guarded_agent(client, "q", token_limit=100_000, max_turns=3)
    except RuntimeError as exc:
        assert "did not finish" in str(exc)
    else:
        raise AssertionError("expected RuntimeError when max_turns is exceeded")
    print("ok  max_turns exhaustion still raises RuntimeError")


def main() -> int:
    tests = [
        test_check_budget_table,
        test_check_budget_rejects_invalid_args,
        test_stops_on_turn_three_without_sending,
        test_generous_limit_returns_final_text,
        test_count_tokens_error_propagates_and_nothing_is_sent,
        test_max_turns_still_raises,
    ]
    for t in tests:
        t()
    print(f"\nAll {len(tests)} self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
