"""Milestone 8, criterion 1: ``check_plugin`` passes the real plugin and names each injected defect.

Every defect test edits a temp copy of ``plugin/`` and asserts the exact list
of problem kinds, so a checker that always returns ``[]`` (or that reports
everything) fails.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from nightloop.plugin_check import Problem, ProblemKind as K, check_plugin
from nightloop.plugin_check.frontmatter import FrontmatterError, parse_frontmatter

from .plugin_support import PLUGIN, copy_plugin

MANIFEST = Path(".claude-plugin/plugin.json")
HOOKS = Path("hooks/hooks.json")
BUILDER = Path("agents/builder.md")
GUARD_ARG = "${CLAUDE_PLUGIN_ROOT}/hooks/publish_guard.py"


def kinds(problems: list[Problem]) -> list[K]:
    return [p.kind for p in problems]


class PluginCopyCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = copy_plugin(Path(self._tmp.name))

    def write(self, rel: Path, text: str) -> None:
        (self.root / rel).write_text(text, encoding="utf-8")

    def manifest(self, **extra: object) -> None:
        self.write(MANIFEST, json.dumps({"name": "nightloop", **extra}))

    def hooks(self, doc: object) -> None:
        self.write(HOOKS, json.dumps(doc))

    def hook_handler(self, handler: dict[str, object]) -> None:
        self.hooks({"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [handler]}]}})

    def add_builder_field(self, line: str) -> None:
        text = (self.root / BUILDER).read_text(encoding="utf-8")
        self.write(BUILDER, text.replace("\nmodel: ", f"\n{line}\nmodel: ", 1))

    def assertKinds(self, expected: list[K]) -> None:
        problems = check_plugin(self.root)
        self.assertEqual(kinds(problems), expected, problems)


class RealPluginTest(unittest.TestCase):
    def test_the_shipped_plugin_has_no_problems(self) -> None:
        self.assertEqual(check_plugin(PLUGIN), [])

    def test_an_untouched_copy_has_no_problems(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(check_plugin(copy_plugin(Path(tmp))), [])

    def test_a_root_that_is_not_a_directory_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(NotADirectoryError):
                check_plugin(Path(tmp) / "absent")


class ManifestDefectTest(PluginCopyCase):
    def test_missing_manifest(self) -> None:
        (self.root / MANIFEST).unlink()
        self.assertKinds([K.MANIFEST_MISSING])

    def test_invalid_json(self) -> None:
        self.write(MANIFEST, '{"name": "nightloop",')
        self.assertKinds([K.MANIFEST_INVALID_JSON])

    def test_json_that_is_not_an_object(self) -> None:
        self.write(MANIFEST, '["nightloop"]')
        self.assertKinds([K.MANIFEST_INVALID_JSON])

    def test_not_utf8(self) -> None:
        (self.root / MANIFEST).write_bytes(b'{"name": "night\xffloop"}')
        self.assertKinds([K.MANIFEST_INVALID_JSON])

    def test_no_name(self) -> None:
        for doc in ({"version": "0.1.0"}, {"name": ""}, {"name": 7}):
            with self.subTest(doc=doc):
                self.write(MANIFEST, json.dumps(doc))
                self.assertKinds([K.MANIFEST_NO_NAME])

    def test_name_not_kebab_case(self) -> None:
        for name in ("Night_Loop", "nightLoop", "night--loop", "-nightloop", "nightloop-"):
            with self.subTest(name=name):
                self.manifest(name=name)
                self.assertKinds([K.MANIFEST_BAD_NAME])

    def test_reserved_prefix(self) -> None:
        for name in ("claude-nightloop", "anthropic-nightloop"):
            with self.subTest(name=name):
                self.manifest(name=name)
                self.assertKinds([K.MANIFEST_RESERVED_PREFIX])

    def test_agents_key_set(self) -> None:
        self.manifest(agents="./agents/")
        self.assertKinds([K.MANIFEST_AGENTS_SET])

    def test_path_not_starting_dot_slash(self) -> None:
        self.manifest(skills="agents")
        self.assertKinds([K.MANIFEST_PATH_NOT_RELATIVE])

    def test_path_with_dot_dot(self) -> None:
        self.manifest(commands="./agents/../../outside")
        self.assertKinds([K.MANIFEST_PATH_ESCAPES])

    def test_missing_path(self) -> None:
        self.manifest(commands=["./agents", "./commands"])
        self.assertKinds([K.MANIFEST_PATH_MISSING])

    def test_path_of_the_wrong_type(self) -> None:
        for value in (3, ["./agents", 4], None):
            with self.subTest(value=value):
                self.manifest(outputStyles=value)
                self.assertKinds([K.MANIFEST_PATH_WRONG_TYPE])

    def test_existing_paths_and_an_inline_object_pass(self) -> None:
        self.manifest(commands="./bin/", hooks={"PreToolUse": []}, outputStyles=["./"])
        self.assertKinds([])


class AgentDefectTest(PluginCopyCase):
    def test_each_ignored_field(self) -> None:
        cases = {
            "permissionMode": "permissionMode: dontAsk",
            "hooks": "hooks:\n  PreToolUse:\n    - matcher: Bash",
            "mcpServers": "mcpServers:\n  - github",
            "initialPrompt": "initialPrompt: start now",
        }
        original = (self.root / BUILDER).read_text(encoding="utf-8")
        for field, line in cases.items():
            with self.subTest(field=field):
                self.write(BUILDER, original)
                self.add_builder_field(line)
                problems = check_plugin(self.root)
                self.assertEqual(kinds(problems), [K.AGENT_IGNORED_FIELD], problems)
                self.assertIn(field, problems[0].detail)
                self.assertEqual(problems[0].where, BUILDER.as_posix())

    def test_unknown_field(self) -> None:
        self.add_builder_field("allowed-tools: Read")
        self.assertKinds([K.AGENT_UNKNOWN_FIELD])

    def test_missing_required_field(self) -> None:
        text = (self.root / BUILDER).read_text(encoding="utf-8")
        lines = [line for line in text.split("\n") if not line.startswith("description:")]
        self.write(BUILDER, "\n".join(lines))
        self.assertKinds([K.AGENT_MISSING_FIELD])

    def test_bad_frontmatter(self) -> None:
        cases = {
            "none": "You are the builder.\n",
            "unclosed": "---\nname: builder\ndescription: x\n",
            "not key: value": "---\nname: builder\njust words\n---\n",
        }
        for label, text in cases.items():
            with self.subTest(case=label):
                self.write(BUILDER, text)
                self.assertKinds([K.AGENT_FRONTMATTER_INVALID])

    def test_not_utf8(self) -> None:
        (self.root / BUILDER).write_bytes(b"---\nname: b\xffuilder\n---\n")
        self.assertKinds([K.AGENT_FRONTMATTER_INVALID])


class HooksDefectTest(PluginCopyCase):
    def test_no_hooks_wrapper(self) -> None:
        handler = {"type": "command", "command": "python3", "args": [GUARD_ARG]}
        self.hooks({"PreToolUse": [{"matcher": "Bash", "hooks": [handler]}]})
        self.assertKinds([K.HOOKS_NO_WRAPPER])

    def test_invalid_json(self) -> None:
        self.write(HOOKS, "{ hooks: }")
        self.assertKinds([K.HOOKS_INVALID_JSON])

    def test_malformed_groups(self) -> None:
        for event_value in ({"matcher": "Bash"}, [{"matcher": "Bash"}], [{"hooks": ["python3"]}]):
            with self.subTest(value=event_value):
                self.hooks({"hooks": {"PreToolUse": event_value}})
                self.assertKinds([K.HOOKS_MALFORMED])

    def test_missing_hook_script(self) -> None:
        (self.root / "hooks" / "publish_guard.py").unlink()
        problems = check_plugin(self.root)
        self.assertEqual(kinds(problems), [K.HOOK_SCRIPT_MISSING], problems)
        self.assertIn("hooks/publish_guard.py", problems[0].detail)

    def test_script_path_through_dot_dot(self) -> None:
        self.hook_handler({"type": "command", "command": "python3",
                           "args": ["${CLAUDE_PLUGIN_ROOT}/hooks/../hooks/publish_guard.py"]})
        self.assertKinds([K.HOOK_SCRIPT_MISSING])

    def test_shell_form_command(self) -> None:
        self.hook_handler({"type": "command", "command": 'python3 "${CLAUDE_PLUGIN_ROOT}/hooks/nope.py"'})
        self.assertKinds([K.HOOK_SCRIPT_MISSING])
        self.hook_handler({"type": "command", "command": f'python3 "{GUARD_ARG}"'})
        self.assertKinds([])


class BinDefectTest(PluginCopyCase):
    def test_bin_entry_not_executable(self) -> None:
        (self.root / "bin" / "run_silent").chmod(0o644)
        problems = check_plugin(self.root)
        self.assertEqual(kinds(problems), [K.BIN_NOT_EXECUTABLE], problems)
        self.assertEqual(problems[0].where, "bin/run_silent")


class SeveralDefectsTest(PluginCopyCase):
    def test_every_defect_is_reported_not_only_the_first(self) -> None:
        self.manifest(name="Night_Loop", agents="./agents")
        (self.root / "bin" / "run_silent").chmod(0o600)
        self.add_builder_field("permissionMode: dontAsk")
        self.assertKinds(
            [K.MANIFEST_BAD_NAME, K.MANIFEST_AGENTS_SET, K.AGENT_IGNORED_FIELD, K.BIN_NOT_EXECUTABLE]
        )


class FrontmatterTest(unittest.TestCase):
    def test_reads_top_level_keys_and_skips_nested_lines(self) -> None:
        text = "---\nname: b\n# note\nhooks:\n  PreToolUse: []\n- x\n\ntools: Read, Bash\n---\nbody: not a key\n"
        self.assertEqual(parse_frontmatter(text), {"name": "b", "hooks": "", "tools": "Read, Bash"})

    def test_crlf_lines(self) -> None:
        self.assertEqual(parse_frontmatter("---\r\nname: b\r\n---\r\n"), {"name": "b"})

    def test_rejections(self) -> None:
        for text in ("", "name: b\n", "---\nname: b\nname: c\n---\n", "---\n  indented: first\n---\n"):
            with self.subTest(text=text):
                with self.assertRaises(FrontmatterError):
                    parse_frontmatter(text)


if __name__ == "__main__":
    unittest.main()
