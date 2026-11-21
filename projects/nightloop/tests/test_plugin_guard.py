"""Milestone 8, criteria 2 and 3: the plugin's publish guard, run as Claude Code runs it.

Each recorded payload goes to the script on stdin in a subprocess. Allowed
means exit 0 with nothing printed; blocked means exit 2 with the reason on
stderr and nothing on stdout. Exit 1 would fail open, so it never counts.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import unittest

from .plugin_support import (
    GUARD, PLUGIN, SUBPROCESS_TIMEOUT_S, SYSTEM_PYTHON, load_guard, run_guard, system_python_differs,
)

ALLOW, BLOCK = True, False

# (label, Bash command, expected). The guard takes no phase: the same table
# holds whatever agent is running.
BASH_CASES: tuple[tuple[str, str, bool], ...] = (
    ("bare push", "git push", BLOCK),
    ("push with refspec", "git push origin main", BLOCK),
    ("global -C option", "git -C x push", BLOCK),
    ("global -c option", "git -c user.name=x push --force", BLOCK),
    ("absolute git path", "/usr/bin/git push", BLOCK),
    ("rtk prefix", "rtk git push", BLOCK),
    ("env assignment prefix", "env A=1 git push", BLOCK),
    ("sudo prefix", "sudo git push", BLOCK),
    ("bash -c", "bash -c 'git push'", BLOCK),
    ("nested three deep", "bash -c \"bash -c 'bash -c \\\"git push\\\"'\"", BLOCK),
    ("nested four deep, harmless", "bash -c \"bash -c 'bash -c \\\"bash -c ls\\\"'\"", BLOCK),
    ("gh pr create", "gh pr create --fill", BLOCK),
    ("gh pr merge", "gh pr merge 3 --squash", BLOCK),
    ("gh api POST", "gh api -X POST repos/o/r/issues", BLOCK),
    ("bare gh", "gh", BLOCK),
    ("after a semicolon", "echo hi; git push", BLOCK),
    ("after &&", "git status && git push", BLOCK),
    ("in backticks", "echo `git push`", BLOCK),
    ("in $( )", "echo $(git push)", BLOCK),
    ("after a comment line", "ls # note\ngit push", BLOCK),
    ("unquoted heredoc runs $( )", "cat <<EOF\n$(git push)\nEOF", BLOCK),
    ("unparseable, mentions push", "echo 'x; git push", BLOCK),
    ("unparseable, mentions gh", "echo 'x; gh pr create", BLOCK),
    ("git status", "git status", ALLOW),
    ("commit message says push", 'git commit -m "push the button"', ALLOW),
    ("git log", "git log --oneline", ALLOW),
    ("gh pr view", "gh pr view 3", ALLOW),
    ("gh pr list", "gh pr list", ALLOW),
    ("gh auth status", "gh auth status", ALLOW),
    ("grep for the words", 'grep -rn "git push" .', ALLOW),
    ("run_silent test run", "run_silent tests -- python3 -m unittest", ALLOW),
    ("quoted heredoc to a file", "cat <<'EOF' > notes.md\ngit push\nEOF", ALLOW),
    ("unparseable, mentions neither", "echo 'unclosed", ALLOW),
)

# (label, raw stdin, expected) for payloads that are not a well-formed Bash call.
RAW_CASES: tuple[tuple[str, str, bool], ...] = (
    ("non-Bash tool", json.dumps({"tool_name": "Write",
                                  "tool_input": {"file_path": "x", "content": "git push"}}), ALLOW),
    ("JSON array payload", "[1, 2]", BLOCK),
    ("JSON string payload", '"git push"', BLOCK),
    ("JSON null payload", "null", BLOCK),
    ("Bash with no command", json.dumps({"tool_name": "Bash", "tool_input": {}}), BLOCK),
    ("Bash with a non-string command", json.dumps({"tool_name": "Bash", "tool_input": {"command": 5}}), BLOCK),
    ("Bash with tool_input not an object", json.dumps({"tool_name": "Bash", "tool_input": "git status"}), BLOCK),
)


def bash_payload(command: str) -> str:
    return json.dumps({"session_id": "s", "hook_event_name": "PreToolUse", "tool_name": "Bash",
                       "tool_input": {"command": command}, "agent_type": "nightloop:builder"})


def all_cases() -> list[tuple[str, str, bool]]:
    return [(label, bash_payload(cmd), want) for label, cmd, want in BASH_CASES] + list(RAW_CASES)


class GuardTableTest(unittest.TestCase):
    def assertOutcome(self, done: subprocess.CompletedProcess[str], want: bool) -> None:
        if want is ALLOW:
            self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "", ""))
        else:
            self.assertEqual(done.returncode, 2, done.stderr)
            self.assertEqual(done.stdout, "")
            self.assertIn("publish_guard", done.stderr)

    def test_the_table_is_big_enough(self) -> None:
        self.assertGreaterEqual(len(all_cases()), 25)
        self.assertTrue(any(w is ALLOW for *_, w in all_cases()))
        self.assertTrue(any(w is BLOCK for *_, w in all_cases()))

    def test_each_recorded_payload_as_a_subprocess(self) -> None:
        for label, stdin, want in all_cases():
            with self.subTest(case=label):
                self.assertOutcome(run_guard(stdin), want)

    def test_a_block_names_what_was_blocked(self) -> None:
        done = run_guard(bash_payload("gh pr merge 3"))
        self.assertIn("'gh pr merge'", done.stderr)

    @unittest.skipUnless(system_python_differs(), f"{SYSTEM_PYTHON} absent or the same interpreter")
    def test_the_table_under_the_system_python(self) -> None:
        # The hook runs as `python3` from PATH, which may be macOS's 3.9.
        driver = (
            "import importlib.util, json, sys\n"
            "spec = importlib.util.spec_from_file_location('g', sys.argv[1])\n"
            "g = importlib.util.module_from_spec(spec); spec.loader.exec_module(g)\n"
            "print(json.dumps([g.decide(json.loads(s))[0] for s in json.loads(sys.stdin.read())]))\n"
        )
        cases = all_cases()
        done = subprocess.run(
            [str(SYSTEM_PYTHON), "-I", "-B", "-c", driver, str(GUARD)],
            input=json.dumps([stdin for _, stdin, _ in cases]),
            capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT_S,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout), [want for *_, want in cases])
        self.assertOutcome(run_guard(bash_payload("git push"), python=str(SYSTEM_PYTHON)), BLOCK)


class GuardFailsClosedTest(unittest.TestCase):
    def test_empty_stdin_blocks(self) -> None:
        done = run_guard("")
        self.assertEqual(done.returncode, 2)
        self.assertIn("not a JSON object", done.stderr)

    def test_invalid_json_blocks(self) -> None:
        done = run_guard('{"tool_name": "Bash",')
        self.assertEqual(done.returncode, 2)
        self.assertEqual(done.stdout, "")

    def test_non_utf8_stdin_blocks(self) -> None:
        done = subprocess.run([sys.executable, "-I", "-B", str(GUARD)], input=b"\xff\xfe{",
                              capture_output=True, timeout=SUBPROCESS_TIMEOUT_S)
        self.assertEqual(done.returncode, 2)

    def test_an_exception_inside_decide_blocks(self) -> None:
        guard = load_guard()

        def explode(payload: object) -> tuple[bool, str]:
            raise RuntimeError("boom")

        guard.decide = explode
        stderr = io.StringIO()
        # A payload decide() would otherwise allow, so only the exception can block it.
        code = guard.main(io.StringIO(bash_payload("git status")), stderr)
        self.assertEqual(code, 2)
        self.assertIn("RuntimeError: boom", stderr.getvalue())

    def test_the_unpatched_main_allows_the_same_payload(self) -> None:
        guard = load_guard()
        stderr = io.StringIO()
        self.assertEqual(guard.main(io.StringIO(bash_payload("git status")), stderr), 0)
        self.assertEqual(stderr.getvalue(), "")


class GuardRegistrationTest(unittest.TestCase):
    def test_hooks_json_runs_the_guard_before_every_bash_call(self) -> None:
        doc = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        groups = doc["hooks"]["PreToolUse"]
        self.assertEqual(
            [(g["matcher"], h) for g in groups for h in g["hooks"]],
            [("Bash", {"type": "command", "command": "python3",
                       "args": ["${CLAUDE_PLUGIN_ROOT}/hooks/publish_guard.py"]})],
        )


if __name__ == "__main__":
    unittest.main()
