#!/usr/bin/env python3
"""fake_gh.py: a stand-in for `gh issue` in offline tests. Never touches GitHub.

Used by test_issues.sh and test_migrate_backlog_findings.sh, which put a `gh`
wrapper on PATH that runs this. It keeps its issues in $FAKE_GH_DIR/issues.json
(a list of {number, title, state, labels, body, comments}) and appends every
call's arguments, one JSON list per line, to $FAKE_GH_DIR/calls.

Understands only what issues.sh runs:
  gh issue list --label L --state all --limit N --json number,title,state
  gh issue create --title=T --label L --body-file F   (prints the issue URL)
  gh issue comment N --body-file F
Anything else exits 1. $FAKE_GH_FAIL, a comma list of list/create/comment,
makes those calls fail (exit 1) without changing state.

Python 3.9 stdlib only: it runs under /usr/bin/python3.
"""

import json
import os
import sys
from typing import Dict, List, Optional


def _opt(args: List[str], name: str) -> Optional[str]:
    """The value of --name, given as `--name v` or `--name=v`."""
    for i, a in enumerate(args):
        if a == name and i + 1 < len(args):
            return args[i + 1]
        if a.startswith(name + "="):
            return a[len(name) + 1:]
    return None


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def main(argv: List[str]) -> int:
    state_dir = os.environ["FAKE_GH_DIR"]
    store = os.path.join(state_dir, "issues.json")
    with open(os.path.join(state_dir, "calls"), "a", encoding="utf-8") as f:
        f.write(json.dumps(argv) + "\n")
    issues: List[Dict] = json.loads(_read(store)) if os.path.exists(store) else []
    failing = set(filter(None, os.environ.get("FAKE_GH_FAIL", "").split(",")))

    if argv[:1] != ["issue"] or len(argv) < 2:
        print("fake_gh: unsupported: %s" % argv, file=sys.stderr)
        return 1
    verb = argv[1]
    if verb in failing:
        print("fake_gh: %s failed (FAKE_GH_FAIL)" % verb, file=sys.stderr)
        return 1

    if verb == "list":
        label = _opt(argv, "--label")
        rows = [{"number": i["number"], "title": i["title"], "state": i["state"]}
                for i in issues if label in i["labels"]]
        print(json.dumps(rows))
        return 0
    if verb == "create":
        title, label, body_file = _opt(argv, "--title"), _opt(argv, "--label"), _opt(argv, "--body-file")
        if not (title and label and body_file):
            print("fake_gh: create needs --title, --label, --body-file", file=sys.stderr)
            return 1
        number = max([i["number"] for i in issues] or [0]) + 1
        issues.append({"number": number, "title": title, "state": "OPEN",
                       "labels": [label], "body": _read(body_file), "comments": []})
        print("https://github.com/example/repo/issues/%d" % number)
    elif verb == "comment":
        body_file = _opt(argv, "--body-file")
        target = [i for i in issues if len(argv) > 2 and str(i["number"]) == argv[2]]
        if not target or not body_file:
            print("fake_gh: no such issue or no --body-file", file=sys.stderr)
            return 1
        target[0]["comments"].append(_read(body_file))
    else:
        print("fake_gh: unsupported: %s" % argv, file=sys.stderr)
        return 1
    with open(store, "w", encoding="utf-8") as f:
        json.dump(issues, f)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
