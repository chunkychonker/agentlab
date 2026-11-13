"""Stand-in executables for the milestone-5 shipper tests (not a test module).

``FakeGh`` is a Python script that stands in for the GitHub CLI: it records
every call's argv (and the body it was given on stdin) and answers from a
scripted state file. No test talks to GitHub. ``failing_git`` writes a
wrapper around the real git that refuses chosen subcommands, so a step that
real git would never fail (``branch -f``, ``merge-base --is-ancestor``) can
be made to fail.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

# The reviewer's output contract (agentlab-reviewer.md), with a multi-line Ran.
VERDICT = """\
VERDICT: PASS
Increment: projects/demo/
Ran: python3 -m unittest -v -> 12 passed
ruff check projects/demo -> clean
Findings:
- warning: retry loop has no upper bound on total wait (client.py:41)
- nit: README example uses an old flag name
- nit: unicode is fine — café ✓
"""
FINDINGS_LINES = [line for line in VERDICT.splitlines() if line.startswith("- ")]
PR_URL = "https://github.com/o/r/pull/7\n"

_FAKE_GH = '''\
#!{python} -I
import json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
state = json.loads((HERE / "state.json").read_text(encoding="utf-8"))
args = sys.argv[1:]
stdin = None
if "--body-file" in args and args[args.index("--body-file") + 1] == "-":
    stdin = sys.stdin.buffer.read().decode("utf-8")
calls = HERE / "calls.jsonl"
with calls.open("a", encoding="utf-8") as log:
    log.write(json.dumps({{"argv": args, "stdin": stdin}}) + "\\n")
if args[:2] == ["pr", "create"]:
    if stdin is not None:
        (HERE / "body.txt").write_text(stdin, encoding="utf-8")
    sys.stdout.write(state["create_stdout"])
    sys.stderr.write(state["create_stderr"])
    sys.exit(state["create_rc"])
if args[:2] == ["pr", "view"]:
    if state["view_rc"] != 0:
        sys.stderr.write("fake gh: view refused\\n")
        sys.exit(state["view_rc"])
    if state["view_raw"] is not None:
        sys.stdout.write(state["view_raw"])
        sys.exit(0)
    views = sum(1 for line in calls.read_text(encoding="utf-8").splitlines()
                if json.loads(line)["argv"][:2] == ["pr", "view"])
    values = state["mergeable"]
    body_file = HERE / "body.txt"
    body = body_file.read_text(encoding="utf-8") if body_file.exists() else ""
    if state["strip_marker"]:
        body = body.replace("<!-- agentlab-review -->", "")
    print(json.dumps({{"body": body, "mergeable": values[min(views, len(values)) - 1]}}))
    sys.exit(0)
sys.stderr.write("fake gh: unsupported call\\n")
sys.exit(2)
'''

_FAILING_GIT = '''\
#!{python} -I
import os, sys

REAL = {real!r}
FAIL = {fail!r}
args = sys.argv[1:]
rest = args[2:] if args[:1] == ["-C"] else args
if any(rest[:len(prefix)] == prefix for prefix in FAIL):
    sys.stderr.write("refused by the stand-in git\\n")
    sys.exit(1)
os.execv(REAL, [REAL, *args])
'''


class FakeGh:
    """A fake GitHub CLI in ``directory``; ``path`` is the program to hand the shipper.

    State (each overridable by keyword): ``create_rc`` (0), ``create_stdout``
    (``PR_URL``), ``create_stderr`` (""), ``view_rc`` (0), ``view_raw``
    (None: serve JSON), ``strip_marker`` (False), ``mergeable`` (one value per
    view, the last repeating; default ``["MERGEABLE"]``). The served body is
    the one ``pr create`` received.
    """

    def __init__(self, directory: Path, **state: object) -> None:
        directory.mkdir(parents=True)
        self.directory = directory
        defaults: dict[str, object] = {
            "create_rc": 0, "create_stdout": PR_URL, "create_stderr": "",
            "view_rc": 0, "view_raw": None, "strip_marker": False, "mergeable": ["MERGEABLE"],
        }
        unknown = set(state) - set(defaults)
        if unknown:
            raise TypeError(f"unknown fake gh state {sorted(unknown)}")
        (directory / "state.json").write_text(json.dumps({**defaults, **state}), encoding="utf-8")
        self.path = directory / "gh"
        self.path.write_text(_FAKE_GH.format(python=sys.executable), encoding="utf-8")
        self.path.chmod(0o755)

    def calls(self) -> list[dict]:
        log = self.directory / "calls.jsonl"
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]

    def calls_to(self, *subcommand: str) -> list[dict]:
        return [call for call in self.calls() if call["argv"][: len(subcommand)] == list(subcommand)]


def failing_git(directory: Path, *refused: tuple[str, ...]) -> Path:
    """Write a git wrapper that exits 1 for any subcommand starting with one of ``refused``."""
    real = shutil.which("git")
    if real is None:
        raise RuntimeError("git is not on PATH")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "git"
    path.write_text(_FAILING_GIT.format(python=sys.executable, real=real, fail=[list(p) for p in refused]),
                    encoding="utf-8")
    path.chmod(0o755)
    return path
