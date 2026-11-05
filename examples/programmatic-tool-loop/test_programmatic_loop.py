"""Offline self-test: stdlib only, no key, no network.

Each test class maps to one acceptance criterion from
research/2026-11-05-programmatic-tool-calling-loop.md. The fixtures in
fixtures/ are docs-shaped response bodies. They are recorded shapes, not
captured traffic.

Run:  python3 -m unittest -v
"""

from __future__ import annotations

import copy
import json
import time
import unittest
from pathlib import Path

from demo_tools import HANDLERS, TOOLS
from execute import UnknownToolError, run_calls
from loop import MaxTurnsError, run
from reply import MissingResultError, NonTextResultError, ToolError, UnexpectedResultError, build_reply
from toolset import ToolsetError, validate_tools
from turn import (
    CodeExecutionFailedError,
    Finished,
    MissingContainerError,
    Paused,
    ProtocolError,
    Reply,
    ToolCall,
    UnexpectedStopError,
    UnknownCallerError,
    classify,
)

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def as_reply(body: dict) -> Reply:
    container = body.get("container")
    return Reply(stop_reason=body["stop_reason"], content=body["content"],
                 container_id=container["id"] if container else None)


class RecordingSend:
    """A fake ``Send``: replays replies in order, keeps a deep copy of every request."""

    def __init__(self, *replies: Reply):
        self.replies = list(replies)
        self.requests: list[dict] = []

    def __call__(self, request: dict) -> Reply:
        self.requests.append(copy.deepcopy(request))
        if not self.replies:
            raise AssertionError("loop sent more requests than the test scripted")
        return self.replies.pop(0)


def paused_with(*calls: dict, container: str | None = "container_t") -> Reply:
    """A paused response whose tool_use blocks are ``calls`` (each {id, name, input, caller?})."""
    content = [{"type": "server_tool_use", "id": "srvtoolu_t", "name": "code_execution", "input": {"code": "..."}}]
    content += [{"type": "tool_use", **call} for call in calls]
    return Reply("tool_use", content, container)


PROGRAMMATIC = {"type": "code_execution_20260120", "tool_id": "srvtoolu_t"}
FINAL = as_reply(fixture("final"))


class C1Classify(unittest.TestCase):
    def test_paused_fixture_has_three_programmatic_calls_and_the_container(self):
        turn = classify(as_reply(fixture("paused_three_calls")))
        self.assertIsInstance(turn, Paused)
        self.assertEqual([c.id for c in turn.programmatic], ["toolu_01", "toolu_02", "toolu_03"])
        self.assertEqual(turn.direct, ())
        self.assertEqual(turn.container_id, "container_xyz789")
        self.assertTrue(all(c.caller_tool_id == "srvtoolu_abc123" for c in turn.programmatic))

    def test_missing_caller_is_direct(self):
        turn = classify(paused_with({"id": "toolu_old", "name": "query_database", "input": {}}, container=None))
        self.assertEqual([c.id for c in turn.direct], ["toolu_old"])


class C2ReplyOnlyToolResults(unittest.TestCase):
    def test_three_results_in_call_order_and_nothing_else(self):
        turn = classify(as_reply(fixture("paused_three_calls")))
        # dict order deliberately reversed: the reply must follow the response's order
        message = build_reply(turn, {"toolu_03": "c", "toolu_02": "b", "toolu_01": "a"})
        self.assertEqual(message["role"], "user")
        self.assertEqual({b["type"] for b in message["content"]}, {"tool_result"})
        self.assertEqual([(b["tool_use_id"], b["content"]) for b in message["content"]],
                         [("toolu_01", "a"), ("toolu_02", "b"), ("toolu_03", "c")])

    def test_partial_extra_or_non_text_results_build_nothing(self):
        turn = classify(as_reply(fixture("paused_three_calls")))
        with self.assertRaises(MissingResultError):
            build_reply(turn, {"toolu_01": "a", "toolu_02": "b"})
        with self.assertRaises(UnexpectedResultError):
            build_reply(turn, {"toolu_01": "a", "toolu_02": "b", "toolu_03": "c", "srvtoolu_abc123": "x"})
        with self.assertRaises(NonTextResultError):
            build_reply(turn, {"toolu_01": "a", "toolu_02": {"rows": []}, "toolu_03": "c"})


class C3MixedTurn(unittest.TestCase):
    def test_mixed_turn_answers_both_with_tool_results_only(self):
        turn = classify(as_reply(fixture("mixed_direct_and_programmatic")))
        self.assertEqual([c.id for c in turn.direct], ["toolu_direct1"])
        self.assertEqual([c.id for c in turn.programmatic], ["toolu_prog1"])
        message = build_reply(turn, {"toolu_direct1": "d", "toolu_prog1": "p"})
        self.assertEqual([b["type"] for b in message["content"]], ["tool_result", "tool_result"])
        self.assertEqual([b["tool_use_id"] for b in message["content"]], ["toolu_direct1", "toolu_prog1"])

    def test_errors_are_error_strings_with_is_error_only_on_the_direct_call(self):
        turn = classify(as_reply(fixture("mixed_direct_and_programmatic")))
        message = build_reply(turn, {"toolu_direct1": ToolError("boom"), "toolu_prog1": ToolError("bust")})
        direct, prog = message["content"]
        self.assertEqual((direct["content"], direct.get("is_error")), ("Error: boom", True))
        self.assertEqual(prog["content"], "Error: bust")
        self.assertNotIn("is_error", prog)
        self.assertEqual({b["type"] for b in message["content"]}, {"tool_result"})


class C4LoopRequests(unittest.TestCase):
    def test_container_threaded_tools_resent_assistant_turn_replayed(self):
        paused_body = fixture("paused_three_calls")
        send = RecordingSend(as_reply(paused_body), FINAL)
        result = run(send, "Which regions missed Q3 target?", TOOLS, HANDLERS)

        first, second = send.requests
        self.assertNotIn("container", first)
        self.assertEqual(second["container"], "container_xyz789")
        self.assertEqual(second["tools"], first["tools"])
        self.assertEqual(second["tools"], TOOLS)
        user, assistant, answer = second["messages"]
        self.assertEqual(assistant, {"role": "assistant", "content": paused_body["content"]})
        self.assertIn("server_tool_use", [b["type"] for b in assistant["content"]])
        # the real demo handler ran each SQL query; results are the rows as JSON strings
        rows = [json.loads(b["content"]) for b in answer["content"]]
        self.assertEqual(rows, [[{"region": "West", "revenue_usd": 370000, "target_usd": 400000}],
                                [{"region": "East", "revenue_usd": 515000, "target_usd": 500000}],
                                [{"region": "Central", "revenue_usd": 255000, "target_usd": 300000}]])
        self.assertEqual((result.turns, result.programmatic_calls, result.direct_calls), (2, 3, 0))

    def test_the_most_recent_container_id_is_passed_on(self):
        call = {"name": "query_database", "input": {"sql": "SELECT 1"}, "caller": PROGRAMMATIC}
        send = RecordingSend(paused_with({"id": "t1", **call}, container="container_a"),
                             paused_with({"id": "t2", **call}, container="container_b"), FINAL)
        run(send, "q", TOOLS, HANDLERS)
        self.assertEqual([r.get("container") for r in send.requests], [None, "container_a", "container_b"])


class C5HandlerFailures(unittest.TestCase):
    def test_slow_handler_times_out_as_error_string_and_loop_completes(self):
        def slow(_input):
            time.sleep(0.5)
            return "too late"

        tools = [TOOLS[0], {**TOOLS[1], "name": "slow"}]
        send = RecordingSend(paused_with({"id": "toolu_s", "name": "slow", "input": {}, "caller": PROGRAMMATIC}),
                             FINAL)
        started = time.monotonic()
        result = run(send, "q", tools, {"slow": slow}, tool_timeout_s=0.05)
        self.assertLess(time.monotonic() - started, 0.4, "loop waited for the abandoned handler")
        sent = send.requests[1]["messages"][2]["content"][0]
        self.assertEqual(sent["content"], "Error: timed out after 0.05s")
        self.assertEqual(result.final_text, FINAL.content[1]["text"])

    def test_raising_handler_becomes_error_string(self):
        send = RecordingSend(paused_with({"id": "toolu_bad", "name": "query_database",
                                          "input": {"sql": "SELECT nope FROM sales"}, "caller": PROGRAMMATIC}), FINAL)
        run(send, "q", TOOLS, HANDLERS)
        sent = send.requests[1]["messages"][2]["content"][0]
        self.assertEqual(sent["content"], "Error: OperationalError: no such column: nope")

    def test_write_attempt_is_refused_by_the_read_only_tool(self):
        results = run_calls([ToolCall("t", "query_database", {"sql": "DELETE FROM sales"}, "srvtoolu_t")],
                            HANDLERS, 5.0)
        self.assertIsInstance(results["t"], ToolError)
        self.assertIn("readonly", results["t"].message)

    def test_unknown_tool_name_raises_before_any_handler_runs(self):
        ran = []
        handlers = {"query_database": lambda i: ran.append(i) or "x"}
        calls = [ToolCall("a", "query_database", {}, "srvtoolu_t"), ToolCall("b", "drop_tables", {}, "srvtoolu_t")]
        with self.assertRaises(UnknownToolError):
            run_calls(calls, handlers, 5.0)
        self.assertEqual(ran, [])


class C6UnknownCaller(unittest.TestCase):
    def test_unknown_caller_type_raises(self):
        reply = paused_with({"id": "toolu_x", "name": "query_database", "input": {},
                             "caller": {"type": "browser_20270101", "tool_id": "srvtoolu_t"}})
        with self.assertRaises(UnknownCallerError):
            classify(reply)


class C7FinishAndLimits(unittest.TestCase):
    def test_final_turn_gives_stdout_and_text(self):
        turn = classify(FINAL)
        self.assertIsInstance(turn, Finished)
        send = RecordingSend(as_reply(fixture("paused_three_calls")), FINAL)
        result = run(send, "q", TOOLS, HANDLERS)
        self.assertEqual(result.stdout, "West: missed target by 30000\nEast: beat target by 15000\n"
                                        "Central: missed target by 45000\n")
        self.assertTrue(result.final_text.startswith("West missed"))
        self.assertEqual(result.stderr, "")

    def test_server_side_timeout_surfaces_only_in_stderr(self):
        body = fixture("final")
        body["content"][0]["content"]["stderr"] = "TimeoutError: query_database: no response after 270s\n"
        result = run(RecordingSend(as_reply(body)), "q", TOOLS, HANDLERS)
        self.assertIn("TimeoutError", result.stderr)

    def test_max_turns_raises(self):
        again = paused_with({"id": "toolu_r", "name": "query_database", "input": {"sql": "SELECT 1"},
                             "caller": PROGRAMMATIC})
        send = RecordingSend(again, again, again)
        with self.assertRaises(MaxTurnsError):
            run(send, "q", TOOLS, HANDLERS, max_turns=3)
        self.assertEqual(len(send.requests), 3)


class C8NotASecurityBoundary(unittest.TestCase):
    def test_direct_call_to_code_only_tool_is_still_dispatched(self):
        self.assertEqual(TOOLS[1]["allowed_callers"], ["code_execution_20260120"])
        send = RecordingSend(as_reply(fixture("mixed_direct_and_programmatic")), FINAL)
        result = run(send, "q", TOOLS, HANDLERS)
        answers = {b["tool_use_id"]: b for b in send.requests[1]["messages"][2]["content"]}
        self.assertEqual(json.loads(answers["toolu_direct1"]["content"]),
                         [{"quarter": "2026-Q2"}, {"quarter": "2026-Q3"}])
        self.assertEqual(json.loads(answers["toolu_prog1"]["content"]), [{"n": 8}])
        self.assertEqual(send.requests[1]["container"], "container_mixed42")
        self.assertEqual((result.programmatic_calls, result.direct_calls), (1, 1))


class BoundaryChecks(unittest.TestCase):
    """Fail-fast checks from the docstrings' failure modes, beyond criteria 1-8."""

    def test_programmatic_call_without_container_is_refused(self):
        with self.assertRaises(MissingContainerError):
            classify(paused_with({"id": "t", "name": "q", "input": {}, "caller": PROGRAMMATIC}, container=None))

    def test_bad_stop_and_shapes_raise(self):
        with self.assertRaises(UnexpectedStopError):
            classify(Reply("pause_turn", [], "c"))
        with self.assertRaises(ProtocolError):
            classify(Reply("tool_use", [{"type": "text", "text": "hi"}], "c"))
        with self.assertRaises(ProtocolError):
            classify(Reply("end_turn", paused_with({"id": "t", "name": "q", "input": {}}).content, "c"))
        failed = [{"type": "code_execution_tool_result", "tool_use_id": "srvtoolu_t",
                   "content": {"type": "code_execution_tool_result_error", "error_code": "unavailable"}}]
        with self.assertRaises(CodeExecutionFailedError):
            classify(Reply("end_turn", failed, "c"))

    def test_invalid_toolsets_fail_before_any_request(self):
        send = RecordingSend()
        with self.assertRaisesRegex(ToolsetError, "exactly one code execution tool"):
            run(send, "q", TOOLS[1:], HANDLERS)
        with self.assertRaisesRegex(ToolsetError, "handlers differ"):
            run(send, "q", TOOLS, {**HANDLERS, "extra": lambda i: ""})
        with self.assertRaisesRegex(ToolsetError, "strict"):
            validate_tools([TOOLS[0], {**TOOLS[1], "strict": True}], frozenset(HANDLERS))
        self.assertEqual(send.requests, [])

    def test_timeout_must_stay_below_the_server_limit(self):
        with self.assertRaises(ValueError):
            run(RecordingSend(), "q", TOOLS, HANDLERS, tool_timeout_s=300)


if __name__ == "__main__":
    unittest.main()
