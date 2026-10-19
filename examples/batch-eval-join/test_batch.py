"""Offline self-test for the batch eval join. No API key, no network, stdlib only.

Run:
    python test_batch.py

Each test maps to an acceptance criterion in
research/2026-10-19-message-batches-eval.md (Build proposal, criteria 1-7).
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import batch_cli
from cost import batch_cost_usd
from join import (
    Canceled,
    DuplicateResult,
    Errored,
    Expired,
    Grade,
    MalformedResult,
    MissingResult,
    Succeeded,
    UnknownCustomId,
    grade,
    join_results,
)
from plan import (
    MAX_REQUESTS_PER_BATCH,
    BatchTooLarge,
    DuplicateCaseId,
    EvalCase,
    InvalidCustomId,
    PollDecision,
    build_requests,
    poll_decision,
)

FIXTURE = Path(__file__).parent / "fixtures" / "results.jsonl"
CASES = batch_cli.EVAL_CASES


def _fixture_lines() -> list[dict]:
    return [json.loads(line) for line in FIXTURE.read_text().splitlines() if line]


def _raises(exc_type: type[BaseException], fn, *args) -> BaseException:
    try:
        fn(*args)
    except exc_type as exc:
        return exc
    raise AssertionError(f"expected {exc_type.__name__} from {fn.__name__}{args!r}")


def test_join_returns_case_order_from_scrambled_fixture() -> None:
    lines = _fixture_lines()
    fixture_order = [line["custom_id"] for line in lines]
    case_order = [case.id for case in CASES]
    assert sorted(fixture_order) == sorted(case_order)
    assert fixture_order != case_order, "fixture must be scrambled to prove anything"

    joined = join_results(CASES, lines)
    assert [case.id for case, _ in joined] == case_order
    outcomes = {case.id: outcome for case, outcome in joined}
    assert outcomes["planet-red"] == Succeeded(
        text="Mars", input_tokens=19, output_tokens=4, service_tier="batch"
    )
    assert outcomes["chem-water"] == Expired()
    assert outcomes["arith-17x23"] == Errored("overloaded_error", "req_01FixtureOverloaded0001")
    assert outcomes["capital-france"] == Errored("invalid_request_error", "req_01FixtureInvalid00001")


def test_retryable_follows_error_type() -> None:
    outcomes = dict((c.id, o) for c, o in join_results(CASES, _fixture_lines()))
    assert outcomes["capital-france"].retryable is False
    assert outcomes["arith-17x23"].retryable is True
    assert Errored("api_error", None).retryable is True


def test_grades_never_fail_an_unjudged_case() -> None:
    graded = {c.id: grade(c, o) for c, o in join_results(CASES, _fixture_lines())}
    assert graded == {
        "capital-france": Grade.NO_RESULT,  # errored, invalid_request_error
        "arith-17x23": Grade.NO_RESULT,  # errored, overloaded_error
        "chem-water": Grade.NO_RESULT,  # expired
        "planet-red": Grade.PASS,
    }
    mars = CASES[3]
    assert grade(mars, Succeeded("It is Jupiter.", 19, 5, "batch")) is Grade.FAIL
    assert grade(mars, Succeeded("the red planet is MARS", 19, 7, None)) is Grade.PASS
    assert grade(mars, Canceled()) is Grade.NO_RESULT


def test_each_failure_is_a_distinct_named_error() -> None:
    lines = _fixture_lines()
    missing = _raises(MissingResult, join_results, CASES, lines[:-1])
    unknown = _raises(
        UnknownCustomId, join_results, CASES, lines + [{"custom_id": "ghost", "result": {"type": "expired"}}]
    )
    dup_result = _raises(DuplicateResult, join_results, CASES, lines + [lines[0]])
    dup_case = _raises(DuplicateCaseId, build_requests, [CASES[0], CASES[0]], "m", 1)
    bad_id = _raises(InvalidCustomId, EvalCase, "has space", "p", "x")
    bad_id_long = _raises(InvalidCustomId, EvalCase, "a" * 65, "p", "x")

    assert "capital-france" in str(missing)
    assert "ghost" in str(unknown)
    assert "planet-red" in str(dup_result)
    assert "capital-france" in str(dup_case)
    assert "has space" in str(bad_id) and "a" * 65 in str(bad_id_long)
    errors = [missing, unknown, dup_result, dup_case, bad_id]
    kinds = {type(e) for e in errors}
    assert len(kinds) == 5, kinds
    for e in errors:
        for other in kinds - {type(e)}:
            assert not isinstance(e, other), (type(e), other)


def test_malformed_result_lines_fail_loudly() -> None:
    _raises(MalformedResult, join_results, CASES[:1], [{"custom_id": "capital-france", "result": {"type": "weird"}}])
    _raises(MalformedResult, join_results, CASES[:1], [{"custom_id": "capital-france", "result": {"type": "succeeded"}}])
    _raises(MalformedResult, join_results, CASES[:1], [{"result": {"type": "expired"}}])


def test_build_requests_shape_and_rejections() -> None:
    requests = build_requests(CASES, "claude-haiku-4-5", 64)
    assert [r["custom_id"] for r in requests] == [c.id for c in CASES]
    assert requests[0]["params"] == {
        "model": "claude-haiku-4-5",
        "max_tokens": 64,
        "messages": [{"role": "user", "content": CASES[0].prompt}],
    }
    _raises(ValueError, build_requests, [], "m", 1)
    _raises(ValueError, build_requests, CASES, "m", 0)
    _raises(ValueError, build_requests, CASES, "", 1)
    _raises(ValueError, EvalCase, "ok", "p", "")
    many = [EvalCase(f"c{i}", "p", "x") for i in range(MAX_REQUESTS_PER_BATCH + 1)]
    _raises(BatchTooLarge, build_requests, many, "m", 1)


def test_poll_decision_table_and_unknown_status() -> None:
    assert poll_decision("in_progress") is PollDecision.WAIT
    assert poll_decision("canceling") is PollDecision.WAIT
    assert poll_decision("ended") is PollDecision.FETCH
    assert "weird" in str(_raises(ValueError, poll_decision, "weird"))


class _FakeLine:
    def __init__(self, data: dict) -> None:
        self._data = data

    def model_dump(self, *, mode: str) -> dict:
        assert mode == "json"
        return self._data


class _FakeBatches:
    def __init__(self, statuses: list[str], lines: list[dict]) -> None:
        self._statuses = list(statuses)
        self._lines = lines
        self.calls: list[str] = []
        self.ended = False

    def retrieve(self, batch_id: str):
        self.calls.append("retrieve")
        status = self._statuses.pop(0)
        self.ended = status == "ended"
        return SimpleNamespace(id=batch_id, processing_status=status)

    def results(self, batch_id: str):
        self.calls.append("results")
        assert self.ended, "results requested before the batch ended"
        return iter(_FakeLine(d) for d in self._lines)


def test_wait_and_collect_polls_until_ended() -> None:
    batches = _FakeBatches(["in_progress", "in_progress", "ended"], _fixture_lines())
    client = SimpleNamespace(messages=SimpleNamespace(batches=batches))
    slept: list[float] = []
    lines = batch_cli.wait_and_collect(client, "msgbatch_x", sleep=slept.append, interval=60.0)
    assert batches.calls == ["retrieve", "retrieve", "retrieve", "results"]
    assert slept == [60.0, 60.0]
    assert [c.id for c, _ in join_results(CASES, lines)] == [c.id for c in CASES]


def test_wait_and_collect_unknown_status_raises_without_results() -> None:
    batches = _FakeBatches(["in_progress", "archived"], [])
    client = SimpleNamespace(messages=SimpleNamespace(batches=batches))
    try:
        batch_cli.wait_and_collect(client, "b", sleep=lambda s: None, interval=1.0)
    except ValueError as exc:
        assert "archived" in str(exc)
    else:
        raise AssertionError("an unknown processing_status must raise")
    assert batches.calls == ["retrieve", "retrieve"]


def test_cost_is_half_the_standard_price() -> None:
    # 1M in + 1M out on Haiku 4.5 is $1 + $5 standard, so $3 at batch price.
    assert batch_cost_usd("claude-haiku-4-5", 1_000_000, 1_000_000) == 3.0
    _raises(KeyError, batch_cost_usd, "claude-unknown", 1, 1)
    _raises(ValueError, batch_cost_usd, "claude-haiku-4-5", -1, 0)


def test_main_dry_run_and_keyless_live_send_nothing() -> None:
    saved_argv, saved_key = sys.argv, os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        sys.argv = ["batch_cli.py"]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            assert batch_cli.main() == 0
        assert "nothing was sent" in out.getvalue()
        sys.argv = ["batch_cli.py", "--live"]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            assert batch_cli.main() == 2
        assert "ANTHROPIC_API_KEY" in err.getvalue()
        assert "anthropic" not in sys.modules, "the SDK must not even be imported"
    finally:
        sys.argv = saved_argv
        if saved_key is not None:
            os.environ["ANTHROPIC_API_KEY"] = saved_key


TESTS = [
    (test_join_returns_case_order_from_scrambled_fixture, "join returns case order from a scrambled results file"),
    (test_retryable_follows_error_type, "invalid_request_error is not retryable; overloaded/api errors are"),
    (test_grades_never_fail_an_unjudged_case, "errored/expired/canceled grade no_result; wrong text grades fail"),
    (test_each_failure_is_a_distinct_named_error, "missing, unknown, duplicate result, duplicate case, bad id: 5 named errors"),
    (test_malformed_result_lines_fail_loudly, "unknown result type or missing field raises MalformedResult"),
    (test_build_requests_shape_and_rejections, "build_requests keeps order and rejects empty/0 tokens/oversize"),
    (test_poll_decision_table_and_unknown_status, "poll_decision: in_progress/canceling wait, ended fetch, else raise"),
    (test_wait_and_collect_polls_until_ended, "wait_and_collect: 3 retrieves, 2 sleeps, results only after ended"),
    (test_wait_and_collect_unknown_status_raises_without_results, "an unknown status raises and results is never called"),
    (test_cost_is_half_the_standard_price, "batch cost is 50% of the dated standard price"),
    (test_main_dry_run_and_keyless_live_send_nothing, "dry run and keyless --live never import the SDK"),
]


def main() -> int:
    for fn, label in TESTS:
        fn()
        print(f"ok  {label}")
    print(f"\nAll {len(TESTS)} self-tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
