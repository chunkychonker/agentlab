"""Offline self-test: the acceptance criteria from the research note's build proposal.

No API key, no network, no Claude Code. Run from this directory:
    python3 -m unittest -v test_check_plugin
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from check_plugin import main
from plugin_rules import Severity, check_drift, check_manifest, check_mcp, has_failures

HERE = Path(__file__).resolve().parent
EXAMPLES = HERE.parent
DRIFT_PAIRS = (
    ("skills/commit-message/SKILL.md", EXAMPLES / "skill-anatomy/skills/commit-message/SKILL.md"),
    ("server/server.py", EXAMPLES / "mcp-hello-world/server.py"),
)

LISTING = frozenset(
    {
        ".claude-plugin",
        ".claude-plugin/plugin.json",
        ".mcp.json",
        "skills",
        "skills/commit-message",
        "skills/commit-message/SKILL.md",
        "server",
        "server/server.py",
    }
)
MANIFEST = {
    "name": "agentlab-bundle",
    "version": "0.1.0",
    "description": "d",
    "author": {"name": "a"},
}
MCP = {
    "mcpServers": {
        "hello-world": {
            "type": "stdio",
            "command": "python3",
            "args": ["${CLAUDE_PLUGIN_ROOT}/server/server.py"],
        }
    }
}


def errors(findings):
    return [f for f in findings if f.severity is Severity.ERROR]


def warnings(findings):
    return [f for f in findings if f.severity is Severity.WARNING]


def with_(base: dict, **changes) -> dict:
    out = json.loads(json.dumps(base))
    out.update(changes)
    return out


class ValidFixture(unittest.TestCase):
    # Criterion 1
    def test_valid_manifest_and_mcp_have_no_findings(self):
        self.assertEqual(check_manifest(MANIFEST, LISTING), [])
        self.assertEqual(check_mcp(MCP, LISTING, frozenset()), [])

    def test_component_keys_that_resolve_are_accepted(self):
        m = with_(MANIFEST, skills=["./skills/", "."], mcpServers="./.mcp.json")
        self.assertEqual(check_manifest(m, LISTING), [])


class Name(unittest.TestCase):
    # Criterion 2
    def test_missing_name_is_error(self):
        m = dict(MANIFEST)
        del m["name"]
        self.assertTrue(errors(check_manifest(m, LISTING)))

    def test_forbidden_characters_are_errors(self):
        for bad in ("my bundle", "a@b", "a:b", "a/b", "a\\b", ""):
            with self.subTest(name=bad):
                self.assertTrue(errors(check_manifest(with_(MANIFEST, name=bad), LISTING)))

    def test_reserved_names_are_errors(self):
        for bad in (
            "claude-lab",
            "anthropic-tools",
            "anthropics-x",
            "cc-plugin-x",
            "claude",
            "Claude_Code",
            "claude-mods",
            "official-claude-tools",
        ):
            with self.subTest(name=bad):
                found = errors(check_manifest(with_(MANIFEST, name=bad), LISTING))
                self.assertTrue(any("reserved" in f.message for f in found), found)

    def test_brand_word_elsewhere_and_non_kebab_are_warnings_only(self):
        for name in ("mcp-for-claude", "Agentlab_Bundle"):
            with self.subTest(name=name):
                found = check_manifest(with_(MANIFEST, name=name), LISTING)
                self.assertEqual(errors(found), [])
                self.assertEqual(len(warnings(found)), 1)


class Paths(unittest.TestCase):
    # Criterion 3
    def test_bad_component_paths_are_errors(self):
        cases = {
            "skills": ["skills", "../x", "./../x", "./nope"],
            "agents": ["./agents/a.md"],
            "commands": ["./commands/"],
            "hooks": ["./hooks/hooks.json"],
            "mcpServers": ["./missing.json", "https://example.com/server.zip"],
        }
        for key, paths in cases.items():
            for path in paths:
                with self.subTest(key=key, path=path):
                    self.assertTrue(errors(check_manifest(with_(MANIFEST, **{key: path}), LISTING)))

    def test_bad_path_inside_an_array_is_caught(self):
        m = with_(MANIFEST, skills=["./skills", "./nope"])
        self.assertEqual(len(errors(check_manifest(m, LISTING))), 1)

    def test_traversal_without_prefix_reports_both_problems(self):
        # Matches `claude plugin validate`: '"./" prefix' and '".." traversal' separately.
        self.assertEqual(len(errors(check_manifest(with_(MANIFEST, skills="../x"), LISTING))), 2)

    def test_command_map_needs_exactly_one_of_source_or_content(self):
        for entry in ({}, {"source": "./skills", "content": "x"}, {"source": "./nope.md"}):
            with self.subTest(entry=entry):
                m = with_(MANIFEST, commands={"c": entry})
                self.assertTrue(errors(check_manifest(m, LISTING)))
        self.assertEqual(check_manifest(with_(MANIFEST, commands={"c": {"content": "x"}}), LISTING), [])


class RecommendedFields(unittest.TestCase):
    # Criterion 4
    def test_missing_metadata_is_warning_and_fails_only_under_strict(self):
        for key in ("version", "description", "author"):
            with self.subTest(missing=key):
                m = dict(MANIFEST)
                del m[key]
                found = check_manifest(m, LISTING)
                self.assertEqual(errors(found), [])
                self.assertEqual(len(warnings(found)), 1)
                self.assertFalse(has_failures(found, strict=False))
                self.assertTrue(has_failures(found, strict=True))

    def test_author_without_name_is_error(self):
        self.assertTrue(errors(check_manifest(with_(MANIFEST, author={"email": "x"}), LISTING)))


class McpConfig(unittest.TestCase):
    # Criterion 5
    def test_plugin_root_reference_to_missing_file_is_error(self):
        mcp = json.loads(json.dumps(MCP))
        mcp["mcpServers"]["hello-world"]["args"] = ["${CLAUDE_PLUGIN_ROOT}/server/missing.py"]
        found = errors(check_mcp(mcp, LISTING, frozenset()))
        self.assertEqual(len(found), 1)
        self.assertIn("server/missing.py", found[0].message)

    def test_plugin_root_reference_that_escapes_is_error(self):
        mcp = json.loads(json.dumps(MCP))
        mcp["mcpServers"]["hello-world"]["args"] = ["${CLAUDE_PLUGIN_ROOT}/../x.py"]
        self.assertTrue(errors(check_mcp(mcp, LISTING, frozenset())))

    def test_inline_manifest_servers_get_the_same_checks(self):
        m = with_(MANIFEST, mcpServers={"x": {"command": "node", "args": ["${CLAUDE_PLUGIN_ROOT}/nope.js"]}})
        self.assertTrue(errors(check_manifest(m, LISTING)))

    def test_bad_shapes_are_errors(self):
        cases = [
            [],
            {"hello-world": MCP["mcpServers"]["hello-world"]},  # missing wrapper
            {"mcpServers": {"s": "python3"}},
            {"mcpServers": {"s": {"type": "stdio"}}},
            {"mcpServers": {"s": {"command": "x", "args": "not-a-list"}}},
            {"mcpServers": {"s": {"type": "http", "url": "not a url"}}},
            {"mcpServers": {"s": {"type": "carrier-pigeon"}}},
        ]
        for mcp in cases:
            with self.subTest(mcp=mcp):
                self.assertTrue(errors(check_mcp(mcp, LISTING, frozenset())))

    # Criterion 6
    def test_undeclared_user_config_reference_is_error(self):
        mcp = json.loads(json.dumps(MCP))
        mcp["mcpServers"]["hello-world"]["env"] = {"T": "${user_config.token}"}
        self.assertTrue(errors(check_mcp(mcp, LISTING, frozenset())))
        self.assertEqual(check_mcp(mcp, LISTING, frozenset({"token"})), [])


class Drift(unittest.TestCase):
    # Criterion 7
    def test_differing_copy_is_error_naming_both_paths(self):
        found = check_drift(b"a", b"b", "copy.md and orig.md")
        self.assertEqual(len(errors(found)), 1)
        self.assertIn("copy.md and orig.md", found[0].message)
        self.assertEqual(check_drift(b"same", b"same", "x"), [])

    def test_real_plugin_passes_strict_with_drift_checks(self):
        argv = [str(HERE), "--strict"]
        for copy, original in DRIFT_PAIRS:
            argv += ["--original", f"{copy}={original}"]
        out = io.StringIO()
        with redirect_stdout(out):
            code = main(argv)
        self.assertEqual(code, 0, out.getvalue())

    def test_drifted_copy_fails_the_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".claude-plugin").mkdir()
            (root / ".claude-plugin/plugin.json").write_text(json.dumps(MANIFEST))
            (root / "SKILL.md").write_text("edited copy\n")
            out = io.StringIO()
            with redirect_stdout(out):
                code = main([str(root), "--original", f"SKILL.md={DRIFT_PAIRS[0][1]}"])
            self.assertEqual(code, 1)
            self.assertIn("drift", out.getvalue())


class MalformedInput(unittest.TestCase):
    # Criterion 8
    def _run(self, root: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(HERE / "check_plugin.py"), str(root)],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_malformed_json_exits_nonzero_without_traceback(self):
        for rel in (".claude-plugin/plugin.json", ".mcp.json"):
            with self.subTest(file=rel), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / ".claude-plugin").mkdir()
                (root / ".claude-plugin/plugin.json").write_text(json.dumps(MANIFEST))
                (root / rel).write_text('{"name": "agentlab-bundle",')
                proc = self._run(root)
                self.assertEqual(proc.returncode, 1)
                self.assertIn("not valid JSON", proc.stderr)
                self.assertNotIn("Traceback", proc.stderr + proc.stdout)

    def test_missing_manifest_and_non_object_json_fail_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = self._run(Path(tmp))
            self.assertEqual(proc.returncode, 1)
            self.assertNotIn("Traceback", proc.stderr)
        self.assertTrue(errors(check_manifest(["not", "an", "object"], LISTING)))


if __name__ == "__main__":
    unittest.main()
