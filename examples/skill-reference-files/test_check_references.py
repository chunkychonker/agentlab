"""Offline self-test for check_references.py.

Stdlib-only `unittest`, no network, no API key — same convention as
`skill-script-execution/test_scan_dependencies.py`. Each test builds a
fixture skill directory under `tempfile.TemporaryDirectory()` and asserts
the *specific* Finding kind for one documented rule, not just "some finding
appeared".

Run:
    cd examples/skill-reference-files
    python3 test_check_references.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

import check_references as cr

_HERE = Path(__file__).resolve().parent
_SHIPPED_SKILL = _HERE / "skills" / "looking-up-http-status-codes"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _findings_of_kind(findings: list[cr.Finding], kind: str) -> list[cr.Finding]:
    return [f for f in findings if f.kind == kind]


class ShippedFixtureTests(unittest.TestCase):
    """The fixture this example ships is designed to satisfy every rule."""

    def test_shipped_skill_has_zero_findings(self) -> None:
        findings = cr.check_skill(_SHIPPED_SKILL)
        self.assertEqual(findings, [], f"expected zero findings, got: {findings}")


class WellFormedSkillTests(unittest.TestCase):
    """A minimal, independently-constructed well-formed skill also passes clean."""

    def test_well_formed_skill_has_zero_findings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root / "SKILL.md",
                "---\nname: demo\ndescription: demo skill\n---\n\n"
                "See [reference/topic.md](reference/topic.md) for detail.\n",
            )
            _write(root / "reference" / "topic.md", "# Topic\n\nShort body.\n")
            findings = cr.check_skill(root)
            self.assertEqual(findings, [])


class BrokenLinkTests(unittest.TestCase):
    def test_link_to_nonexistent_file_is_broken_link_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root / "SKILL.md",
                "---\nname: demo\ndescription: demo\n---\n\n"
                "See [reference/missing.md](reference/missing.md).\n",
            )
            findings = cr.check_skill(root)
            hits = _findings_of_kind(findings, "broken-link")
            self.assertEqual(len(hits), 1, findings)
            self.assertEqual(hits[0].level, "error")


class NestedReferenceTests(unittest.TestCase):
    def test_reference_file_linking_to_second_md_is_nested_reference_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root / "SKILL.md",
                "---\nname: demo\ndescription: demo\n---\n\n"
                "See [reference/a.md](reference/a.md).\n",
            )
            _write(root / "reference" / "a.md", "# A\n\nSee [reference/b.md](reference/b.md).\n")
            _write(root / "reference" / "b.md", "# B\n\nUnrelated leaf content.\n")
            findings = cr.check_skill(root)
            hits = _findings_of_kind(findings, "nested-reference")
            self.assertEqual(len(hits), 1, findings)
            self.assertEqual(hits[0].level, "error")
            self.assertEqual(hits[0].file, "reference/a.md")

    def test_skill_md_mentioning_its_own_filename_is_not_a_nested_reference(self) -> None:
        """Regression: extract_local_references' bare-mention glob used to
        include SKILL.md itself, so any SKILL.md that names itself in prose
        (or a reference file saying "see SKILL.md") got treated as a
        reference *to* SKILL.md, which cascaded into false nested-reference
        errors on every genuine link out of it."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root / "SKILL.md",
                "---\nname: demo\ndescription: demo\n---\n\n"
                "This SKILL.md stays short; see [reference/topic.md](reference/topic.md) "
                "for detail.\n",
            )
            _write(
                root / "reference" / "topic.md",
                "# Topic\n\nSome detail here. See SKILL.md for the summary.\n",
            )
            findings = cr.check_skill(root)
            self.assertEqual(findings, [], f"expected zero findings, got: {findings}")


class TableOfContentsTests(unittest.TestCase):
    def _long_body(self, heading: str | None) -> str:
        lines = []
        if heading is not None:
            lines.append(heading)
        lines.append("# Long Reference")
        lines += [f"line {i}" for i in range(150)]
        return "\n".join(lines) + "\n"

    def test_long_file_with_no_toc_heading_is_missing_toc_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root / "SKILL.md",
                "---\nname: demo\ndescription: demo\n---\n\n"
                "See [reference/long.md](reference/long.md).\n",
            )
            _write(root / "reference" / "long.md", self._long_body(heading=None))
            findings = cr.check_skill(root)
            hits = _findings_of_kind(findings, "missing-toc")
            self.assertEqual(len(hits), 1, findings)
            self.assertEqual(hits[0].level, "warning")

    def test_long_file_with_contents_heading_in_first_15_lines_has_no_missing_toc(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root / "SKILL.md",
                "---\nname: demo\ndescription: demo\n---\n\n"
                "See [reference/long.md](reference/long.md).\n",
            )
            _write(root / "reference" / "long.md", self._long_body(heading="## Contents"))
            findings = cr.check_skill(root)
            hits = _findings_of_kind(findings, "missing-toc")
            self.assertEqual(hits, [], findings)


class WindowsPathTests(unittest.TestCase):
    def test_backslash_link_path_is_windows_path_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root / "SKILL.md",
                "---\nname: demo\ndescription: demo\n---\n\n"
                "See [reference\\\\topic.md](reference\\\\topic.md).\n",
            )
            findings = cr.check_skill(root)
            hits = _findings_of_kind(findings, "windows-path")
            self.assertEqual(len(hits), 1, findings)
            self.assertEqual(hits[0].level, "error")


class GenericFilenameTests(unittest.TestCase):
    def test_doc2_md_bundled_file_is_generic_filename_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "SKILL.md", "---\nname: demo\ndescription: demo\n---\n\nbody\n")
            _write(root / "reference" / "doc2.md", "# Doc 2\n\nbody\n")
            findings = cr.check_skill(root)
            hits = _findings_of_kind(findings, "generic-filename")
            self.assertEqual(len(hits), 1, findings)
            self.assertEqual(hits[0].level, "warning")
            self.assertEqual(hits[0].file, "reference/doc2.md")


class PathEscapeTests(unittest.TestCase):
    """The checker must report an escaping link and never open/read it."""

    def test_dotdot_escape_is_error_and_target_is_never_read(self) -> None:
        with tempfile.TemporaryDirectory() as outside_tmp:
            outside_root = Path(outside_tmp)
            sentinel = outside_root / "etc" / "passwd"
            sentinel.parent.mkdir(parents=True)
            sentinel.write_text("outside-sandbox-sentinel-content\n", encoding="utf-8")

            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                # Point the escaping link at a *real* file outside the skill
                # directory sandbox (relative path from root up to it), so a
                # checker bug that actually opens the path would either
                # succeed silently (wrong) or blow up with an unrelated
                # exception (also wrong) instead of cleanly reporting it.
                rel_escape = "../" * 6 + str(sentinel).lstrip("/")
                _write(
                    root / "SKILL.md",
                    "---\nname: demo\ndescription: demo\n---\n\n"
                    f"See [outside]({rel_escape}).\n",
                )

                findings = cr.check_skill(root)  # must not raise

                hits = _findings_of_kind(findings, "path-escapes-skill-dir")
                self.assertEqual(len(hits), 1, findings)
                self.assertEqual(hits[0].level, "error")

                # broken-link must NOT also fire for the same path — the
                # escape check owns it, and the checker must never have
                # stat'd/opened the real file to find out it "exists".
                self.assertEqual(_findings_of_kind(findings, "broken-link"), [])

                referenced = cr.extract_local_references(
                    (root / "SKILL.md").read_text(encoding="utf-8"), root
                )
                self.assertNotIn(sentinel, referenced)
                for rel in referenced:
                    self.assertFalse(str(rel).startswith(".."))


class CliTests(unittest.TestCase):
    def test_shipped_skill_exits_zero(self) -> None:
        exit_code = cr.main([str(_SHIPPED_SKILL)])
        self.assertEqual(exit_code, 0)

    def test_missing_skill_md_raises_file_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                cr.check_skill(Path(tmp))

    def test_relative_dot_skill_dir_has_zero_findings(self) -> None:
        """Regression: passing '.' as skill_dir used to misreport ordinary
        in-directory links as path-escapes-skill-dir, because
        os.path.normpath(".") == "." shares no string prefix with the
        normpath of an in-directory link unless both sides are resolved to
        absolute paths first."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root / "SKILL.md",
                "---\nname: demo\ndescription: demo\n---\n\n"
                "See [reference/topic.md](reference/topic.md) for detail.\n",
            )
            _write(root / "reference" / "topic.md", "# Topic\n\nShort body.\n")
            cwd = Path.cwd()
            os.chdir(root)
            try:
                findings = cr.check_skill(Path("."))
            finally:
                os.chdir(cwd)
            self.assertEqual(findings, [], f"expected zero findings, got: {findings}")


if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    sys.exit(0 if result.wasSuccessful() else 1)
