"""Acceptance tests for milestone 1: nightloop.toml schema and loader.

Numbered comments refer to the acceptance criteria in
research/2026-11-08-nightloop-config-schema.md. Run from projects/nightloop/:

    python3 -I -m unittest discover -v
"""

from __future__ import annotations

import copy
import dataclasses
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from nightloop.config import (
    Config,
    ConfigError,
    Phase,
    Postcondition,
    Window,
    load_config,
    parse_config,
)

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "agentlab.nightloop.toml"


def base_doc() -> dict[str, Any]:
    """A valid document with agentlab's real values, as a mutable dict."""
    return {
        "schema_version": 1,
        "repo": {"backlog": "BACKLOG.md"},
        "run": {"cycles": 1, "window_start_hour": 1, "window_end_hour": 6},
        "phase": {
            "research": {
                "agent": "agentlab-researcher",
                "model": "sonnet",
                "postcondition": "research_note_fresh",
                "allowed_tools": ["Read", "Write", "Bash(git log *)", "mcp__hn-search__*"],
            },
            "build": {
                "agent": "agentlab-builder",
                "model": "opus",
                "postcondition": "increment_built",
                "allowed_tools": ["Read", "Write", "Edit", "Bash(python3 *)"],
            },
            "review": {
                "agent": "agentlab-reviewer",
                "model": "opus",
                "postcondition": "review_verdict",
                "allowed_tools": ["Read", "Grep", "Bash(git diff:*)"],
            },
        },
    }


def dump(doc: dict[str, Any]) -> str:
    """Serialise the small TOML subset these tests use (tables, str/int/float/bool/arrays)."""
    lines: list[str] = []

    def value(v: Any) -> str:
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return repr(v)
        if isinstance(v, str):
            return json.dumps(v)  # JSON string escapes are valid TOML basic strings
        if isinstance(v, list):
            return "[" + ", ".join(value(x) for x in v) + "]"
        raise TypeError(f"test serialiser cannot emit {type(v).__name__}")

    def table(path: str, t: dict[str, Any]) -> None:
        if path:
            lines.append(f"[{path}]")
        for k, v in t.items():
            if not isinstance(v, dict):
                lines.append(f"{k} = {value(v)}")
        for k, v in t.items():
            if isinstance(v, dict):
                table(f"{path}.{k}" if path else k, v)

    table("", doc)
    return "\n".join(lines) + "\n"


def with_change(path: str, new: Any = None, *, delete: bool = False) -> str:
    """Return base_doc() serialised with the dotted ``path`` set to ``new`` or deleted."""
    doc = copy.deepcopy(base_doc())
    *parents, last = path.split(".")
    node = doc
    for key in parents:
        node = node[key]
    if delete:
        del node[last]
    else:
        node[last] = new
    return dump(doc)


class ConfigErrorAssertions(unittest.TestCase):
    def assertFieldError(self, text: str, field: str) -> ConfigError:
        with self.assertRaises(ConfigError) as ctx:
            parse_config(text)
        self.assertEqual(ctx.exception.field, field, str(ctx.exception))
        return ctx.exception


class ValidConfigTest(ConfigErrorAssertions):
    # 1. A fully valid file loads and the fields round-trip.
    def test_valid_document_round_trips(self) -> None:
        config = parse_config(dump(base_doc()))
        self.assertEqual(config.backlog, "BACKLOG.md")
        self.assertEqual(config.cycles, 1)
        self.assertEqual(config.window, Window(start_hour=1, end_hour=6))
        self.assertEqual(config.research.agent, "agentlab-researcher")
        self.assertEqual(config.research.model, "sonnet")
        self.assertIs(config.research.postcondition, Postcondition.RESEARCH_NOTE_FRESH)
        self.assertEqual(
            config.research.allowed_tools,
            ("Read", "Write", "Bash(git log *)", "mcp__hn-search__*"),
        )
        self.assertFalse(config.research.allow_unrestricted_bash)
        self.assertIs(config.build.postcondition, Postcondition.INCREMENT_BUILT)
        self.assertIs(config.review.postcondition, Postcondition.REVIEW_VERDICT)
        self.assertEqual(config.review.model, "opus")

    def test_shipped_agentlab_example_loads(self) -> None:
        config = load_config(EXAMPLE)
        self.assertEqual((config.cycles, config.window.start_hour, config.window.end_hour), (1, 1, 6))
        self.assertEqual(
            (config.research.agent, config.build.agent, config.review.agent),
            ("agentlab-researcher", "agentlab-builder", "agentlab-reviewer"),
        )
        self.assertEqual((config.research.model, config.build.model), ("sonnet", "opus"))
        self.assertIn("mcp__hn-search__get_story", config.research.allowed_tools)
        self.assertTrue(config.build.allow_unrestricted_bash)

    def test_parsing_is_deterministic(self) -> None:
        text = dump(base_doc())
        self.assertEqual(parse_config(text), parse_config(text))

    def test_error_string_is_field_colon_message(self) -> None:
        err = self.assertFieldError(with_change("run.cycles", 0), "run.cycles")
        self.assertTrue(str(err).startswith("run.cycles: "), str(err))
        self.assertIsInstance(err, ValueError)


class RequiredAndUnknownKeysTest(ConfigErrorAssertions):
    # 2. Deleting each required key names that key's dotted path.
    def test_each_required_key_is_reported_when_missing(self) -> None:
        required = [
            "schema_version", "repo", "repo.backlog", "run", "run.cycles",
            "run.window_start_hour", "run.window_end_hour", "phase",
        ]
        for phase in ("research", "build", "review"):
            required.append(f"phase.{phase}")
            for key in ("agent", "model", "postcondition", "allowed_tools"):
                required.append(f"phase.{phase}.{key}")
        for path in required:
            with self.subTest(path=path):
                self.assertFieldError(with_change(path, delete=True), path)

    # 3. Unknown keys at every level are errors naming the key.
    def test_unknown_keys_are_reported(self) -> None:
        for path in ("extra", "repo.root", "run.cylces", "phase.build.timeout"):
            with self.subTest(path=path):
                self.assertFieldError(with_change(path, 1), path)

    def test_typo_names_the_typo_not_the_missing_key(self) -> None:
        doc = base_doc()
        doc["run"]["cylces"] = doc["run"].pop("cycles")
        self.assertFieldError(dump(doc), "run.cylces")

    def test_allow_unrestricted_bash_is_optional_and_defaults_false(self) -> None:
        config = parse_config(dump(base_doc()))
        self.assertFalse(config.build.allow_unrestricted_bash)

    def test_tables_must_be_tables(self) -> None:
        self.assertFieldError(with_change("run", 3), "run")
        self.assertFieldError(with_change("phase.build", "x"), "phase.build")

    def test_schema_version_must_be_exactly_1(self) -> None:
        for bad in (2, 0, True, "1"):
            with self.subTest(value=bad):
                self.assertFieldError(with_change("schema_version", bad), "schema_version")


class RunTest(ConfigErrorAssertions):
    # 4. cycles must be an int >= 1, never a bool, string or float.
    def test_bad_cycles(self) -> None:
        for bad in (0, -1, True, "2", 1.0):
            with self.subTest(value=bad):
                self.assertFieldError(with_change("run.cycles", bad), "run.cycles")

    def test_large_cycles_is_valid(self) -> None:
        self.assertEqual(parse_config(with_change("run.cycles", 12)).cycles, 12)

    # 5. Window: start 0..23, end 1..24, end exclusive and > start.
    def test_bad_windows_name_the_right_field(self) -> None:
        cases = [
            ({"window_start_hour": 6, "window_end_hour": 6}, "run.window_end_hour"),
            ({"window_start_hour": 7, "window_end_hour": 6}, "run.window_end_hour"),
            ({"window_start_hour": 24, "window_end_hour": 6}, "run.window_start_hour"),
            ({"window_start_hour": -1, "window_end_hour": 6}, "run.window_start_hour"),
            ({"window_start_hour": 0, "window_end_hour": 0}, "run.window_end_hour"),
            ({"window_start_hour": 1, "window_end_hour": 25}, "run.window_end_hour"),
            ({"window_start_hour": False, "window_end_hour": 6}, "run.window_start_hour"),
            ({"window_start_hour": 1, "window_end_hour": 6.0}, "run.window_end_hour"),
        ]
        for run, field in cases:
            with self.subTest(run=run):
                doc = base_doc()
                doc["run"].update(run)
                self.assertFieldError(dump(doc), field)

    def test_end_hour_24_is_valid(self) -> None:
        self.assertEqual(parse_config(with_change("run.window_end_hour", 24)).window.end_hour, 24)

    def test_start_hour_0_is_valid(self) -> None:
        self.assertEqual(parse_config(with_change("run.window_start_hour", 0)).window.start_hour, 0)


class RepoTest(ConfigErrorAssertions):
    def test_bad_backlog_paths(self) -> None:
        for bad in ("", "/etc/BACKLOG.md", "../BACKLOG.md", "docs/../../x.md", "~/BACKLOG.md",
                    ".", "docs\\BACKLOG.md", 7):
            with self.subTest(value=bad):
                self.assertFieldError(with_change("repo.backlog", bad), "repo.backlog")

    def test_nested_relative_backlog_is_valid(self) -> None:
        self.assertEqual(parse_config(with_change("repo.backlog", "docs/BACKLOG.md")).backlog,
                         "docs/BACKLOG.md")


class PhaseTest(ConfigErrorAssertions):
    # 6. Exactly research, build, review.
    def test_missing_phase_table(self) -> None:
        self.assertFieldError(with_change("phase.review", delete=True), "phase.review")

    def test_extra_phase_is_rejected(self) -> None:
        doc = base_doc()
        doc["phase"]["deploy"] = copy.deepcopy(doc["phase"]["build"])
        self.assertFieldError(dump(doc), "phase.deploy")

    # 7. Postcondition must be in the phase's allowed subset.
    def test_review_must_be_gated(self) -> None:
        self.assertFieldError(with_change("phase.review.postcondition", "none"),
                              "phase.review.postcondition")

    def test_build_cannot_use_review_verdict(self) -> None:
        self.assertFieldError(with_change("phase.build.postcondition", "review_verdict"),
                              "phase.build.postcondition")

    def test_research_and_build_may_be_ungated(self) -> None:
        doc = base_doc()
        doc["phase"]["research"]["postcondition"] = "none"
        doc["phase"]["build"]["postcondition"] = "none"
        config = parse_config(dump(doc))
        self.assertIs(config.research.postcondition, Postcondition.NONE)
        self.assertIs(config.build.postcondition, Postcondition.NONE)

    def test_unknown_or_non_string_postcondition(self) -> None:
        for bad in ("artifact_fresh", 1, ""):
            with self.subTest(value=bad):
                self.assertFieldError(with_change("phase.research.postcondition", bad),
                                      "phase.research.postcondition")

    def test_bad_agent_names(self) -> None:
        for bad in ("", "Agentlab-Builder", "-builder", "agent_builder", "a b", 3):
            with self.subTest(value=bad):
                self.assertFieldError(with_change("phase.build.agent", bad), "phase.build.agent")

    def test_bad_models(self) -> None:
        for bad in ("", "claude opus", 4):
            with self.subTest(value=bad):
                self.assertFieldError(with_change("phase.build.model", bad), "phase.build.model")

    def test_full_model_id_is_accepted(self) -> None:
        config = parse_config(with_change("phase.build.model", "claude-opus-4-5-20251101"))
        self.assertEqual(config.build.model, "claude-opus-4-5-20251101")

    def test_allow_unrestricted_bash_must_be_bool(self) -> None:
        for bad in (1, "true"):
            with self.subTest(value=bad):
                self.assertFieldError(with_change("phase.build.allow_unrestricted_bash", bad),
                                      "phase.build.allow_unrestricted_bash")


class AllowedToolsTest(ConfigErrorAssertions):
    """8. Each allowlist rule has reject and accept cases, reported by index."""

    FIELD = "phase.build.allowed_tools"

    def tools_text(self, tools: Any, *, unrestricted: bool | None = None) -> str:
        doc = base_doc()
        doc["phase"]["build"]["allowed_tools"] = tools
        if unrestricted is not None:
            doc["phase"]["build"]["allow_unrestricted_bash"] = unrestricted
        return dump(doc)

    def test_rejected_rules_name_their_index(self) -> None:
        rejected = {
            "unanchored glob": "*",
            "glob in tool name": "B*",
            "glob in tool name suffix": "Web*",
            "unanchored mcp glob": "mcp__*",
            "mcp with no server": "mcp__",
            "mcp glob in server": "mcp__hn*__get",
            "mcp with parens": "mcp__x__tool(arg)",
            "mcp trailing separator": "mcp__hn-search__",
            "bare Bash": "Bash",
            "Bash(*)": "Bash(*)",
            "Bash(:*)": "Bash(:*)",
            "star before subcommand": "Bash(git * main)",
            "star before subcommand with suffix": "Bash(git * main:*)",
            "star in command position": "Bash(* --version)",
            "glob in command word": "Bash(g* log)",
            "colon-star not at end": "Bash(git:* log)",
            "empty": "",
            "leading space": " Read",
            "unclosed paren": "Read(./src",
            "empty specifier": "Bash()",
            "double space": "Bash(git  log)",
            "space in tool name": "Bash (git log)",
        }
        for label, rule in rejected.items():
            with self.subTest(label, rule=rule):
                self.assertFieldError(self.tools_text(["Read", "Edit", rule]), f"{self.FIELD}[2]")

    def test_accepted_rules(self) -> None:
        accepted = [
            "mcp__hn-search__*",
            "mcp__hn-search",
            "mcp__hn-search__get_story",
            "mcp__hn-search__get_*",
            "Bash(git log *)",
            "Bash(git log:*)",
            "Bash(git *)",
            "Bash(ls *)",
            "Bash(npm run test)",
            "Bash(git log * --oneline)",
            "Read(./src/**)",
            "WebFetch(domain:example.com)",
        ]
        config = parse_config(self.tools_text(accepted))
        self.assertEqual(config.build.allowed_tools, tuple(accepted))

    def test_unrestricted_bash_is_accepted_with_opt_in(self) -> None:
        for rule in ("Bash", "Bash(*)", "Bash(:*)"):
            with self.subTest(rule=rule):
                config = parse_config(self.tools_text(["Read", rule], unrestricted=True))
                self.assertIn(rule, config.build.allowed_tools)
                self.assertTrue(config.build.allow_unrestricted_bash)

    def test_opt_in_does_not_admit_other_bad_bash_rules(self) -> None:
        self.assertFieldError(self.tools_text(["Bash(git * main)"], unrestricted=True),
                              f"{self.FIELD}[0]")

    def test_explicit_false_still_rejects_bare_bash(self) -> None:
        self.assertFieldError(self.tools_text(["Bash"], unrestricted=False), f"{self.FIELD}[0]")

    def test_duplicate_rule_reports_the_repeat(self) -> None:
        self.assertFieldError(self.tools_text(["Read", "Edit", "Read"]), f"{self.FIELD}[2]")

    def test_list_shape(self) -> None:
        self.assertFieldError(self.tools_text([]), self.FIELD)
        self.assertFieldError(self.tools_text("Read"), self.FIELD)
        self.assertFieldError(self.tools_text(["Read", 1]), f"{self.FIELD}[1]")
        self.assertFieldError(self.tools_text(["Read", ["Edit"]]), f"{self.FIELD}[1]")

    def test_rule_message_quotes_the_rule(self) -> None:
        err = self.assertFieldError(self.tools_text(["mcp__*"]), f"{self.FIELD}[0]")
        self.assertIn("'mcp__*'", err.message)


class DocumentAndFileTest(ConfigErrorAssertions):
    # 9. Bad TOML, missing file and oversize file all raise ConfigError.
    def test_bad_toml_is_a_config_error(self) -> None:
        self.assertFieldError("schema_version = 1\n[run]\ncycles = \n", "<toml>")

    def test_deep_nesting_is_a_config_error_not_recursion_error(self) -> None:
        text = "a = " + "[" * 30000 + "]" * 30000
        self.assertLess(len(text.encode()), 64 * 1024)
        self.assertFieldError(text, "<toml>")

    def test_every_truncation_raises_only_config_error(self) -> None:
        text = dump(base_doc())
        for end in range(len(text)):
            try:
                parse_config(text[:end])
            except ConfigError:
                pass

    def test_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ConfigError) as ctx:
                load_config(Path(tmp) / "nightloop.toml")
        self.assertEqual(ctx.exception.field, "<file>")

    def test_directory_instead_of_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ConfigError) as ctx:
                load_config(Path(tmp))
        self.assertEqual(ctx.exception.field, "<file>")

    def test_oversize_file(self) -> None:
        # Valid TOML padded with a comment, so only the size can be the reason.
        text = dump(base_doc()) + "#" + "x" * (65 * 1024) + "\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nightloop.toml"
            path.write_text(text, encoding="utf-8")
            with self.assertRaises(ConfigError) as ctx:
                load_config(path)
        self.assertEqual(ctx.exception.field, "<file>")

    def test_file_at_exactly_the_limit_loads(self) -> None:
        body = dump(base_doc())
        padding = 64 * 1024 - len(body.encode()) - 2
        text = body + "#" + "x" * padding + "\n"
        self.assertEqual(len(text.encode()), 64 * 1024)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nightloop.toml"
            path.write_text(text, encoding="utf-8")
            self.assertEqual(load_config(path).cycles, 1)

    def test_non_utf8_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nightloop.toml"
            path.write_bytes(b"schema_version = 1\n# \xff\xfe\n")
            with self.assertRaises(ConfigError) as ctx:
                load_config(path)
        self.assertEqual(ctx.exception.field, "<toml>")

    def test_load_is_idempotent(self) -> None:
        self.assertEqual(load_config(EXAMPLE), load_config(EXAMPLE))

    # 10. Bytes input is a ConfigError, not TypeError.
    def test_bytes_input_is_a_config_error(self) -> None:
        self.assertFieldError(dump(base_doc()).encode(), "<toml>")  # type: ignore[arg-type]


class ImmutabilityAndInvariantsTest(unittest.TestCase):
    # 10. A loaded config cannot be mutated.
    def test_loaded_config_is_frozen(self) -> None:
        config = parse_config(dump(base_doc()))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            config.cycles = 5  # type: ignore[misc]
        with self.assertRaises(dataclasses.FrozenInstanceError):
            config.window.end_hour = 23  # type: ignore[misc]
        with self.assertRaises(dataclasses.FrozenInstanceError):
            config.build.model = "haiku"  # type: ignore[misc]
        self.assertIsInstance(config.build.allowed_tools, tuple)

    # Invalid values cannot be represented even when built directly.
    def test_direct_construction_enforces_invariants(self) -> None:
        with self.assertRaises(ConfigError) as ctx:
            Window(start_hour=5, end_hour=2)
        self.assertEqual(ctx.exception.field, "end_hour")

        with self.assertRaises(ConfigError) as ctx:
            Phase(agent="a", model="m", postcondition=Postcondition.NONE,
                  allowed_tools=["Read"])  # type: ignore[arg-type]
        self.assertEqual(ctx.exception.field, "allowed_tools")

        with self.assertRaises(ConfigError) as ctx:
            Phase(agent="a", model="m", postcondition="none",  # type: ignore[arg-type]
                  allowed_tools=("Read",))
        self.assertEqual(ctx.exception.field, "postcondition")

        ok = Phase(agent="a", model="m", postcondition=Postcondition.NONE, allowed_tools=("Read",))
        gated = dataclasses.replace(ok, postcondition=Postcondition.REVIEW_VERDICT)
        with self.assertRaises(ConfigError) as ctx:
            Config(backlog="B.md", cycles=1, window=Window(1, 6),
                   research=ok, build=ok, review=ok)
        self.assertEqual(ctx.exception.field, "review.postcondition")
        Config(backlog="B.md", cycles=1, window=Window(1, 6), research=ok, build=ok, review=gated)

        with self.assertRaises(ConfigError):
            dataclasses.replace(ok, allowed_tools=("Bash",))


if __name__ == "__main__":
    unittest.main()
