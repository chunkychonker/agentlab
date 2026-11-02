"""Offline self-test for the MRTR elicitation example -- no subprocess, no network, no key.

Drives fresh servers through the SDK's in-memory `Client` in both protocol eras.
Convention matches examples/mcp-tool-annotations/test_server.py: plain `test_*`
async functions, each printing "ok  <description>", run from `main() -> int`.
Each test asserts one acceptance criterion from the research note
(research/2026-11-02-mcp-mrtr-elicitation.md), numbered in its docstring.

Run: python test_mrtr.py
"""

import asyncio
from pathlib import Path

from mcp import Client, InputRequiredRoundsExceededError
from mcp.client import ClientRequestContext
from mcp.server.mcpserver import AcceptedElicitation, CancelledElicitation, DeclinedElicitation
from mcp.shared.exceptions import MCPError
from mcp.types import CallToolResult, ElicitRequestParams, ElicitResult, InputRequiredResult

from policy import Confirm, Decision, TypedCount, decide, decide_purge
from run_demo import transcript
from server import SEED_NOTES, build_server

MODERN = "2026-07-28"
LEGACY = "legacy"
README = Path(__file__).with_name("README.md")
DEMO_MARKER = "Demo transcript"


class ScriptedHuman:
    """An elicitation callback that answers from a fixed script and records every question."""

    def __init__(self, *answers: ElicitResult) -> None:
        self._answers = list(answers)
        self.asked: list[str] = []

    async def __call__(self, _ctx: ClientRequestContext, params: ElicitRequestParams) -> ElicitResult:
        self.asked.append(params.message)
        if not self._answers:
            raise AssertionError(f"unscripted question: {params.message!r}")
        return self._answers.pop(0)


def _accept(**content: object) -> ElicitResult:
    return ElicitResult(action="accept", content=content)


def _text(result: CallToolResult) -> str:
    return "".join(getattr(block, "text", "") for block in result.content)


async def test_modern_accept_deletes_and_asks_once() -> None:
    """1. 2026-07-28 + callback answering ok=True: deleted, callback invoked exactly once."""
    store = dict(SEED_NOTES)
    human = ScriptedHuman(_accept(ok=True))
    async with Client(build_server(store), mode=MODERN, elicitation_callback=human) as client:
        assert client.protocol_version == MODERN, client.protocol_version
        result = await client.call_tool("delete_note", {"note_id": "n1"})
    assert not result.is_error and _text(result) == "deleted n1", _text(result)
    assert sorted(store) == ["n2"], store
    assert len(human.asked) == 1, human.asked
    print("ok  2026-07-28: accept ok=true -> 'deleted n1', note gone, asked exactly once")


async def test_modern_no_or_decline_keeps() -> None:
    """2. Same with ok=False, and with an explicit decline: note retained, result says kept."""
    for answer, want in [
        (_accept(ok=False), "kept: answered no n1"),
        (ElicitResult(action="decline"), "kept: declined n1"),
    ]:
        store = dict(SEED_NOTES)
        human = ScriptedHuman(answer)
        async with Client(build_server(store), mode=MODERN, elicitation_callback=human) as client:
            result = await client.call_tool("delete_note", {"note_id": "n1"})
        assert not result.is_error and _text(result) == want, _text(result)
        assert sorted(store) == ["n1", "n2"], store
        assert len(human.asked) == 1, human.asked
    print("ok  2026-07-28: ok=false and action=decline both keep the note; result says 'kept'")


async def test_manual_drive_shows_input_required_then_resumes() -> None:
    """3. allow_input_required=True returns InputRequiredResult; nothing deleted until the retry."""
    store = dict(SEED_NOTES)
    human = ScriptedHuman()  # advertises the capability; must never be called here
    async with Client(build_server(store), mode=MODERN, elicitation_callback=human) as client:
        args = {"note_id": "n1"}
        first = await client.session.call_tool("delete_note", args, allow_input_required=True)
        assert isinstance(first, InputRequiredResult), type(first).__name__
        wire = first.model_dump(by_alias=True, exclude_none=True)
        assert wire["resultType"] == "input_required", wire
        assert len(wire["inputRequests"]) == 1, wire
        (key, request), = wire["inputRequests"].items()
        assert request["method"] == "elicitation/create", request
        assert first.request_state, "server sent no requestState"
        assert sorted(store) == ["n1", "n2"], "deleted before the human answered"
        final = await client.session.call_tool(
            "delete_note", args, input_responses={key: _accept(ok=True)}, request_state=first.request_state
        )
    assert isinstance(final, CallToolResult) and _text(final) == "deleted n1", final
    assert final.result_type == "complete", final.result_type
    assert sorted(store) == ["n2"], store
    assert human.asked == [], human.asked
    print("ok  manual drive: resultType=input_required, note intact; retry with request_state completes")


async def test_legacy_parity_with_standalone_request() -> None:
    """4. mode='legacy': same end state, callback once, and no InputRequiredResult on the wire."""
    store = dict(SEED_NOTES)
    human = ScriptedHuman(_accept(ok=True))
    async with Client(build_server(store), mode=LEGACY, elicitation_callback=human) as client:
        assert client.protocol_version == "2025-11-25", client.protocol_version
        # Even when asked to surface InputRequiredResult, legacy has none to return:
        # the question went out as a standalone elicitation/create during this call.
        result = await client.session.call_tool("delete_note", {"note_id": "n1"}, allow_input_required=True)
    assert isinstance(result, CallToolResult) and _text(result) == "deleted n1", result
    assert sorted(store) == ["n2"], store
    assert len(human.asked) == 1, human.asked
    print("ok  legacy 2025-11-25: same result and end state, asked once, no InputRequiredResult")


async def test_dependent_questions_need_two_rounds() -> None:
    """5. A two-question chain completes under the default cap and raises at max_rounds=1."""
    store = dict(SEED_NOTES)
    human = ScriptedHuman(_accept(ok=True), _accept(count=2))
    async with Client(build_server(store), mode=MODERN, elicitation_callback=human) as client:
        result = await client.call_tool("purge_notes", {})
    assert _text(result) == "purged 2" and store == {}, (_text(result), store)
    assert len(human.asked) == 2, human.asked

    store = dict(SEED_NOTES)
    human = ScriptedHuman(_accept(ok=True), _accept(count=2))
    capped = Client(build_server(store), mode=MODERN, elicitation_callback=human, input_required_max_rounds=1)
    async with capped as client:
        try:
            await client.call_tool("purge_notes", {})
        except InputRequiredRoundsExceededError:
            pass
        else:
            raise AssertionError("max_rounds=1 did not raise on a two-round tool")
    assert sorted(store) == ["n1", "n2"], store
    assert len(human.asked) == 1, human.asked
    print("ok  purge_notes needs 2 rounds: default cap -> 'purged 2'; max_rounds=1 -> InputRequiredRoundsExceededError")


async def test_tampered_or_rebound_state_is_rejected() -> None:
    """6. A modified requestState, or a valid one replayed on other arguments, is rejected; nothing deleted."""
    store = dict(SEED_NOTES)
    async with Client(build_server(store), mode=MODERN, elicitation_callback=ScriptedHuman()) as client:
        first = await client.session.call_tool("delete_note", {"note_id": "n1"}, allow_input_required=True)
        assert isinstance(first, InputRequiredResult) and first.request_state is not None
        (key,) = first.input_requests
        yes = {key: _accept(ok=True)}
        sealed = first.request_state
        tampered = sealed[:-4] + ("AAAA" if not sealed.endswith("AAAA") else "BBBB")
        for label, args, state in [
            ("tampered", {"note_id": "n1"}, tampered),
            ("rebound to n2", {"note_id": "n2"}, sealed),
        ]:
            try:
                await client.session.call_tool("delete_note", args, input_responses=yes, request_state=state)
            except MCPError as exc:
                assert "requestState" in str(exc), f"{label}: {exc}"
            else:
                raise AssertionError(f"{label} requestState was accepted")
    assert sorted(store) == ["n1", "n2"], store
    print("ok  tampered requestState and one replayed on note_id=n2 both -> MCPError; nothing deleted")


async def test_unknown_note_errors_without_asking() -> None:
    """Extra: a resolver that fails before asking surfaces as is_error, and the human is never asked."""
    store = dict(SEED_NOTES)
    human = ScriptedHuman()
    async with Client(build_server(store), mode=MODERN, elicitation_callback=human) as client:
        result = await client.call_tool("delete_note", {"note_id": "nope"})
    assert result.is_error and "nope" in _text(result), _text(result)
    assert human.asked == [] and sorted(store) == ["n1", "n2"], (human.asked, store)
    print("ok  unknown note_id -> is_error naming the id; the human is never asked")


async def test_policy_table() -> None:
    """Extra: the pure policy maps every outcome to exactly one Decision."""
    cases = [
        (decide(AcceptedElicitation(data=Confirm(ok=True))), Decision.DELETE),
        (decide(AcceptedElicitation(data=Confirm(ok=False))), Decision.KEEP_ANSWERED_NO),
        (decide(DeclinedElicitation()), Decision.KEEP_DECLINED),
        (decide(CancelledElicitation()), Decision.KEEP_CANCELLED),
        (decide_purge(AcceptedElicitation(data=TypedCount(count=2)), 2), Decision.DELETE),
        (decide_purge(AcceptedElicitation(data=TypedCount(count=3)), 2), Decision.KEEP_WRONG_COUNT),
        (decide_purge(CancelledElicitation(), 2), Decision.KEEP_CANCELLED),
    ]
    for i, (got, want) in enumerate(cases):
        assert got is want, f"case {i}: {got} != {want}"
    print(f"ok  policy: {len(cases)} outcome cases map to the expected Decision")


def _readme_demo_block() -> str:
    """The fenced block right after the line containing DEMO_MARKER.

    Raises:
        AssertionError: the marker or its fenced block is missing.
    """
    lines = README.read_text().splitlines()
    starts = [i for i, line in enumerate(lines) if DEMO_MARKER in line]
    assert len(starts) == 1, f"expected one {DEMO_MARKER!r} line in README, found {len(starts)}"
    i = starts[0] + 1
    while i < len(lines) and not lines[i].strip():
        i += 1
    assert i < len(lines) and lines[i].startswith("```"), "no fenced block after the demo marker"
    end = lines.index("```", i + 1)
    return "\n".join(lines[i + 1 : end])


async def test_readme_demo_transcript_matches() -> None:
    """7. The README's demo transcript equals what run_demo.py prints."""
    got = "\n".join(await transcript())
    want = _readme_demo_block()
    assert got == want, f"README demo transcript drifted.\n--- README\n{want}\n--- run_demo.py\n{got}"
    print("ok  README demo transcript equals run_demo.py output")


async def _run_all() -> int:
    tests = [
        test_modern_accept_deletes_and_asks_once,
        test_modern_no_or_decline_keeps,
        test_manual_drive_shows_input_required_then_resumes,
        test_legacy_parity_with_standalone_request,
        test_dependent_questions_need_two_rounds,
        test_tampered_or_rebound_state_is_rejected,
        test_unknown_note_errors_without_asking,
        test_policy_table,
        test_readme_demo_transcript_matches,
    ]
    for test in tests:
        await test()
    return len(tests)


def main() -> int:
    count = asyncio.run(_run_all())
    print(f"\nAll {count} self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
