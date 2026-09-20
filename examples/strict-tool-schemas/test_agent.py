"""Offline self-test for the strict/loose tool registries. No key, no network.

Run:
    python test_agent.py

Like typed-tool-registry's test, this imports `anthropic` (see requirements.txt)
and exercises the real `BetaFunctionTool` objects from agent.py: schema
generation, the `strict` flag's placement on the wire payload, and Pydantic
validation are all real, pure, local code. Only the runner seam needs a fake,
because only it would otherwise make a network call.

One test function and one `ok` line per acceptance criterion 1-8 in
research/2026-09-03-strict-tool-schemas.md. Exits non-zero on the first failure.
"""

from __future__ import annotations

import copy

import agent
import schema_subset

# --------------------------------------------------------------------------- #
# Fakes for the runner seam (criterion 8). Only the surface run_agent touches:
# iterate the runner for each assistant turn, then
# tool_runner(...).until_done() -> final message with .stop_reason and .content.
# --------------------------------------------------------------------------- #


class _FakeTextBlock:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _FakeToolUseBlock:
    def __init__(self, name: str, input: dict) -> None:
        self.type = "tool_use"
        self.name = name
        self.input = input


class _FakeMessage:
    def __init__(self, *, stop_reason: str, content: list) -> None:
        self.stop_reason = stop_reason
        self.content = content


class _FakeRunner:
    """Yields scripted turns, then returns the last one from until_done()."""

    def __init__(self, turns: list) -> None:
        self._turns = turns

    def __iter__(self):
        return iter(self._turns)

    def until_done(self):
        return self._turns[-1]


class _FakeClient:
    """Serves a scripted turn list from beta.messages.tool_runner(...)."""

    def __init__(self, turns: list) -> None:
        held = turns

        class _Messages:
            def tool_runner(self, **_kwargs):
                return _FakeRunner(held)

        class _Beta:
            messages = _Messages()

        self.beta = _Beta()


# --------------------------------------------------------------------------- #
# 1. The strict registry carries the flag
# --------------------------------------------------------------------------- #

def test_strict_tools_emit_the_flag() -> None:
    assert agent.STRICT_TOOLS, "STRICT_TOOLS is empty"
    for tool in agent.STRICT_TOOLS:
        payload = tool.to_dict()
        assert payload.get("strict") is True, (
            f"{tool.name}: expected top-level 'strict': True, got {payload.get('strict')!r}"
        )
        # Top-level, alongside name/description/input_schema - not inside the schema.
        assert "strict" not in payload["input_schema"], (
            f"{tool.name}: 'strict' belongs beside input_schema, not inside it"
        )
    print("ok  every strict tool's to_dict() carries top-level \"strict\": true")


# --------------------------------------------------------------------------- #
# 2. The loose registry does not
# --------------------------------------------------------------------------- #

def test_loose_tools_omit_the_flag() -> None:
    assert agent.LOOSE_TOOLS, "LOOSE_TOOLS is empty"
    for tool in agent.LOOSE_TOOLS:
        payload = tool.to_dict()
        assert "strict" not in payload, (
            f"{tool.name}: loose payload should have no 'strict' key, got {payload['strict']!r}"
        )
    print("ok  no loose tool's to_dict() has a \"strict\" key at all")


# --------------------------------------------------------------------------- #
# 3. The two registries are parallel by construction
# --------------------------------------------------------------------------- #

def test_registries_differ_only_by_the_flag() -> None:
    strict, loose = agent.STRICT_TOOLS, agent.LOOSE_TOOLS
    assert [t.name for t in strict] == [t.name for t in loose], "tool names diverge"

    for s, l in zip(strict, loose):
        assert s.func is l.func, f"{s.name}: wrapped functions are not the same object"
        assert s.input_schema == l.input_schema, (
            f"{s.name}: schemas differ, so a live A/B would not be controlled"
        )
        # The whole claim: strip the flag and the payloads are equal.
        stripped = {k: v for k, v in s.to_dict().items() if k != "strict"}
        assert stripped == l.to_dict(), (
            f"{s.name}: payloads differ by more than the strict flag"
        )

    assert agent.STRICT_REGISTRY.keys() == agent.LOOSE_REGISTRY.keys()
    assert agent.STRICT_REGISTRY["set_priority"] is agent.set_priority
    print("ok  strict and loose payloads are identical except for that one key")


# --------------------------------------------------------------------------- #
# 4. Every strict schema is inside the grammar-compilable subset
# --------------------------------------------------------------------------- #

def test_strict_schemas_are_subset_compatible() -> None:
    for tool in agent.STRICT_TOOLS:
        schema = tool.input_schema
        assert schema["type"] == "object", f"{tool.name}: type is {schema['type']!r}"
        assert schema["additionalProperties"] is False, (
            f"{tool.name}: strict objects must set additionalProperties to false, "
            f"got {schema['additionalProperties']!r}"
        )
        # "Make parameters required where possible" - every optional parameter
        # roughly doubles a portion of the grammar's state space.
        assert set(schema["required"]) == set(schema["properties"]), (
            f"{tool.name}: required {sorted(schema['required'])} != "
            f"properties {sorted(schema['properties'])}"
        )
        leftovers = schema_subset.unsupported_keywords(schema)
        assert leftovers == [], f"{tool.name}: unsupported keywords {leftovers}"
    print("ok  each strict schema is an object, closed, all-required, subset-clean")


# --------------------------------------------------------------------------- #
# 5. The Literal lowered to a scalar enum
# --------------------------------------------------------------------------- #

def test_literal_lowers_to_a_scalar_enum() -> None:
    level = agent.set_priority.input_schema["properties"]["level"]
    assert level["type"] == "string", f"level type is {level['type']!r}"
    assert level["enum"] == ["low", "medium", "high"], f"level enum is {level['enum']!r}"
    attendees = agent.schedule_event.input_schema["properties"]["attendees"]
    assert attendees["type"] == "integer", f"attendees type is {attendees['type']!r}"
    print("ok  Priority lowered to a string enum, attendees to a JSON integer")


# --------------------------------------------------------------------------- #
# 6. The .call() guard survives - defence in depth
# --------------------------------------------------------------------------- #

def test_call_still_rejects_bad_input() -> None:
    # `strict` makes the model unable to produce any of these. The guard stays
    # anyway: strict constrains the model, it does not constrain your own code,
    # a replayed transcript, or a tool called from a non-strict code path.
    for tool, bad_input in [
        (agent.set_priority, {"task": "x", "level": "urgent"}),   # off-enum
        (agent.set_priority, {"task": "x", "level": 3}),          # wrong type
        (agent.set_priority, {"task": "x"}),                      # missing field
        (agent.schedule_event, {"title": "x", "date": "2026-10-05", "attendees": "three"}),
    ]:
        try:
            tool.call(bad_input)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{tool.name}.call({bad_input!r}) should have raised ValueError")
    assert agent.set_priority.call({"task": "x", "level": "high"}) == "'x' priority set to high"
    print("ok  .call() still raises ValueError on off-enum, wrong-type, missing input")


# --------------------------------------------------------------------------- #
# 7. The keyword walker itself
# --------------------------------------------------------------------------- #

def test_unsupported_keywords_walker() -> None:
    assert schema_subset.unsupported_keywords(agent.set_priority.input_schema) == []

    # Each expected keyword is reachable by exactly one route, so dropping any
    # one recursion in schema_subset breaks this assertion:
    #   "pattern"   only via `properties` -> "code"
    #   "minLength" only via `properties` -> "tags" -> `items`
    # And a property *named* like a keyword ("minimum") must not be reported,
    # so treating property names as keywords also breaks it.
    hand_built = {
        "type": "object",
        "additionalProperties": False,
        "required": ["code", "tags"],
        "properties": {
            "minimum": {"type": "string"},
            "code": {"type": "string", "pattern": "^[A-Z]+$"},
            "tags": {"type": "array", "items": {"type": "string", "minLength": 1}},
        },
    }
    before = copy.deepcopy(hand_built)
    assert schema_subset.unsupported_keywords(hand_built) == ["minLength", "pattern"], (
        schema_subset.unsupported_keywords(hand_built)
    )
    assert hand_built == before, "unsupported_keywords mutated its input"

    # oneOf is unsupported and still walked, so both it and what it hides report.
    assert schema_subset.unsupported_keywords(
        {"oneOf": [{"type": "integer", "multipleOf": 2}]}
    ) == ["multipleOf", "oneOf"]

    # $defs is a name -> sub-schema mapping too: entry names are not keywords,
    # but constraints inside the entries are.
    assert schema_subset.unsupported_keywords(
        {"$defs": {"maxLength": {"type": "string", "maxLength": 4}}, "$ref": "#/$defs/maxLength"}
    ) == ["maxLength"]
    print("ok  walker finds nested constraints, ignores property names, mutates nothing")


# --------------------------------------------------------------------------- #
# 8. run_agent's seam
# --------------------------------------------------------------------------- #

def test_run_agent_seam() -> None:
    seen: list[agent.ToolCall] = []
    client = _FakeClient(
        [
            _FakeMessage(
                stop_reason="tool_use",
                content=[_FakeToolUseBlock("set_priority", {"task": "x", "level": "high"})],
            ),
            _FakeMessage(stop_reason="end_turn", content=[_FakeTextBlock("done"), _FakeTextBlock("!")]),
        ]
    )
    assert agent.run_agent(client, "anything", agent.STRICT_TOOLS, observer=seen.append) == "done!"
    assert [c.name for c in seen] == ["set_priority"], seen
    assert seen[0].input == {"task": "x", "level": "high"}

    # Cap hit while the model still wanted a tool -> no real answer.
    capped = _FakeClient([_FakeMessage(stop_reason="tool_use", content=[])])
    try:
        agent.run_agent(capped, "anything", agent.STRICT_TOOLS, max_iterations=3)
    except RuntimeError as exc:
        assert "3" in str(exc), f"error should name the iteration cap: {exc!r}"
    else:
        raise AssertionError("run_agent should raise RuntimeError when max_iterations is exhausted")
    print("ok  run_agent joins final text, reports tool inputs, raises on the cap")


# --------------------------------------------------------------------------- #

def main() -> int:
    tests = [
        test_strict_tools_emit_the_flag,
        test_loose_tools_omit_the_flag,
        test_registries_differ_only_by_the_flag,
        test_strict_schemas_are_subset_compatible,
        test_literal_lowers_to_a_scalar_enum,
        test_call_still_rejects_bad_input,
        test_unsupported_keywords_walker,
        test_run_agent_seam,
    ]
    for t in tests:
        t()
    print(f"\nAll {len(tests)} self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
