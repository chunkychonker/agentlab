"""Stand-in ``gh`` and ``git`` executables for the milestone-5 shipper tests (not a test module).

``FakeGh`` writes a small Python program that behaves like the parts of
``gh`` the shipper uses (``pr list``, ``pr create``, ``pr view``) against a
JSON state file, and records every argv and stdin it gets. Nothing talks to
GitHub. ``GitRecorder`` writes a wrapper that logs each argv and then
``exec``s the real git, so a test can assert on exactly what was pushed.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

_FAKE_GH = r'''
import json, sys, time
STATE = {state!r}
with open(STATE, encoding="utf-8") as f:
    state = json.load(f)
argv = sys.argv[1:]
body_in = sys.stdin.read() if "--body-file" in argv and argv[argv.index("--body-file") + 1] == "-" else None
state["calls"].append({{"argv": argv, "stdin": body_in}})

def save():
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(state, f)

def opt(name):
    return argv[argv.index(name) + 1]

key = " ".join(argv[:2])
time.sleep(state.get("sleep", {{}}).get(key, 0))
if key in state.get("fail", {{}}):
    save()
    rc, err = state["fail"][key]
    sys.stderr.write("some detail\n" + err + "\n")
    sys.exit(rc)
if key == "pr list":
    head = opt("--head")
    found = [{{"number": p["number"], "url": p["url"]}} for p in state["prs"] if p["head"] == head and p["open"]]
    print(json.dumps(found))
elif key == "pr create":
    number = state["next"]
    state["next"] += 1
    url = "https://github.com/acme/widgets/pull/%d" % number
    state["prs"].append({{"number": number, "url": url, "head": opt("--head"), "base": opt("--base"),
                         "title": opt("--title"), "body": body_in, "open": True}})
    out = state.get("create_stdout")
    print(url if out is None else out)
elif key == "pr view":
    number = int(argv[2])
    pr = next(p for p in state["prs"] if p["number"] == number)
    field = opt("--json")
    if field == "body":
        print(state["body_override"] if "body_override" in state else pr["body"])
    elif field == "mergeable":
        seq = state["mergeable"]
        print(seq.pop(0) if len(seq) > 1 else seq[0])
    elif field == "mergeCommit":
        print(pr.get("merge_commit", ""))
    else:
        sys.exit("fake gh: unsupported field " + field)
else:
    sys.exit("fake gh: unsupported command " + key)
save()
'''

_GIT_RECORDER = r'''
import json, os, sys
with open({log!r}, "a", encoding="utf-8") as f:
    f.write(json.dumps(sys.argv[1:]) + "\n")
os.execv({git!r}, [{git!r}, *sys.argv[1:]])
'''


def _executable(path: Path, source: str) -> Path:
    path.write_text(f"#!{sys.executable} -I\n{source}", encoding="utf-8")
    path.chmod(0o755)
    return path


class FakeGh:
    """A scripted ``gh`` under ``root``; ``program`` is its path.

    State keys: ``prs`` (what exists; a PR's ``merge_commit`` is what
    ``pr view --json mergeCommit`` prints, empty when absent, the shape
    ``-q .mergeCommit.oid`` gives in ``.pipeline/run.sh``), ``next`` (the next PR number),
    ``mergeable`` (values read in order; the last one repeats),
    ``create_stdout`` (replaces the URL ``pr create`` prints),
    ``body_override`` (replaces every body read back), ``fail`` (``"pr
    create": [rc, stderr]``), ``sleep`` (``"pr view": seconds``).
    """

    def __init__(self, root: Path, **state: object) -> None:
        self._state = root / "gh-state.json"
        initial = {"prs": [], "next": 7, "mergeable": ["MERGEABLE"], "calls": []}
        initial.update(state)
        self._state.write_text(json.dumps(initial), encoding="utf-8")
        self.program = str(_executable(root / "fake-gh", _FAKE_GH.format(state=str(self._state))))

    def state(self) -> dict:
        return json.loads(self._state.read_text(encoding="utf-8"))

    def update(self, **changes: object) -> None:
        state = self.state()
        state.update(changes)
        self._state.write_text(json.dumps(state), encoding="utf-8")

    def commands(self) -> list[str]:
        """Each call as ``"<verb> <noun>"``, e.g. ``"pr create"``."""
        return [" ".join(call["argv"][:2]) for call in self.state()["calls"]]

    def created_bodies(self) -> list[str]:
        return [call["stdin"] for call in self.state()["calls"] if call["argv"][:2] == ["pr", "create"]]


class GitRecorder:
    """A ``git`` that logs every argv to ``root/git-argv.jsonl`` and then runs the real one."""

    def __init__(self, root: Path) -> None:
        real = shutil.which("git")
        assert real is not None, "git is not on PATH"
        self._log = root / "git-argv.jsonl"
        self._log.write_text("", encoding="utf-8")
        self.program = str(_executable(root / "recording-git", _GIT_RECORDER.format(log=str(self._log), git=real)))

    def calls(self) -> list[list[str]]:
        """Every argv after ``-C <repo>``."""
        lines = self._log.read_text(encoding="utf-8").splitlines()
        return [json.loads(line)[2:] for line in lines if line]


def verdict_text(findings: str = "- nit: the README could name the timeout", verdict: str = "PASS") -> str:
    """A reviewer verdict in the agentlab output contract."""
    return (
        f"VERDICT: {verdict}\n"
        "Spec: research/2026-11-15-note.md\n"
        "Ran: python3 -I -m unittest discover -v\n"
        "  242 tests OK\n"
        f"Findings: {findings}\n"
    )
