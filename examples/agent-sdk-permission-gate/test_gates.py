"""Offline self-test for the permission gate. No API key, no CLI subprocess, no network.

Run:
    python test_gates.py

Needs only the pinned ``claude-agent-sdk`` installed. Covers the acceptance
criteria in research/2026-11-03-agent-sdk-permission-gate.md:
  1. ``decide``: the deny/allow table, fail-closed shapes, non-empty reasons.
  2. Hook adapter: exact documented deny dict, ``{}`` on allow, malformed
     payload -> deny rather than an exception.
  3. ``can_use_tool`` adapter: deny message / allow echoing ``updated_input``.
  4. Structure: which layout leaves tools unguarded, cross-checked against
     the SDK's own ``CanUseToolShadowedWarning``.
  5. End to end through the SDK's real ``query()`` with a scripted CLI:
     hook registration on the wire, the replies the CLI receives, string
     prompts keeping stdin open for ``can_use_tool``, a raising hook vs ours,
     and ``on_failure="block"`` refusing a CLI that cannot honour it.
  6. ``main`` refuses the billed run without a key.

What none of this can show offline: that the real CLI skips ``can_use_tool``
under acceptEdits/bypassPermissions. That claim rests on the docs, encoded in
``blind_spots.py``, and on the live run in ``agent.py``.
"""

from __future__ import annotations

import contextlib
import dataclasses
import io
import re
import warnings
from pathlib import Path
from typing import Any

import anyio
from claude_agent_sdk import (
    CanUseToolShadowedWarning,
    ClaudeAgentOptions,
    CLIConnectionError,
    HookMatcher,
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
    query,
)
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

import agent
from blind_spots import approved_before_callback, unguarded
from fake_cli import HookCall, PermissionCall, ScriptedPermissionCLI
from gates import AuditEntry, Checkpoint, build_gates
from options import BUILTIN_TOOLS, HOOK_MATCHER, MODEL, Layout, build_options
from policy import ALLOWED, GATED_TOOLS, Allowed, Denied, decide

WORKDIR = Path("/work")
ENV_WRITE = {"file_path": "/work/.env", "content": "DEMO=1"}
SAFE_WRITE = {"file_path": "/work/src/app.py", "content": "print(1)"}
ALL_GATES = frozenset(GATED_TOOLS)


def _payload(tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    """A PreToolUse hook input shaped like the CLI's (PreToolUseHookInput)."""
    return {
        "session_id": "s",
        "transcript_path": "/tmp/t.jsonl",
        "cwd": str(WORKDIR),
        "permission_mode": "acceptEdits",
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_use_id": "toolu_1",
    }


def _ctx() -> ToolPermissionContext:
    return ToolPermissionContext(tool_use_id="toolu_1")


def _drive(options: ClaudeAgentOptions, cli: ScriptedPermissionCLI) -> None:
    async def go() -> None:
        async for _ in query(prompt="go", options=options, transport=cli):
            pass
    anyio.run(go)


def _deny_reason(verdict: object) -> str:
    assert isinstance(verdict, Denied), verdict
    return verdict.reason


# --------------------------------------------------------------------------- #
# 1. The policy
# --------------------------------------------------------------------------- #

def test_policy_table() -> None:
    denied = [
        ("Write", ENV_WRITE),
        ("Edit", {"file_path": "/work/config/.ENV", "old_string": "a", "new_string": "b"}),
        ("Write", {"file_path": "/etc/passwd", "content": "x"}),
        ("Write", {"file_path": "/work/../etc/hosts", "content": "x"}),  # normalized
        ("Write", {"file_path": "//etc/hosts", "content": "x"}),  # POSIX double slash
        ("Write", {"file_path": "/private/etc/hosts", "content": "x"}),  # macOS spelling
        ("Write", {"content": "x"}),  # no file_path: fail closed
        ("Write", {"file_path": 7, "content": "x"}),
        ("Write", {"file_path": "src/app.py", "content": "x"}),  # relative: unresolvable
        ("Bash", {"command": "rm -rf /tmp/x"}),
        ("Bash", {"command": "cd /w &&  rm   -Rf build"}),  # spacing and case
        ("Bash", {"command": "git push --force origin main"}),
        ("Bash", {"command": "git push -f"}),
        ("Bash", {}),
    ]
    for name, tool_input in denied:
        assert _deny_reason(decide(name, tool_input)).strip(), (name, tool_input)

    allowed = [
        ("Read", {"file_path": "/work/.env"}),  # reading is outside this policy
        ("Bash", {"command": "ls -la"}),
        ("Bash", {"command": "git push origin main"}),
        ("Write", SAFE_WRITE),
        ("Write", {"file_path": "/work/.envrc", "content": "x"}),  # not .env
        ("Write", {"file_path": "/etcetera/x", "content": "x"}),  # prefix trap
        ("mcp__srv__tool", {"anything": "rm -rf /"}),
    ]
    for name, tool_input in allowed:
        assert decide(name, tool_input) == ALLOWED, (name, tool_input)
    print(f"ok  decide: {len(denied)} hostile calls denied with reasons, {len(allowed)} benign allowed")


def test_denied_needs_a_reason() -> None:
    for blank in ("", "   "):
        try:
            Denied(blank)
        except ValueError:
            continue
        raise AssertionError(f"Denied({blank!r}) must be unrepresentable")
    print("ok  a Denied verdict with an empty reason cannot be constructed")


def test_hook_matcher_covers_every_tool_the_policy_can_deny() -> None:
    hostile = [ENV_WRITE, {"file_path": "/etc/x"}, {"command": "rm -rf /"}, {}, {"file_path": None}]
    candidates = [*GATED_TOOLS, "Read", "Glob", "NotebookEdit", "WebFetch", "mcp__x__y"]
    can_deny = {t for t in candidates for i in hostile if isinstance(decide(t, i), Denied)}
    hooked = {t for t in candidates if re.fullmatch(HOOK_MATCHER, t)}
    assert can_deny == hooked == ALL_GATES, (can_deny, hooked)
    print(f"ok  hook matcher {HOOK_MATCHER!r} is exactly the set of tools decide can deny")


# --------------------------------------------------------------------------- #
# 2-3. The adapters, called directly
# --------------------------------------------------------------------------- #

def test_hook_adapter() -> None:
    audit: list[AuditEntry] = []
    hook = build_gates(audit.append).hook

    out = anyio.run(hook, _payload("Write", ENV_WRITE), "toolu_1", {"signal": None})
    reason = _deny_reason(decide("Write", ENV_WRITE))
    assert out == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }, out
    assert anyio.run(hook, _payload("Write", SAFE_WRITE), "toolu_2", {"signal": None}) == {}

    for garbage in (None, {}, {"tool_name": "Write"}, {"tool_name": "Write", "tool_input": "x"}, [1]):
        out = anyio.run(hook, garbage, "toolu_3", {"signal": None})
        decision = out["hookSpecificOutput"]
        assert decision["permissionDecision"] == "deny", (garbage, out)
        assert decision["permissionDecisionReason"].startswith("permission gate failed closed"), out

    assert [e.checkpoint for e in audit] == [Checkpoint.HOOK] * 7
    assert audit[0] == AuditEntry(Checkpoint.HOOK, "Write", Denied(reason))
    assert audit[1].verdict == ALLOWED
    assert audit[2].tool_name == "<malformed payload>" and audit[4].tool_name == "Write"
    print("ok  hook: documented deny dict, {} on allow, malformed payloads denied not raised")


def test_callback_adapter() -> None:
    audit: list[AuditEntry] = []
    can_use_tool = build_gates(audit.append).can_use_tool

    denied = anyio.run(can_use_tool, "Write", dict(ENV_WRITE), _ctx())
    assert isinstance(denied, PermissionResultDeny), denied
    assert denied.message == _deny_reason(decide("Write", ENV_WRITE))

    allowed = anyio.run(can_use_tool, "Write", dict(SAFE_WRITE), _ctx())
    assert isinstance(allowed, PermissionResultAllow), allowed
    assert allowed.updated_input == SAFE_WRITE, allowed

    assert [(e.checkpoint, type(e.verdict)) for e in audit] == [
        (Checkpoint.CALLBACK, Denied), (Checkpoint.CALLBACK, Allowed)]
    print("ok  can_use_tool: PermissionResultDeny(message=reason), Allow echoes updated_input")


# --------------------------------------------------------------------------- #
# 4. Structure: which layout leaves which tools unguarded
# --------------------------------------------------------------------------- #

def test_options_layouts() -> None:
    gates = build_gates(lambda _: None)
    cb = build_options(gates, Layout.CALLBACK_ONLY, "bypassPermissions", WORKDIR)
    hk = build_options(gates, Layout.HOOK_AND_CALLBACK, "bypassPermissions", WORKDIR)

    assert cb.hooks is None, "callback-only must register no hook"
    (matcher,) = hk.hooks["PreToolUse"]
    assert isinstance(matcher, HookMatcher)
    assert matcher.matcher == HOOK_MATCHER and matcher.hooks == [gates.hook]
    assert matcher.on_failure == "block"
    for opts in (cb, hk):
        assert opts.can_use_tool is gates.can_use_tool
        assert opts.setting_sources == [] and opts.allowed_tools == []

    # The argv the SDK would hand the CLI. Private API of the pinned SDK.
    argv = SubprocessCLITransport(
        prompt="x", options=dataclasses.replace(hk, cli_path="/nonexistent/claude"))._build_command()
    flag = lambda name: argv[argv.index(name) + 1]  # noqa: E731
    assert flag("--permission-mode") == "bypassPermissions"
    assert flag("--tools") == ",".join(BUILTIN_TOOLS)
    assert flag("--model") == MODEL
    assert "--setting-sources=" in argv
    print("ok  options: callback-only has no hook; hook layout registers ours with on_failure=block")


def test_unguarded_by_layout() -> None:
    gates = build_gates(lambda _: None)
    expected_callback_only = {
        "default": frozenset(),
        "dontAsk": frozenset(),
        "acceptEdits": ALL_GATES,  # Write/Edit in cwd + Bash rm/mv/... in cwd
        "bypassPermissions": ALL_GATES,
    }
    for mode, expected in expected_callback_only.items():
        cb = build_options(gates, Layout.CALLBACK_ONLY, mode, WORKDIR)
        hk = build_options(gates, Layout.HOOK_AND_CALLBACK, mode, WORKDIR)
        assert unguarded(cb, GATED_TOOLS) == expected, (mode, unguarded(cb, GATED_TOOLS))
        assert unguarded(hk, GATED_TOOLS) == frozenset(), mode

    # allow rules: bare / () / (*) allow the whole tool; a real specifier does not.
    assert approved_before_callback("default", ["Bash"], GATED_TOOLS) == {"Bash"}
    assert approved_before_callback("dontAsk", ["Write(*)", "Edit()"], GATED_TOOLS) == {"Write", "Edit"}
    assert approved_before_callback("default", ["Bash(ls *)", "Read"], GATED_TOOLS) == frozenset()
    for unmodeled in ("plan", "auto", "yolo"):
        try:
            approved_before_callback(unmodeled, [], GATED_TOOLS)
        except ValueError:
            continue
        raise AssertionError(f"{unmodeled!r} must raise, not guess")
    print("ok  callback-only leaves Bash/Edit/Write unguarded under acceptEdits and bypass; hook layout never")


def _sdk_warns(mode: str, allowed: list[str]) -> bool:
    options = ClaudeAgentOptions(
        permission_mode=mode, allowed_tools=allowed, setting_sources=[],
        can_use_tool=build_gates(lambda _: None).can_use_tool)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _drive(options, ScriptedPermissionCLI([]))
    return any(issubclass(w.category, CanUseToolShadowedWarning) for w in caught)


def test_blind_spots_agree_with_sdk_warning() -> None:
    configs = [
        ("default", []), ("default", ["Bash"]), ("default", ["Bash(ls *)"]),
        ("default", ["Write(*)"]), ("dontAsk", ["Edit"]), ("dontAsk", []),
        ("bypassPermissions", []), ("bypassPermissions", ["Read"]),
    ]
    for mode, allowed in configs:
        ours = bool(approved_before_callback(mode, allowed, ALL_GATES | {"Read"}))
        assert ours == _sdk_warns(mode, allowed), (mode, allowed)
    # Where they part ways, by design on the SDK's side (docs: "acceptEdits
    # mode doesn't trigger it"): edits in cwd skip the callback, but no warning.
    assert approved_before_callback("acceptEdits", [], ALL_GATES) == ALL_GATES
    assert _sdk_warns("acceptEdits", []) is False
    print(f"ok  blind spots match the SDK's CanUseToolShadowedWarning on {len(configs)} configs; "
          "acceptEdits is the one it does not flag")


# --------------------------------------------------------------------------- #
# 5. End to end through query(), scripted CLI
# --------------------------------------------------------------------------- #

def test_wire_contract_through_query() -> None:
    audit: list[AuditEntry] = []
    gates = build_gates(audit.append)
    cli = ScriptedPermissionCLI([
        HookCall("Write", _payload("Write", ENV_WRITE)),
        HookCall("Read", _payload("Read", {"file_path": "/work/x"})),
        PermissionCall("Write", SAFE_WRITE),
        PermissionCall("Bash", {"command": "rm -rf /work"}),
    ])
    _drive(build_options(gates, Layout.HOOK_AND_CALLBACK, "default", WORKDIR), cli)

    (registered,) = cli.initialize["hooks"]["PreToolUse"]
    assert registered["matcher"] == HOOK_MATCHER and registered["onFailure"] == "block", registered
    assert len(registered["hookCallbackIds"]) == 1
    assert cli.unrouted == ["Read"], "Read must not reach the hook"

    hook_reply, allow_reply, deny_reply = (r["response"] for r in cli.replies)
    assert all(r["subtype"] == "success" for r in cli.replies), cli.replies
    assert hook_reply["hookSpecificOutput"]["permissionDecision"] == "deny", hook_reply
    assert allow_reply == {"behavior": "allow", "updatedInput": SAFE_WRITE}, allow_reply
    assert deny_reply == {
        "behavior": "deny", "message": _deny_reason(decide("Bash", {"command": "rm -rf /work"}))}
    assert [e.checkpoint for e in audit] == [Checkpoint.HOOK, Checkpoint.CALLBACK, Checkpoint.CALLBACK]
    print("ok  query(): hook registered on the wire with onFailure=block; CLI receives our deny/allow")


def test_string_prompt_reaches_can_use_tool() -> None:
    gates = build_gates(lambda _: None)
    cli = ScriptedPermissionCLI([PermissionCall("Write", ENV_WRITE)])
    _drive(build_options(gates, Layout.CALLBACK_ONLY, "default", WORKDIR), cli)
    assert cli.stdin_open_after_prompt is True
    assert cli.replies[0]["response"]["behavior"] == "deny", cli.replies

    # Control: with nothing that needs stdin, the SDK closes it right after the
    # prompt, and the fake notices. So the True above is not a fake artefact.
    bare = ScriptedPermissionCLI([])
    _drive(ClaudeAgentOptions(setting_sources=[]), bare)
    assert bare.stdin_open_after_prompt is False
    print("ok  string prompt + can_use_tool alone: stdin stays open, no dummy hook needed")


def test_raising_hook_is_an_error_reply_ours_is_a_deny() -> None:
    async def naive_hook(input_data: Any, tool_use_id: str | None, context: Any) -> dict:
        return {} if input_data["tool_input"] else {}  # KeyError on a bad payload

    bad_payload = {"hook_event_name": "PreToolUse", "tool_name": "Write"}  # no tool_input
    naive = ScriptedPermissionCLI([HookCall("Write", bad_payload)])
    _drive(ClaudeAgentOptions(setting_sources=[], hooks={
        "PreToolUse": [HookMatcher(matcher="Write", hooks=[naive_hook])]}), naive)
    # The SDK turns the exception into an error reply. With on_failure unset,
    # the SDK documents that the CLI counts that as "no decision": fail open.
    assert naive.replies[0]["subtype"] == "error" and "tool_input" in naive.replies[0]["error"]
    assert "onFailure" not in naive.initialize["hooks"]["PreToolUse"][0]

    ours = ScriptedPermissionCLI([HookCall("Write", bad_payload)])
    _drive(build_options(build_gates(lambda _: None), Layout.HOOK_AND_CALLBACK, "default", WORKDIR), ours)
    reply = ours.replies[0]
    assert reply["subtype"] == "success", reply
    assert reply["response"]["hookSpecificOutput"]["permissionDecision"] == "deny", reply
    print("ok  a raising hook becomes an SDK error reply (fail open by default); ours replies deny")


def test_block_refuses_a_cli_that_cannot_honour_it() -> None:
    options = build_options(build_gates(lambda _: None), Layout.HOOK_AND_CALLBACK, "default", WORKDIR)
    try:
        _drive(options, ScriptedPermissionCLI([], on_failure_supported=False))
    except CLIConnectionError as exc:
        assert "on_failure" in str(exc), exc
    else:
        raise AssertionError("on_failure='block' must refuse a CLI that cannot honour it")
    print("ok  on_failure=block: SDK refuses to connect to a CLI that would fail open")


# --------------------------------------------------------------------------- #
# 6. Entry point
# --------------------------------------------------------------------------- #

def test_main_refuses_without_key() -> None:
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = agent.main([], environ={})
    assert code == agent.EXIT_NO_KEY != 0, code
    assert agent.API_KEY_ENV in err.getvalue()

    def result(layout: Layout, written: bool) -> agent.ScenarioResult:
        return agent.ScenarioResult(layout, written, (), (), None, False)
    cb, hk = Layout.CALLBACK_ONLY, Layout.HOOK_AND_CALLBACK
    assert agent.matches_docs(result(cb, True), result(hk, False))
    for cb_written, hk_written in ((False, False), (True, True), (False, True)):
        assert not agent.matches_docs(result(cb, cb_written), result(hk, hk_written))
    print("ok  main exits 2 without ANTHROPIC_API_KEY; matches_docs only for written/blocked")


# --------------------------------------------------------------------------- #

def main() -> int:
    tests = [
        test_policy_table,
        test_denied_needs_a_reason,
        test_hook_matcher_covers_every_tool_the_policy_can_deny,
        test_hook_adapter,
        test_callback_adapter,
        test_options_layouts,
        test_unguarded_by_layout,
        test_blind_spots_agree_with_sdk_warning,
        test_wire_contract_through_query,
        test_string_prompt_reaches_can_use_tool,
        test_raising_hook_is_an_error_reply_ours_is_a_deny,
        test_block_refuses_a_cli_that_cannot_honour_it,
        test_main_refuses_without_key,
    ]
    for t in tests:
        t()
    print(f"\nAll {len(tests)} self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
