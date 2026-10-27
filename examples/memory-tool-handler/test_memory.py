"""Offline self-test for the memory-tool handler. No API key, no network, stdlib only.

Run:
    python3 test_memory.py

Each test asserts one acceptance criterion from
research/2026-10-27-memory-tool-handler.md (numbered C1..C7 below). The command
table runs against both InMemoryStore and DirStore so the two stores are held
to one contract.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import agent
from commands import CreateMode, Result, handle
from dir_store import DirStore
from memory_store import InMemoryStore
from paths import MemoryPath, MemoryPathError, resolve_memory_path
from store import Store

OVERWRITE, REJECT = CreateMode.OVERWRITE, CreateMode.REJECT


@contextmanager
def tmpdir() -> Iterator[Path]:
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@contextmanager
def each_store() -> Iterator[list[tuple[str, Store]]]:
    with tmpdir() as d:
        yield [("InMemoryStore", InMemoryStore()), ("DirStore", DirStore(d))]


def ok(store: Store, cmd: dict, mode: CreateMode = OVERWRITE) -> str:
    r = handle(store, cmd, mode)
    assert not r.is_error, f"expected success for {cmd}, got {r.text!r}"
    return r.text


def err(store: Store, cmd: dict, mode: CreateMode = OVERWRITE) -> str:
    r = handle(store, cmd, mode)
    assert r.is_error, f"expected error for {cmd}, got {r.text!r}"
    return r.text


def read(store: Store, path: str) -> str:
    return store.read_text(resolve_memory_path(path))


# --------------------------------------------------------------------------- #
# C1  path parsing
# --------------------------------------------------------------------------- #

def test_paths_accept_valid() -> None:
    assert str(resolve_memory_path("/memories")) == "/memories"
    assert resolve_memory_path("/memories").is_root
    assert resolve_memory_path("/memories/").is_root
    assert resolve_memory_path("/memories/a.txt").parts == ("a.txt",)
    assert resolve_memory_path("/memories/notes/2026/x.md").parts == ("notes", "2026", "x.md")
    assert resolve_memory_path("/memories/dir/").parts == ("dir",)
    assert resolve_memory_path("/memories/50%.txt").parts == ("50%.txt",)  # not an escape
    assert resolve_memory_path("/memories/..hidden").parts == ("..hidden",)  # a name, not traversal
    print("ok  C1 valid /memories paths parse to the expected segments")


def test_paths_reject_invalid() -> None:
    bad = [
        "", "/", "memories", "memories/a", "/etc/passwd", "/memoriesX/a", "/memoriesX",
        "/memories/../x", "/memories/../../secrets.env", "/memories/a/../../x",
        "/memories/./a", "/memories/a//b", "/memories//",
        "/memories/%2e%2e%2fsecrets.env", "/memories/%2E%2E/x", "/memories/..%2fx",
        "/memories/%252e%252e%252f", "%2fmemories/a",
        "/memories/..\\..\\x", "/memories\\..\\x", "/memories/a\x00.txt",
        None, 42, ["/memories"],
    ]
    for raw in bad:
        try:
            resolve_memory_path(raw)  # type: ignore[arg-type]
        except MemoryPathError:
            continue
        raise AssertionError(f"accepted {raw!r}")
    # The type itself refuses bad segments, so no code path can build one.
    for parts in [("..",), (".",), ("",), ("a/b",), ("a\\b",), ("a\x00",)]:
        try:
            MemoryPath(parts)
        except MemoryPathError:
            continue
        raise AssertionError(f"MemoryPath accepted {parts!r}")
    print(f"ok  C1 {len(bad)} invalid paths and 6 bad segments are rejected")


# --------------------------------------------------------------------------- #
# C2  the six commands, exact strings, on both stores
# --------------------------------------------------------------------------- #

def test_command_table_both_stores() -> None:
    with each_store() as stores:
        for name, s in stores:
            assert ok(s, {"command": "create", "path": "/memories/notes.md", "file_text": "alpha\nbeta\ngamma\n"}) \
                == "File created successfully at: /memories/notes.md", name
            ok(s, {"command": "create", "path": "/memories/proj/todo.txt", "file_text": "x"})

            assert ok(s, {"command": "view", "path": "/memories"}) == (
                "Here're the files and directories up to 2 levels deep in /memories, "
                "excluding hidden items and node_modules:\n"
                "18B\t/memories\n"
                "17B\t/memories/notes.md\n"
                "1B\t/memories/proj/\n"
                "1B\t/memories/proj/todo.txt"
            ), name

            assert ok(s, {"command": "view", "path": "/memories/notes.md"}) == (
                "Here's the content of /memories/notes.md with line numbers:\n"
                "     1\talpha\n     2\tbeta\n     3\tgamma"
            ), name
            assert ok(s, {"command": "view", "path": "/memories/notes.md", "view_range": [2, 3]}).splitlines()[1:] \
                == ["     2\tbeta", "     3\tgamma"], name
            assert ok(s, {"command": "view", "path": "/memories/notes.md", "view_range": [3, -1]}).splitlines()[1:] \
                == ["     3\tgamma"], name
            err(s, {"command": "view", "path": "/memories/notes.md", "view_range": [0, 2]})
            err(s, {"command": "view", "path": "/memories/notes.md", "view_range": [2, 9]})
            err(s, {"command": "view", "path": "/memories/notes.md", "view_range": [3, 2]})

            assert ok(s, {"command": "insert", "path": "/memories/notes.md", "insert_line": 0, "insert_text": "top"}) \
                == "The file /memories/notes.md has been edited.", name
            ok(s, {"command": "insert", "path": "/memories/notes.md", "insert_line": 4, "insert_text": "end\n"})
            ok(s, {"command": "insert", "path": "/memories/notes.md", "insert_line": 2, "insert_text": "mid"})
            assert read(s, "/memories/notes.md") == "top\nalpha\nmid\nbeta\ngamma\nend\n", name
            assert err(s, {"command": "insert", "path": "/memories/notes.md", "insert_line": 7, "insert_text": "z"}) \
                == "Error: Invalid `insert_line` parameter: 7. It should be within the range [0, 6].", name

            assert ok(s, {"command": "str_replace", "path": "/memories/notes.md", "old_str": "beta", "new_str": "BETA"}) == (
                "The memory file has been edited. Here is the snippet showing the change (with line numbers):\n"
                "     2\talpha\n     3\tmid\n     4\tBETA\n     5\tgamma\n     6\tend"
            ), name
            ok(s, {"command": "str_replace", "path": "/memories/notes.md", "old_str": "mid\n"})  # new_str omitted
            assert read(s, "/memories/notes.md") == "top\nalpha\nBETA\ngamma\nend\n", name

            # create on an existing file: both documented behaviours.
            assert err(s, {"command": "create", "path": "/memories/notes.md", "file_text": "new"}, REJECT) \
                == "Error: File /memories/notes.md already exists", name
            assert read(s, "/memories/notes.md").startswith("top"), name
            ok(s, {"command": "create", "path": "/memories/notes.md", "file_text": "new"}, OVERWRITE)
            assert read(s, "/memories/notes.md") == "new", name

            assert ok(s, {"command": "rename", "old_path": "/memories/notes.md", "new_path": "/memories/arch/n.md"}) \
                == "Successfully renamed /memories/notes.md to /memories/arch/n.md", name
            assert err(s, {"command": "rename", "old_path": "/memories/arch/n.md", "new_path": "/memories/proj/todo.txt"}) \
                == "Error: The destination /memories/proj/todo.txt already exists", name
            assert ok(s, {"command": "delete", "path": "/memories/arch/n.md"}) \
                == "Successfully deleted /memories/arch/n.md", name

            missing = "The path /memories/nope.md does not exist. Please provide a valid path."
            assert err(s, {"command": "view", "path": "/memories/nope.md"}) == missing, name
            assert err(s, {"command": "delete", "path": "/memories/nope.md"}) == missing, name
            assert err(s, {"command": "str_replace", "path": "/memories/nope.md", "old_str": "a"}) == missing, name
            assert err(s, {"command": "insert", "path": "/memories/nope.md", "insert_line": 0, "insert_text": "a"}) \
                == missing, name
            assert err(s, {"command": "rename", "old_path": "/memories/nope.md", "new_path": "/memories/b"}) \
                == missing, name
    print("ok  C2 all six commands return the documented strings on both stores")


def test_listing_rules_both_stores() -> None:
    with each_store() as stores:
        for name, s in stores:
            for p in ["/memories/a/b/c/deep.txt", "/memories/.secret", "/memories/node_modules/x.js", "/memories/z.txt"]:
                ok(s, {"command": "create", "path": p, "file_text": "1"})
            listed = [line.split("\t")[1] for line in ok(s, {"command": "view", "path": "/memories"}).splitlines()[1:]]
            assert listed == ["/memories", "/memories/a/", "/memories/a/b/", "/memories/z.txt"], (name, listed)
            # Creating inside a file, or a file over a directory, is refused.
            err(s, {"command": "create", "path": "/memories/z.txt/child", "file_text": "1"})
            err(s, {"command": "create", "path": "/memories/a", "file_text": "1"})
            err(s, {"command": "create", "path": "/memories", "file_text": "1"})
            err(s, {"command": "view", "path": "/memories/a", "view_range": [1, 2]})
    print("ok  C2 listings stop at 2 levels and hide dot-files and node_modules")


def test_bad_input_is_reported_not_raised() -> None:
    s = InMemoryStore()
    assert err(s, {"command": "explode", "path": "/memories"}).startswith("Error: Unknown command `explode`")
    assert err(s, {"path": "/memories"}) == "Error: Missing required parameter `command`."
    assert err(s, {"command": "create", "path": "/memories/a"}) == "Error: Missing required parameter `file_text`."
    err(s, {"command": "insert", "path": "/memories/a", "insert_line": True, "insert_text": "x"})
    err(s, {"command": "view", "path": "/memories", "view_range": [1]})
    print("ok  unknown commands and malformed parameters become is_error results")


# --------------------------------------------------------------------------- #
# C3  str_replace never guesses
# --------------------------------------------------------------------------- #

def test_str_replace_zero_or_many_changes_nothing() -> None:
    with each_store() as stores:
        for name, s in stores:
            text = "cat\ndog\ncat\n"
            ok(s, {"command": "create", "path": "/memories/p.txt", "file_text": text})
            assert err(s, {"command": "str_replace", "path": "/memories/p.txt", "old_str": "cat", "new_str": "x"}) == (
                "No replacement was performed. Multiple occurrences of old_str `cat` in lines: 1, 3. "
                "Please ensure it is unique"
            ), name
            assert err(s, {"command": "str_replace", "path": "/memories/p.txt", "old_str": "emu", "new_str": "x"}) == (
                "No replacement was performed, old_str `emu` did not appear verbatim in /memories/p.txt."
            ), name
            err(s, {"command": "str_replace", "path": "/memories/p.txt", "old_str": "", "new_str": "x"})
            assert read(s, "/memories/p.txt") == text, name
    print("ok  C3 str_replace with 0 or 2+ matches is an error and changes nothing")


# --------------------------------------------------------------------------- #
# C4  root protection and recursive delete
# --------------------------------------------------------------------------- #

def test_root_protected_and_delete_recursive() -> None:
    with each_store() as stores:
        for name, s in stores:
            ok(s, {"command": "create", "path": "/memories/d/e/f.txt", "file_text": "1"})
            assert err(s, {"command": "delete", "path": "/memories"}) \
                == "Error: Cannot delete the /memories directory itself.", name
            err(s, {"command": "delete", "path": "/memories/"})
            err(s, {"command": "rename", "old_path": "/memories", "new_path": "/memories/x"})
            err(s, {"command": "rename", "old_path": "/memories/d", "new_path": "/memories"})
            err(s, {"command": "rename", "old_path": "/memories/d", "new_path": "/memories/d/e/inner"})
            assert ok(s, {"command": "view", "path": "/memories/d/e/f.txt"}).endswith("\t1"), name

            ok(s, {"command": "delete", "path": "/memories/d"})
            err(s, {"command": "view", "path": "/memories/d/e/f.txt"})
            err(s, {"command": "view", "path": "/memories/d"})
            assert ok(s, {"command": "view", "path": "/memories"}).splitlines()[1:] == ["0B\t/memories"], name
    print("ok  C4 /memories cannot be deleted or renamed; deleting a directory removes its children")


# --------------------------------------------------------------------------- #
# C5  nothing outside the DirStore root is ever touched
# --------------------------------------------------------------------------- #

TRAVERSALS = [
    "/memories/../secrets.env",
    "/memories/../../secrets.env",
    "/memories/a/../../secrets.env",
    "/memories/%2e%2e%2fsecrets.env",
    "/memories/..%2fsecrets.env",
    "/memories/..\\secrets.env",
    "/memoriesX/../secrets.env",
    "/etc/passwd",
    "/memories/link/secrets.env",  # through the planted symlink
    "/memories/link",
]


def test_traversal_never_escapes_dirstore() -> None:
    with tmpdir() as base:
        root = base / "root"
        root.mkdir()
        sentinel = base / "secrets.env"
        sentinel.write_bytes(b"API_KEY=do-not-touch\n")
        (root / "link").symlink_to(base, target_is_directory=True)  # escape hatch inside the root
        before = sentinel.read_bytes()
        s = DirStore(root)
        ok(s, {"command": "create", "path": "/memories/inside.txt", "file_text": "hello"})

        attempts = 0
        for bad in TRAVERSALS:
            for cmd in [
                {"command": "view", "path": bad},
                {"command": "create", "path": bad, "file_text": "pwned"},
                {"command": "str_replace", "path": bad, "old_str": "API_KEY", "new_str": "pwned"},
                {"command": "insert", "path": bad, "insert_line": 0, "insert_text": "pwned"},
                {"command": "delete", "path": bad},
                {"command": "rename", "old_path": bad, "new_path": "/memories/stolen.env"},
                {"command": "rename", "old_path": "/memories/inside.txt", "new_path": bad},
            ]:
                r = handle(s, cmd, OVERWRITE)
                assert r.is_error, f"{cmd} succeeded: {r.text!r}"
                assert "do-not-touch" not in r.text, f"{cmd} leaked the sentinel"
                attempts += 1

        assert sentinel.read_bytes() == before
        assert sorted(p.name for p in base.iterdir()) == ["root", "secrets.env"]
        assert (root / "inside.txt").read_text() == "hello"
        assert not (root / "stolen.env").exists()
    print(f"ok  C5 {attempts} traversal attempts across path/old_path/new_path leave the outside untouched")


# --------------------------------------------------------------------------- #
# C6  the loop
# --------------------------------------------------------------------------- #

def _text(t: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=t)


def _use(id_: str, inp: dict) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=id_, name="memory", input=inp)


def _resp(stop: str, *blocks: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(stop_reason=stop, content=list(blocks))


class FakeClient:
    """Replays scripted responses and records every create() call's kwargs."""

    def __init__(self, scripted: list[SimpleNamespace]) -> None:
        self._scripted = list(scripted)
        self.calls: list[dict] = []
        self.messages = self

    def create(self, **kwargs) -> SimpleNamespace:
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        return self._scripted.pop(0)


def test_loop_wires_tool_results() -> None:
    client = FakeClient([
        _resp("tool_use", _text("Checking memory."), _use("toolu_1", {"command": "view", "path": "/memories"})),
        _resp("tool_use",
              _use("toolu_2", {"command": "create", "path": "/memories/user.md", "file_text": "likes tea"}),
              _use("toolu_3", {"command": "view", "path": "/memories/../etc"})),
        _resp("end_turn", _text("Noted.")),
    ])
    store = InMemoryStore()
    answer = agent.run_session(client, store, "remember I like tea", create_mode=OVERWRITE)

    assert answer == "Noted."
    assert all(c["tools"] == [{"type": "memory_20250818", "name": "memory"}] for c in client.calls)
    assert all(c["model"] == agent.MODEL for c in client.calls)
    first = client.calls[1]["messages"][-1]["content"]
    assert first == [{
        "type": "tool_result", "tool_use_id": "toolu_1",
        "content": "Here're the files and directories up to 2 levels deep in /memories, "
                   "excluding hidden items and node_modules:\n0B\t/memories",
    }]
    second = client.calls[2]["messages"][-1]["content"]
    assert [b["tool_use_id"] for b in second] == ["toolu_2", "toolu_3"]
    assert "is_error" not in second[0]  # success never carries the flag
    assert second[1]["is_error"] is True and second[1]["content"].startswith("Error: Invalid `path`")
    assert read(store, "/memories/user.md") == "likes tea"
    print("ok  C6 loop declares the tool exactly and pairs each tool_use with its tool_result")


def test_loop_fails_loudly() -> None:
    def expect_agent_error(scripted: list[SimpleNamespace], **kw) -> None:
        try:
            agent.run_session(FakeClient(scripted), InMemoryStore(), "hi", create_mode=OVERWRITE, **kw)
        except agent.AgentError:
            return
        raise AssertionError("expected AgentError")

    expect_agent_error([_resp("max_tokens", _text("trunc"))])
    looping = _resp("tool_use", _use("t", {"command": "view", "path": "/memories"}))
    expect_agent_error([looping, looping], max_turns=2)

    other = SimpleNamespace(type="tool_use", id="t9", name="bash", input={})
    client = FakeClient([_resp("tool_use", other), _resp("end_turn", _text("ok"))])
    agent.run_session(client, InMemoryStore(), "hi", create_mode=OVERWRITE)
    sent = client.calls[1]["messages"][-1]["content"][0]
    assert sent["tool_use_id"] == "t9" and sent["is_error"] is True
    print("ok  C6 bad stop_reason and exhausted turns raise; a foreign tool name is an is_error result")


def test_storage_failure_propagates() -> None:
    class BrokenDisk(InMemoryStore):
        def write_text(self, path, text):  # noqa: ANN001
            raise OSError("disk full")

    try:
        handle(BrokenDisk(), {"command": "create", "path": "/memories/a", "file_text": "x"}, OVERWRITE)
    except OSError:
        print("ok  a storage OSError propagates instead of being reported to the model")
        return
    raise AssertionError("OSError was swallowed")


# --------------------------------------------------------------------------- #
# C7  memory survives across sessions
# --------------------------------------------------------------------------- #

def test_two_sessions_share_only_the_directory() -> None:
    with tmpdir() as d:
        s1 = FakeClient([
            _resp("tool_use", _use("a1", {"command": "view", "path": "/memories"})),
            _resp("tool_use", _use("a2", {"command": "create", "path": "/memories/prefs.md",
                                          "file_text": "favorite color: teal\n"})),
            _resp("end_turn", _text("Saved.")),
        ])
        agent.run_session(s1, DirStore(d), "my favorite color is teal", create_mode=OVERWRITE)

        s2 = FakeClient([
            _resp("tool_use", _use("b1", {"command": "view", "path": "/memories"})),
            _resp("tool_use", _use("b2", {"command": "view", "path": "/memories/prefs.md"})),
            _resp("end_turn", _text("Teal.")),
        ])
        agent.run_session(s2, DirStore(d), "what is my favorite color?", create_mode=OVERWRITE)

        assert len(s2.calls[0]["messages"]) == 1  # session 2 starts with no history
        listing = s2.calls[1]["messages"][-1]["content"][0]["content"]
        assert "\t/memories/prefs.md" in listing
        content = s2.calls[2]["messages"][-1]["content"][0]["content"]
        assert content == "Here's the content of /memories/prefs.md with line numbers:\n     1\tfavorite color: teal"
    print("ok  C7 a fact written in session 1 is read back by session 2 via a fresh DirStore")


TESTS: list[Callable[[], None]] = [
    test_paths_accept_valid,
    test_paths_reject_invalid,
    test_command_table_both_stores,
    test_listing_rules_both_stores,
    test_bad_input_is_reported_not_raised,
    test_str_replace_zero_or_many_changes_nothing,
    test_root_protected_and_delete_recursive,
    test_traversal_never_escapes_dirstore,
    test_loop_wires_tool_results,
    test_loop_fails_loudly,
    test_storage_failure_propagates,
    test_two_sessions_share_only_the_directory,
]

if __name__ == "__main__":
    for t in TESTS:
        t()
    print(f"\nAll {len(TESTS)} self-tests passed.")
