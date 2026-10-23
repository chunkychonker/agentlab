"""Offline self-test for the PreToolUse Bash guard.

Asserts the research note's acceptance criteria against `hook.decide` (the
hook's contract: payload text in, exit code + stdout + stderr out), plus one
subprocess smoke test per recorded fixture. No network, no key, no Claude
Code session.

Run: python3 -m unittest -v test_guard   (from this directory)
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from classify import Deny, Rule, classify
from hook import decide

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
HOOK = HERE / "hook.py"


def bash_payload(command: str) -> str:
    """A PreToolUse payload in the documented shape for one Bash call."""
    return json.dumps(
        {
            "session_id": "test-session",
            "prompt_id": "test-prompt",
            "transcript_path": "/tmp/test-transcript.jsonl",
            "cwd": "/tmp/project",
            "permission_mode": "default",
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command, "description": "test"},
            "tool_use_id": "toolu_test",
        }
    )


class GuardAssertions(unittest.TestCase):
    def assert_denied(self, payload_text: str) -> str:
        """Assert the result is a well-formed deny; return its reason."""
        result = decide(payload_text)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stderr, "")
        # Exactly one JSON object on stdout (json.loads rejects trailing objects).
        body = json.loads(result.stdout)
        self.assertEqual(set(body), {"hookSpecificOutput"})
        out = body["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "PreToolUse")
        self.assertEqual(out["permissionDecision"], "deny")
        self.assertIsInstance(out["permissionDecisionReason"], str)
        self.assertTrue(out["permissionDecisionReason"].strip())
        return out["permissionDecisionReason"]

    def assert_no_decision(self, payload_text: str) -> None:
        result = decide(payload_text)
        self.assertEqual((result.exit_code, result.stdout, result.stderr), (0, "", ""))


class RecursiveRmDenied(GuardAssertions):
    """Criterion 1: recursive rm of root/home, including compound and wrapped forms."""

    COMMANDS = [
        "rm -rf /",
        "rm -fr ~",
        "rm -r -f $HOME",
        "rm --recursive --force /*",
        "sudo rm -rf /",
        "ls && rm -rf ~",
        "bash -c 'rm -rf /'",
        # Extra spellings the tokenizer is meant to catch.
        "rm -rf ~/",
        'rm -rf "$HOME"',
        "rm -rf ${HOME}/*",
        "rm -Rf //",
        "rm -rf -- /",
        "/bin/rm -rf /",
        "sudo -u root rm -rf /",
        "FOO=1 env rm -rf ~",
        "echo hi; rm -rf ~",
        "true || rm -rf ~",
        "cat x | rm -rf ~",
        "cd /tmp\nrm -rf ~",
        "echo $(rm -rf /)",
        "echo `rm -rf /`",
        'sh -lc "cd / && rm -rf ~"',
        "eval 'rm -rf /'",
        "rm --rec ~",
        "if true; then rm -rf /; fi",
    ]

    def test_each_is_denied_with_reason_naming_the_rule(self) -> None:
        for command in self.COMMANDS:
            with self.subTest(command=command):
                reason = self.assert_denied(bash_payload(command))
                self.assertIn(Rule.RECURSIVE_RM_ROOT_OR_HOME.value, reason)


class ForcePushToProtectedDenied(GuardAssertions):
    """Criterion 2: force push to main/master."""

    COMMANDS = [
        "git push --force origin main",
        "git push -f origin master",
        "git push origin +main",
        "git push --force-with-lease origin main",
        "git push --force-with-lease=main:abc123 origin main",
        "git push -fu origin main",
        "git push origin HEAD:refs/heads/main --force",
        "git -C /tmp/repo push -f origin main",
        "git push origin +feature:main",
    ]

    def test_each_is_denied_with_reason_naming_the_rule(self) -> None:
        for command in self.COMMANDS:
            with self.subTest(command=command):
                reason = self.assert_denied(bash_payload(command))
                self.assertIn(Rule.FORCE_PUSH_PROTECTED_BRANCH.value, reason)


class OrdinaryCommandsAllowed(GuardAssertions):
    """Criterion 3: no decision (exit 0, empty stdout) for safe look-alikes."""

    COMMANDS = [
        "rm -rf node_modules",
        "rm -rf ./build",
        "rm file.txt",
        "rm -f ~/.cache/thing.lock",
        "rm -rf ~/projects/old",
        "rm ~",  # not recursive: rm refuses directories without -r
        "git push origin feature",
        "git push --force origin feature",
        "git push origin main",  # non-force push to main is a normal fast-forward
        "git status && git log -1",
        "ls ~",
        "ls /",
        # Pinned choice: a quoted string passed to echo is data, not a command.
        'echo "rm -rf /"',
        "grep -r 'rm -rf /' .",
    ]

    def test_each_gets_no_decision(self) -> None:
        for command in self.COMMANDS:
            with self.subTest(command=command):
                self.assert_no_decision(bash_payload(command))


class PayloadEdgeCases(GuardAssertions):
    """Criterion 4: malformed input fails closed; non-Bash tools are ignored."""

    def test_malformed_json_is_denied(self) -> None:
        reason = self.assert_denied('{"tool_name": "Bash", ')
        self.assertIn("malformed-payload", reason)

    def test_non_object_json_is_denied(self) -> None:
        self.assert_denied("[1, 2, 3]")

    def test_bash_without_command_is_denied(self) -> None:
        self.assert_denied(json.dumps({"tool_name": "Bash", "tool_input": {}}))

    def test_bash_with_non_string_command_is_denied(self) -> None:
        self.assert_denied(json.dumps({"tool_name": "Bash", "tool_input": {"command": 42}}))

    def test_missing_tool_name_is_denied(self) -> None:
        self.assert_denied(json.dumps({"tool_input": {"command": "ls"}}))

    def test_non_bash_tool_gets_no_decision(self) -> None:
        payload = json.dumps(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "Write",
                "tool_input": {"file_path": "/tmp/x", "content": "rm -rf /"},
            }
        )
        self.assert_no_decision(payload)

    def test_unbalanced_quotes_are_denied(self) -> None:
        reason = self.assert_denied(bash_payload("echo 'unterminated"))
        self.assertIn(Rule.UNPARSEABLE_COMMAND.value, reason)

    def test_excessive_nesting_is_denied(self) -> None:
        command = "ls"
        for _ in range(6):
            command = f"bash -c {json.dumps(command)}"
        reason = self.assert_denied(bash_payload(command))
        self.assertIn(Rule.NESTING_TOO_DEEP.value, reason)

    def test_classifier_crash_is_denied_not_propagated(self) -> None:
        def crashing_classifier(command: str) -> Deny:
            raise KeyError("boom")

        result = decide(bash_payload("ls"), classifier=crashing_classifier)
        body = json.loads(result.stdout)
        self.assertEqual(body["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("internal-error", body["hookSpecificOutput"]["permissionDecisionReason"])

    def test_never_emits_allow(self) -> None:
        for command in OrdinaryCommandsAllowed.COMMANDS + ForcePushToProtectedDenied.COMMANDS:
            with self.subTest(command=command):
                self.assertNotIn('"allow"', decide(bash_payload(command)).stdout)


class ClassifierIsTotal(unittest.TestCase):
    """classify never raises on str input, including empty and odd strings."""

    def test_odd_inputs_return_a_verdict(self) -> None:
        for command in ["", "   ", "\n\n", ";;", "&&", "rm", "rm -rf", "git", "git push", "sudo", "bash -c"]:
            with self.subTest(command=command):
                classify(command)  # must not raise


class SubprocessSmoke(unittest.TestCase):
    """Criterion 5: running hook.py as a process matches decide() on each fixture."""

    EXPECTED_DENY = {"deny_rm_rf_home.json", "deny_force_push_main.json", "malformed.txt"}

    def test_fixtures_match_decide(self) -> None:
        fixtures = sorted(FIXTURES.iterdir())
        self.assertEqual(len(fixtures), 5)
        for path in fixtures:
            with self.subTest(fixture=path.name):
                text = path.read_text(encoding="utf-8")
                proc = subprocess.run(
                    [sys.executable, str(HOOK)],
                    input=text,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                expected = decide(text)
                self.assertEqual(
                    (proc.returncode, proc.stdout, proc.stderr),
                    (expected.exit_code, expected.stdout, expected.stderr),
                )
                is_deny = proc.stdout != ""
                self.assertEqual(is_deny, path.name in self.EXPECTED_DENY)

    def test_non_utf8_stdin_is_denied(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(HOOK)], input=b"\xff\xfe", capture_output=True, timeout=30, check=False
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")


if __name__ == "__main__":
    unittest.main()
