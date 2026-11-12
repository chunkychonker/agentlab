"""A stand-in ``gh`` executable for the milestone-5 tests (not a test module).

``FakeGh(root, ...)`` writes a small Python script to ``root`` and runs it
with this interpreter (``-I``). The script records each call's argv, stdin
and cwd to ``calls.jsonl`` and answers from ``scenario.json``. No network,
and the real ``gh`` is never run.

Each scenario key (``list``, ``create``, ``view_body``, ``view_mergeable``)
is a list of replies, used in order and the last one repeated:
``{"rc": 0, "stdout": "...", "stderr": "..."}``, or ``{"echo_body": true}``
to print the body the last ``pr create`` received (what GitHub would store).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PR_URL = "https://github.com/acme/widgets/pull/7\n"
DEFAULTS: dict[str, list[dict]] = {
    "list": [{"stdout": "[]\n"}],
    "create": [{"stdout": f"Creating pull request for nightloop/x into main\n\n{PR_URL}"}],
    "view_body": [{"echo_body": True}],
    "view_mergeable": [{"stdout": "MERGEABLE\n"}],
}

_SCRIPT = '''\
import json, os, sys

STATE = {state!r}
argv = sys.argv[1:]
stdin = sys.stdin.read()
calls_path = os.path.join(STATE, "calls.jsonl")
with open(os.path.join(STATE, "scenario.json"), encoding="utf-8") as f:
    scenario = json.load(f)
previous = []
if os.path.exists(calls_path):
    with open(calls_path, encoding="utf-8") as f:
        previous = [json.loads(line) for line in f if line.strip()]

key = None
if argv[:2] == ["pr", "list"]:
    key = "list"
elif argv[:2] == ["pr", "create"]:
    key = "create"
elif argv[:2] == ["pr", "view"] and "--json" in argv:
    key = "view_" + argv[argv.index("--json") + 1]
with open(calls_path, "a", encoding="utf-8") as f:
    f.write(json.dumps({{"argv": argv, "stdin": stdin, "cwd": os.getcwd(), "key": key}}) + "\\n")
if key not in scenario:
    sys.stderr.write("fake gh: unexpected command %r\\n" % (argv,))
    sys.exit(99)
replies = scenario[key]
reply = replies[min(sum(1 for c in previous if c["key"] == key), len(replies) - 1)]
out = reply.get("stdout", "")
if reply.get("echo_body"):
    out = [c for c in previous if c["key"] == "create"][-1]["stdin"] + "\\n"
sys.stdout.write(out)
sys.stderr.write(reply.get("stderr", ""))
sys.exit(reply.get("rc", 0))
'''


class FakeGh:
    """A fake ``gh`` under ``root``; ``replies`` override ``DEFAULTS`` per key. ``program`` is its path."""

    def __init__(self, root: Path, **replies: list[dict]) -> None:
        root.mkdir(parents=True, exist_ok=True)
        self._root = root
        (root / "scenario.json").write_text(json.dumps({**DEFAULTS, **replies}), encoding="utf-8")
        self.program = root / "gh"
        self.program.write_text(f"#!{sys.executable} -I\n" + _SCRIPT.format(state=str(root)), encoding="utf-8")
        self.program.chmod(0o755)

    def calls(self) -> list[dict]:
        """Every call so far, in order."""
        path = self._root / "calls.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def keys(self) -> list[str | None]:
        """The scenario key of every call so far, in order."""
        return [call["key"] for call in self.calls()]
