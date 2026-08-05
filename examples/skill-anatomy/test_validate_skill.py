"""Offline self-test for validate_skill.py. No API key, no network.

Run:
    python test_validate_skill.py

Builds SKILL.md fixtures in-memory (via parse_frontmatter/validate_* directly,
and one real file on disk for the end-to-end path through validate_skill) and
asserts the *specific* finding each broken rule is supposed to produce — not
just "some error happened" — so the test actually pins down which rule fired.

Covers:
  1. The shipped skills/commit-message/SKILL.md validates clean (zero errors).
  2. An uppercase letter in `name` produces a charset error.
  3. `name` containing "claude" produces a reserved-word error.
  4. A missing `description` produces an error.
  5. A `description` of 1025 chars produces a length error.
  6. A `description` containing an XML tag produces an error.
  7. parse_frontmatter raises ValueError (never returns silently) on a file
     with no opening '---' and on one with no closing '---'.
  8. validate_skill turns a malformed-frontmatter file into one error
     Finding, not an exception.
  9. validate_skill propagates FileNotFoundError for a missing path (a
     boundary failure, not something to swallow).
"""

from __future__ import annotations

from pathlib import Path

import validate_skill as vs

REPO_ROOT = Path(__file__).parent
SHIPPED_SKILL = REPO_ROOT / "skills" / "commit-message" / "SKILL.md"


def _errors(findings: list[vs.Finding]) -> list[vs.Finding]:
    return [f for f in findings if f.level == "error"]


# --------------------------------------------------------------------------- #
# 1. The shipped skill is clean
# --------------------------------------------------------------------------- #

def test_shipped_skill_has_zero_errors() -> None:
    findings = vs.validate_skill(SHIPPED_SKILL)
    errors = _errors(findings)
    assert errors == [], f"shipped skill should have zero errors, got: {errors}"
    print("ok  skills/commit-message/SKILL.md validates with zero errors")


# --------------------------------------------------------------------------- #
# 2. name charset
# --------------------------------------------------------------------------- #

def test_uppercase_name_is_charset_error() -> None:
    findings = vs.validate_name("Commit-Message")
    errors = _errors(findings)
    assert any("characters outside" in f.message for f in errors), errors
    print("ok  uppercase letter in name -> charset error")


# --------------------------------------------------------------------------- #
# 3. reserved word
# --------------------------------------------------------------------------- #

def test_name_with_claude_is_reserved_word_error() -> None:
    findings = vs.validate_name("claude-helper")
    errors = _errors(findings)
    assert any("reserved word" in f.message for f in errors), errors
    print("ok  name containing 'claude' -> reserved-word error")


# --------------------------------------------------------------------------- #
# 4. missing description
# --------------------------------------------------------------------------- #

def test_missing_description_is_error() -> None:
    findings = vs.validate_description(None)
    errors = _errors(findings)
    assert len(errors) == 1 and "missing or empty" in errors[0].message, errors
    print("ok  missing description -> error")


# --------------------------------------------------------------------------- #
# 5. description length
# --------------------------------------------------------------------------- #

def test_long_description_is_length_error() -> None:
    findings = vs.validate_description("x" * 1025)
    errors = _errors(findings)
    assert any("exceeds the 1024-char limit" in f.message for f in errors), errors
    print("ok  1025-char description -> length error")


# --------------------------------------------------------------------------- #
# 6. XML tag in description
# --------------------------------------------------------------------------- #

def test_description_with_xml_tag_is_error() -> None:
    findings = vs.validate_description("Does a thing <foo> for you")
    errors = _errors(findings)
    assert any("XML tag" in f.message for f in errors), errors
    print("ok  description containing an XML tag -> error")


# --------------------------------------------------------------------------- #
# 7. parse_frontmatter never silently degrades
# --------------------------------------------------------------------------- #

def test_parse_frontmatter_raises_on_missing_opening_delimiter() -> None:
    try:
        vs.parse_frontmatter("name: foo\ndescription: bar\n")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for missing opening '---'")
    print("ok  parse_frontmatter raises ValueError with no opening '---'")


def test_parse_frontmatter_raises_on_missing_closing_delimiter() -> None:
    try:
        vs.parse_frontmatter("---\nname: foo\ndescription: bar\n")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for missing closing '---'")
    print("ok  parse_frontmatter raises ValueError with no closing '---'")


# --------------------------------------------------------------------------- #
# 8. validate_skill degrades malformed frontmatter to one Finding
# --------------------------------------------------------------------------- #

def test_validate_skill_reports_malformed_frontmatter_as_one_finding(tmp_path: Path) -> None:
    broken = tmp_path / "SKILL.md"
    broken.write_text("this file has no frontmatter delimiters at all\n")
    findings = vs.validate_skill(broken)
    assert len(findings) == 1, findings
    assert findings[0].level == "error"
    assert "malformed frontmatter" in findings[0].message
    print("ok  malformed frontmatter -> exactly one error Finding, no exception")


# --------------------------------------------------------------------------- #
# 9. missing file propagates FileNotFoundError
# --------------------------------------------------------------------------- #

def test_validate_skill_propagates_file_not_found() -> None:
    try:
        vs.validate_skill(Path("/nonexistent/path/SKILL.md"))
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("expected FileNotFoundError to propagate")
    print("ok  validate_skill propagates FileNotFoundError for a missing path")


# --------------------------------------------------------------------------- #

def main() -> int:
    import tempfile

    tests = [
        test_shipped_skill_has_zero_errors,
        test_uppercase_name_is_charset_error,
        test_name_with_claude_is_reserved_word_error,
        test_missing_description_is_error,
        test_long_description_is_length_error,
        test_description_with_xml_tag_is_error,
        test_parse_frontmatter_raises_on_missing_opening_delimiter,
        test_parse_frontmatter_raises_on_missing_closing_delimiter,
        test_validate_skill_propagates_file_not_found,
    ]
    for t in tests:
        t()

    with tempfile.TemporaryDirectory() as tmp:
        test_validate_skill_reports_malformed_frontmatter_as_one_finding(Path(tmp))

    print(f"\nAll {len(tests) + 1} self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
