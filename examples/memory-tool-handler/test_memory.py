"""Offline self-test for the memory tool handler. No API key, no network.

Run:
    python test_memory.py

Each test is named for the acceptance criterion (AC1-AC7) it checks, from the
build proposal in research/2026-10-28-memory-tool-handler.md.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Iterator

import agent
from commands import Result, execute
from paths import MemoryPathError, resolve_memory_path
from store import DirStore, InMemoryStore, Store


def _stores() -> Iterator[tuple[str, Store]]:
    yield "InMemoryStore", InMemoryStore()
    with tempfile.TemporaryDirectory() as tmp:
        yield "DirStore", DirStore(Path(tmp) / "mem")


def _ok(store: Store, **cmd) -> str:
    res = execute(store, cmd)
    assert not res.is_error, f"{cmd} unexpectedly failed: {res.text}"
    return res.text


def _err(store: Store, **cmd) -> str:
    res = execute(store, cmd)
    assert res.is_error, f"{cmd} unexpectedly succeeded: {res.text}"
    return res.text


# --------------------------------------------------------------------------- #
# AC1: path validation
# --------------------------------------------------------------------------- #

def test_ac1_path_validation() -> None:
    assert resolve_memory_path("/memories").is_root
    assert resolve_memory_path("/memories/").is_root
    assert resolve_memory_path("/memories/a.txt").virtual == "/memories/a.txt"
    assert resolve_memory_path("/memories//notes/a.md").virtual == "/memories/notes/a.md"
    bad = [
        "/etc/passwd",
        "/memories/../x",
        "/memories/../../secrets.env",
        "/memoriesX/a",
        "/memories/%2e%2e%2fx",
        "/memories/%252e%252e%252fx",
        "/memories/..\\x",
        "",
        "/memories/a\x00.txt",
        "memories/a.txt",
        "/memories/./a",
    ]
    for raw in bad:
        try:
            resolve_memory_path(raw)
        except MemoryPathError:
            continue
        raise AssertionError(f"accepted unsafe path {raw!r}")
    print(f"ok  AC1 resolve_memory_path accepts 4 safe paths, rejects {len(bad)} unsafe ones")


# --------------------------------------------------------------------------- #
# AC2: six commands, documented strings, both stores
# --------------------------------------------------------------------------- #

def test_ac2_commands_documented_strings() -> None:
    for name, s in _stores():
        empty = _ok(s, command="view", path="/memories")
        assert empty == (
            "Here're the files and directories up to 2 levels deep in /memories, "
            "excluding hidden items and node_modules:\n0B\t/memories"
        ), empty

        assert _ok(s, command="create", path="/memories/n.md", file_text="alpha\nbeta\ngamma") == (
            "File created successfully at: /memories/n.md"
        )
        assert _err(s, command="create", path="/memories/n.md", file_text="x") == (
            "Error: File /memories/n.md already exists"
        )
        assert _ok(s, command="view", path="/memories/n.md") == (
            "Here's the content of /memories/n.md with line numbers:\n"
            "     1\talpha\n     2\tbeta\n     3\tgamma"
        )
        assert _ok(s, command="view", path="/memories/n.md", view_range=[2, -1]).endswith(
            "with line numbers:\n     2\tbeta\n     3\tgamma"
        )
        assert _ok(s, command="view", path="/memories/n.md", view_range=[2, 2]).endswith(
            ":\n     2\tbeta"
        )
        _err(s, command="view", path="/memories/n.md", view_range=[3, 9])

        assert _ok(s, command="insert", path="/memories/n.md", insert_line=0, insert_text="top") == (
            "The file /memories/n.md has been edited."
        )
        _ok(s, command="insert", path="/memories/n.md", insert_line=4, insert_text="end")
        assert s.read(resolve_memory_path("/memories/n.md")) == "top\nalpha\nbeta\ngamma\nend"
        assert _err(s, command="insert", path="/memories/n.md", insert_line=9, insert_text="x") == (
            "Error: Invalid `insert_line` parameter: 9. It should be within the range "
            "of lines of the file: [0, 5]"
        )
        assert _err(s, command="insert", path="/memories/nope.md", insert_line=0, insert_text="x") == (
            "Error: The path /memories/nope.md does not exist"
        )

        edited = _ok(s, command="str_replace", path="/memories/n.md", old_str="beta", new_str="BETA")
        assert edited.startswith("The memory file has been edited.\n"), edited
        assert "     3\tBETA" in edited, edited
        _ok(s, command="str_replace", path="/memories/n.md", old_str="\nend")  # new_str omitted = delete
        assert s.read(resolve_memory_path("/memories/n.md")) == "top\nalpha\nBETA\ngamma"

        _ok(s, command="create", path="/memories/sub/deep.md", file_text="x")
        listing = _ok(s, command="view", path="/memories")
        assert "\t/memories/n.md" in listing and "\t/memories/sub/deep.md" in listing, listing
        _ok(s, command="create", path="/memories/.hidden", file_text="h")
        assert ".hidden" not in _ok(s, command="view", path="/memories")
        # Directory where a file is required is reported as "does not exist".
        assert _err(s, command="insert", path="/memories/sub", insert_line=0, insert_text="x") == (
            "Error: The path /memories/sub does not exist"
        )

        assert _ok(s, command="rename", old_path="/memories/n.md", new_path="/memories/arch/n.md") == (
            "Successfully renamed /memories/n.md to /memories/arch/n.md"
        )
        assert _err(s, command="rename", old_path="/memories/sub/deep.md", new_path="/memories/arch/n.md") == (
            "Error: The destination /memories/arch/n.md already exists"
        )
        assert _err(s, command="rename", old_path="/memories/gone", new_path="/memories/x") == (
            "Error: The path /memories/gone does not exist"
        )
        assert _ok(s, command="delete", path="/memories/arch/n.md") == "Successfully deleted /memories/arch/n.md"
        assert _err(s, command="delete", path="/memories/arch/n.md") == (
            "Error: The path /memories/arch/n.md does not exist"
        )
        assert _err(s, command="view", path="/memories/missing") == (
            "The path /memories/missing does not exist. Please provide a valid path."
        )
        assert _err(s, command="frobnicate", path="/memories").startswith("Error: Unknown command")
        assert _err(s, command="create", path="/memories/x").startswith("Error: Missing required parameter `file_text`")
        print(f"ok  AC2 six commands return the documented strings ({name})")


# --------------------------------------------------------------------------- #
# AC3: str_replace no-match / duplicate leaves the file byte-identical
# --------------------------------------------------------------------------- #

def test_ac3_str_replace_failures_do_not_write() -> None:
    for name, s in _stores():
        original = "x = 1\ny = 2\nx = 1\n"
        p = resolve_memory_path("/memories/f.py")
        _ok(s, command="create", path=p.virtual, file_text=original)
        miss = _err(s, command="str_replace", path=p.virtual, old_str="z = 3", new_str="q")
        assert miss == "No replacement was performed, old_str `z = 3` did not appear verbatim in /memories/f.py."
        dup = _err(s, command="str_replace", path=p.virtual, old_str="x = 1", new_str="q")
        assert dup == (
            "No replacement was performed. Multiple occurrences of old_str `x = 1` in lines: 1, 3. "
            "Please ensure it is unique"
        ), dup
        assert s.read(p) == original
        print(f"ok  AC3 str_replace with 0 and 2 matches errors and leaves the file intact ({name})")


# --------------------------------------------------------------------------- #
# AC4: root is protected; directory delete is recursive
# --------------------------------------------------------------------------- #

def test_ac4_root_protection_and_recursive_delete() -> None:
    for name, s in _stores():
        _ok(s, command="create", path="/memories/d/a.md", file_text="a")
        _ok(s, command="create", path="/memories/d/e/b.md", file_text="b")
        assert _err(s, command="delete", path="/memories") == "Error: Cannot delete the /memories directory itself"
        assert _err(s, command="delete", path="/memories/") == "Error: Cannot delete the /memories directory itself"
        _err(s, command="rename", old_path="/memories", new_path="/memories/x")
        _err(s, command="rename", old_path="/memories/d", new_path="/memories")
        _err(s, command="rename", old_path="/memories/d", new_path="/memories/d/inner")
        _ok(s, command="delete", path="/memories/d")
        for gone in ("/memories/d", "/memories/d/a.md", "/memories/d/e/b.md"):
            assert not s.exists(resolve_memory_path(gone)), gone
        assert s.is_dir(resolve_memory_path("/memories"))
        print(f"ok  AC4 root delete/rename refused, directory delete is recursive ({name})")


# --------------------------------------------------------------------------- #
# AC5: traversal in every path field cannot touch a file outside the root
# --------------------------------------------------------------------------- #

TRAVERSALS = [
    "/memories/../sentinel.txt",
    "/memories/../../sentinel.txt",
    "/memories/%2e%2e%2fsentinel.txt",
    "/memories/..\\sentinel.txt",
    "/sentinel.txt",
    "/memoriesX/../sentinel.txt",
    "/memories/link/sentinel.txt",  # symlink inside the root pointing outside
    "/memories/link",
    "/memories/filelink",            # symlink to the sentinel file itself
]


def test_ac5_traversal_cannot_escape_dirstore() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        outside = Path(tmp)
        sentinel = outside / "sentinel.txt"
        sentinel.write_bytes(b"DO NOT TOUCH\n")
        root = outside / "mem"
        s = DirStore(root)
        os.symlink(outside, root / "link")
        os.symlink(sentinel, root / "filelink")
        _ok(s, command="create", path="/memories/inside.md", file_text="safe")

        attempts = 0
        for bad in TRAVERSALS:
            cmds = [
                {"command": "view", "path": bad},
                {"command": "create", "path": bad, "file_text": "pwned"},
                {"command": "str_replace", "path": bad, "old_str": "DO NOT", "new_str": "pwned"},
                {"command": "insert", "path": bad, "insert_line": 0, "insert_text": "pwned"},
                {"command": "delete", "path": bad},
                {"command": "rename", "old_path": bad, "new_path": "/memories/stolen.txt"},
                {"command": "rename", "old_path": "/memories/inside.md", "new_path": bad},
            ]
            for cmd in cmds:
                res = execute(s, cmd)
                assert isinstance(res, Result) and res.is_error, f"{cmd} -> {res}"
                attempts += 1
        assert sentinel.read_bytes() == b"DO NOT TOUCH\n"
        assert sorted(p.name for p in outside.iterdir()) == ["mem", "sentinel.txt"]
        assert (root / "inside.md").read_text() == "safe"
        print(f"ok  AC5 {attempts} traversal attempts (incl. symlinks) all errored; sentinel byte-identical")


# --------------------------------------------------------------------------- #
# AC6 / AC7: the loop, against a scripted fake client
# --------------------------------------------------------------------------- #

def _b(**kw) -> SimpleNamespace:
    return SimpleNamespace(**kw)


def _tool_use(id_: str, inp: dict) -> SimpleNamespace:
    return _b(type="tool_use", id=id_, name="memory", input=inp)


class FakeClient:
    """Replays scripted responses and records every create() kwargs."""

    def __init__(self, scripted: list[SimpleNamespace]) -> None:
        self.calls: list[dict] = []
        self._scripted = list(scripted)
        self.messages = self

    def create(self, **kwargs):
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        return self._scripted.pop(0)


def test_ac6_loop_tool_results() -> None:
    s = InMemoryStore()
    client = FakeClient([
        _b(stop_reason="tool_use", content=[_tool_use("toolu_1", {"command": "view", "path": "/memories"})]),
        _b(stop_reason="tool_use", content=[
            _b(type="text", text="Saving."),
            _tool_use("toolu_2", {"command": "create", "path": "/memories/p.md", "file_text": "httpx"}),
            _tool_use("toolu_3", {"command": "view", "path": "/memories/../etc/passwd"}),
        ]),
        _b(stop_reason="end_turn", content=[_b(type="text", text="Noted.")]),
    ])
    answer = agent.run_session(client, s, "remember httpx")
    assert answer == "Noted."
    assert len(client.calls) == 3
    for call in client.calls:
        assert call["tools"] == [{"type": "memory_20250818", "name": "memory"}], call["tools"]
        assert call["model"] == agent.MODEL

    first = client.calls[1]["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in first] == ["toolu_1"]
    assert "is_error" not in first[0] and "0B\t/memories" in first[0]["content"]

    second = client.calls[2]["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in second] == ["toolu_2", "toolu_3"]
    assert second[0]["content"] == "File created successfully at: /memories/p.md"
    assert "is_error" not in second[0]
    assert second[1]["is_error"] is True and "'..' segment" in second[1]["content"]
    assert s.read(resolve_memory_path("/memories/p.md")) == "httpx"
    print("ok  AC6 loop answers each tool_use id, flags is_error, declares the tool exactly")


def test_loop_turn_cap() -> None:
    looping = [_b(stop_reason="tool_use", content=[_tool_use(f"t{i}", {"command": "view", "path": "/memories"})])
               for i in range(3)]
    try:
        agent.run_session(FakeClient(looping), InMemoryStore(), "go", max_turns=3)
    except RuntimeError as exc:
        assert "did not finish within 3 turns" in str(exc)
    else:
        raise AssertionError("expected RuntimeError at the turn cap")
    print("ok  loop raises RuntimeError when max_turns is exceeded")


def test_ac7_memory_survives_sessions() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "mem"
        s1 = FakeClient([
            _b(stop_reason="tool_use", content=[_tool_use("a", {"command": "view", "path": "/memories"})]),
            _b(stop_reason="tool_use", content=[_tool_use("b", {
                "command": "create", "path": "/memories/prefs.md", "file_text": "- HTTP: httpx\n- Python 3.12"})]),
            _b(stop_reason="end_turn", content=[_b(type="text", text="Saved.")]),
        ])
        agent.run_session(s1, DirStore(d), agent.SESSION_1)

        s2 = FakeClient([
            _b(stop_reason="tool_use", content=[_tool_use("c", {"command": "view", "path": "/memories"})]),
            _b(stop_reason="tool_use", content=[_tool_use("d", {"command": "view", "path": "/memories/prefs.md"})]),
            _b(stop_reason="end_turn", content=[_b(type="text", text="httpx on 3.12.")]),
        ])
        agent.run_session(s2, DirStore(d), agent.SESSION_2)  # fresh store, same directory
        listing = s2.calls[1]["messages"][-1]["content"][0]["content"]
        assert "\t/memories/prefs.md" in listing, listing
        body = s2.calls[2]["messages"][-1]["content"][0]["content"]
        assert body.endswith("     1\t- HTTP: httpx\n     2\t- Python 3.12"), body
    print("ok  AC7 a file written in session 1 is listed and readable in session 2")


def main() -> int:
    tests: list[Callable[[], None]] = [
        test_ac1_path_validation,
        test_ac2_commands_documented_strings,
        test_ac3_str_replace_failures_do_not_write,
        test_ac4_root_protection_and_recursive_delete,
        test_ac5_traversal_cannot_escape_dirstore,
        test_ac6_loop_tool_results,
        test_loop_turn_cap,
        test_ac7_memory_survives_sessions,
    ]
    for t in tests:
        t()
    print(f"\nAll {len(tests)} self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
