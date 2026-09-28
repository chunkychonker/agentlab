"""Offline tests for frontmatter.parse_skill_md. No key, no network, stdlib only.

Each upload constraint gets its own case, and each case asserts on the
specific rule named in the error, so a test can't pass by tripping a
different check.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from frontmatter import SkillMd, SkillValidationError, parse_skill_md

FIXTURE_SKILL_MD = Path(__file__).resolve().parent / "skills" / "hello-skill" / "SKILL.md"


def _md(name: str = "hello-skill", description: str = "Greets people.", body: str = "Body.") -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n{body}\n"


class AcceptsValid(unittest.TestCase):
    def test_real_fixture_parses(self) -> None:
        skill = parse_skill_md(FIXTURE_SKILL_MD.read_text(encoding="utf-8"))
        self.assertEqual(skill.name, "hello-skill")
        self.assertTrue(skill.description.startswith("Writes a greeting"))
        self.assertIn("# House-style greeting", skill.body)

    def test_quoted_values_are_unquoted(self) -> None:
        skill = parse_skill_md('---\nname: "a-b"\ndescription: \'Does: things.\'\n---\n')
        self.assertEqual((skill.name, skill.description), ("a-b", "Does: things."))

    def test_boundary_lengths_accepted(self) -> None:
        parse_skill_md(_md(name="a" * 64, description="d" * 1024))


class _RejectsCase(unittest.TestCase):
    def assertRejects(self, text: str, fragment: str) -> None:
        with self.assertRaises(SkillValidationError) as ctx:
            parse_skill_md(text)
        self.assertIn(fragment, str(ctx.exception))


class RejectsName(_RejectsCase):
    def test_uppercase(self) -> None:
        self.assertRejects(_md(name="Hello-Skill"), "only lowercase letters, digits, and hyphens")

    def test_xml_tag_in_name(self) -> None:
        self.assertRejects(_md(name="<b>hi</b>"), "only lowercase letters, digits, and hyphens")

    def test_too_long(self) -> None:
        self.assertRejects(_md(name="a" * 65), "max is 64")

    def test_contains_claude(self) -> None:
        self.assertRejects(_md(name="my-claude-helper"), "reserved word 'claude'")

    def test_contains_anthropic(self) -> None:
        self.assertRejects(_md(name="anthropic-tools"), "reserved word 'anthropic'")

    def test_empty(self) -> None:
        self.assertRejects(_md(name=""), "name must be non-empty")

    def test_missing(self) -> None:
        self.assertRejects("---\ndescription: x\n---\n", "missing required field 'name'")


class RejectsDescription(_RejectsCase):
    def test_empty(self) -> None:
        self.assertRejects(_md(description="   "), "description must be non-empty")

    def test_too_long(self) -> None:
        self.assertRejects(_md(description="d" * 1025), "max is 1024")

    def test_xml_tag(self) -> None:
        self.assertRejects(_md(description="Use for <task>greetings</task>."), "must not contain XML tags")

    def test_missing(self) -> None:
        self.assertRejects("---\nname: ok\n---\n", "missing required field 'description'")


class RejectsStructure(_RejectsCase):
    def test_no_opening_fence(self) -> None:
        self.assertRejects("name: x\n", "must start with a '---'")

    def test_no_closing_fence(self) -> None:
        self.assertRejects("---\nname: x\ndescription: y\n", "no closing '---'")

    def test_block_scalar_is_loud_not_guessed(self) -> None:
        self.assertRejects("---\nname: x\ndescription: >\n  folded\n---\n", "unsupported by this validator")

    def test_duplicate_key(self) -> None:
        self.assertRejects("---\nname: a\nname: b\ndescription: y\n---\n", "duplicate key 'name'")


class IllegalStateUnrepresentable(unittest.TestCase):
    def test_direct_construction_validates_too(self) -> None:
        with self.assertRaises(SkillValidationError):
            SkillMd(name="Bad Name", description="ok", body="")


if __name__ == "__main__":
    unittest.main(verbosity=2)
