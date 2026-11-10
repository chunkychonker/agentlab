"""Milestone 3: ``parse_cli_json`` (A3, invariant 5).

The fixtures are copied verbatim from agentlab's ``.pipeline/test_metrics.sh``
(lines 20 and 23), shapes captured from ``claude -p`` on 2026-11-07.
"""

from __future__ import annotations

import json
import unittest

from nightloop.phase.result import CliOutputError, CliResult, Usage, parse_cli_json

OK_FIXTURE = (
    '{"type":"result","subtype":"success","is_error":false,"duration_ms":1172,"num_turns":1,'
    '"result":"OK\\nsecond line","stop_reason":"end_turn",'
    '"session_id":"5063166f-ae1d-42d3-8a1a-9581cb162ae3","total_cost_usd":0.00287775,'
    '"usage":{"input_tokens":2,"cache_creation_input_tokens":13924,'
    '"cache_read_input_tokens":9075,"output_tokens":4},"modelUsage":{"claude-haiku-5-5":{}}}'
)
AUTH_FIXTURE = (
    '{"type":"result","subtype":"success","is_error":true,"duration_ms":300,"num_turns":1,'
    '"result":"Failed to authenticate. API Error: 401 Invalid bearer token","session_id":"x",'
    '"total_cost_usd":0,"usage":{"input_tokens":0,"output_tokens":0,'
    '"cache_read_input_tokens":0,"cache_creation_input_tokens":0}}'
)


class ParseCliJsonTest(unittest.TestCase):
    # A3, accept: the recorded success fixture, every field typed.
    def test_recorded_success_fixture(self) -> None:
        self.assertEqual(
            parse_cli_json(OK_FIXTURE + "\n"),
            CliResult(
                result="OK\nsecond line",
                is_error=False,
                duration_ms=1172,
                num_turns=1,
                session_id="5063166f-ae1d-42d3-8a1a-9581cb162ae3",
                total_cost_usd=0.00287775,
                usage=Usage(
                    input_tokens=2,
                    output_tokens=4,
                    cache_read_input_tokens=9075,
                    cache_creation_input_tokens=13924,
                ),
                denials=None,
            ),
        )

    def test_auth_failure_reports_is_error_despite_success_subtype(self) -> None:
        parsed = parse_cli_json(AUTH_FIXTURE)
        self.assertIs(parsed.is_error, True)
        self.assertEqual(parsed.total_cost_usd, 0.0)

    # A3, reject.
    def test_rejects_what_is_not_one_object_with_a_string_result(self) -> None:
        cases = {
            "empty": "",
            "whitespace": "  \n",
            "plain text": "Traceback: KeyError door\n",
            "torn": '{"type":"result","result":"cut off',
            "two objects": '{"result":"a"}\n{"result":"b"}',
            "array": '[{"result":"a"}]',
            "string": '"result"',
            "missing result": '{"type":"result","subtype":"error_max_turns","is_error":true}',
            "non-string result": '{"result":42}',
            "null result": '{"result":null}',
            "NaN cost": '{"result":"a","total_cost_usd":NaN}',
            "Infinity turns": '{"result":"a","num_turns":Infinity}',
            "non-bool is_error": '{"result":"a","is_error":"false"}',
            "huge integer": '{"result":"a","num_turns":' + "9" * 5000 + "}",
            "deep nesting": '{"result":"a","x":' + "[" * 100000 + "]" * 100000 + "}",
        }
        for label, stdout in cases.items():
            with self.subTest(case=label):
                with self.assertRaises(CliOutputError):
                    parse_cli_json(stdout)

    # Invariant 5: an absent field is None, never 0.
    def test_minimal_object_gives_none_for_every_optional_field(self) -> None:
        parsed = parse_cli_json('{"result":"done"}')
        self.assertEqual(
            parsed,
            CliResult(
                result="done",
                is_error=None,
                duration_ms=None,
                num_turns=None,
                session_id=None,
                total_cost_usd=None,
                usage=Usage(None, None, None, None),
                denials=None,
            ),
        )

    def test_mistyped_metrics_fields_are_none_not_coerced(self) -> None:
        stdout = json.dumps(
            {
                "result": "done",
                "duration_ms": "1172",
                "num_turns": True,
                "session_id": 7,
                "total_cost_usd": "0.1",
                "usage": [1, 2],
                "permission_denials": {"tool_name": "Bash"},
            }
        )
        parsed = parse_cli_json(stdout)
        self.assertEqual(
            (parsed.duration_ms, parsed.num_turns, parsed.session_id, parsed.total_cost_usd),
            (None, None, None, None),
        )
        self.assertEqual(parsed.usage, Usage(None, None, None, None))
        self.assertIsNone(parsed.denials)

    def test_counts_permission_denials(self) -> None:
        denied = [{"tool_name": "Bash", "tool_input": {"command": "git push"}}] * 3
        parsed = parse_cli_json(json.dumps({"result": "x", "permission_denials": denied}))
        self.assertEqual(parsed.denials, 3)
        self.assertEqual(parse_cli_json('{"result":"x","permission_denials":[]}').denials, 0)


if __name__ == "__main__":
    unittest.main()
