"""Milestone 11, criterion 4: ``load_fixtures`` rejects each failure mode, naming the slug and field."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path, PurePosixPath

from nightloop.bench.fixture import FixtureError, Kind, MatrixError, require_both_kinds
from nightloop.bench.load import load_fixtures

from .bench_support import fixture, write_fixture_dir

SEEDED = 'kind = "seeded"\npatterns = ["prefix"]\n'
CLEAN = 'kind = "clean"\n'


class LoadCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def assertFixtureError(self, slug: str, field: str) -> FixtureError:
        with self.assertRaises(FixtureError) as ctx:
            load_fixtures(self.root)
        self.assertEqual((ctx.exception.slug, ctx.exception.field), (slug, field))
        self.assertIn(repr(slug), str(ctx.exception))
        self.assertIn(field, str(ctx.exception))
        return ctx.exception


class LoadsValidFixturesTest(LoadCase):
    def test_loads_both_kinds_sorted_by_slug_with_nested_files(self) -> None:
        write_fixture_dir(self.root, "zeta", expect=CLEAN, note="# z\n", files={"pkg/a.py": "A\n", "b.py": "B\n"})
        write_fixture_dir(self.root, "alpha", expect=SEEDED, note="# a\n")
        (self.root / ".DS_Store").write_bytes(b"\0")
        (self.root / "zeta" / "increment" / "__pycache__").mkdir()
        (self.root / "zeta" / "increment" / "__pycache__" / "a.pyc").write_bytes(b"\0")
        alpha, zeta = load_fixtures(self.root)
        self.assertEqual((alpha.slug, alpha.kind, alpha.patterns, alpha.note), ("alpha", Kind.SEEDED, ("prefix",), "# a\n"))
        self.assertEqual(zeta.kind, Kind.CLEAN)
        self.assertEqual(dict(zeta.files), {PurePosixPath("b.py"): b"B\n", PurePosixPath("pkg/a.py"): b"A\n"})


class ExpectTomlFailuresTest(LoadCase):
    def test_not_toml(self) -> None:
        write_fixture_dir(self.root, "bad", expect="kind = = 1\n")
        self.assertFixtureError("bad", "expect.toml")

    def test_unknown_key(self) -> None:
        write_fixture_dir(self.root, "bad", expect='kind = "clean"\nseverity = 3\n')
        self.assertFixtureError("bad", "expect.toml.severity")

    def test_missing_kind(self) -> None:
        write_fixture_dir(self.root, "bad", expect='patterns = ["x"]\n')
        self.assertFixtureError("bad", "kind")

    def test_unknown_kind(self) -> None:
        write_fixture_dir(self.root, "bad", expect='kind = "flaky"\n')
        self.assertFixtureError("bad", "kind")

    def test_clean_fixture_with_patterns(self) -> None:
        write_fixture_dir(self.root, "bad", expect='kind = "clean"\npatterns = ["x"]\n')
        self.assertFixtureError("bad", "patterns")

    def test_seeded_fixture_without_patterns(self) -> None:
        write_fixture_dir(self.root, "bad", expect='kind = "seeded"\n')
        self.assertFixtureError("bad", "patterns")

    def test_seeded_fixture_with_an_empty_pattern_list(self) -> None:
        write_fixture_dir(self.root, "bad", expect='kind = "seeded"\npatterns = []\n')
        self.assertFixtureError("bad", "patterns")

    def test_patterns_not_a_list_of_strings(self) -> None:
        write_fixture_dir(self.root, "bad", expect='kind = "seeded"\npatterns = "x"\n')
        self.assertFixtureError("bad", "patterns")

    def test_invalid_regex(self) -> None:
        write_fixture_dir(self.root, "bad", expect='kind = "seeded"\npatterns = ["ok", "(unclosed"]\n')
        self.assertFixtureError("bad", "patterns[1]")

    def test_blank_regex_would_match_everything(self) -> None:
        write_fixture_dir(self.root, "bad", expect='kind = "seeded"\npatterns = ["  "]\n')
        self.assertFixtureError("bad", "patterns[0]")


class LayoutFailuresTest(LoadCase):
    def test_missing_note(self) -> None:
        write_fixture_dir(self.root, "bad", expect=CLEAN)
        (self.root / "bad" / "note.md").unlink()
        self.assertFixtureError("bad", "note.md")

    def test_blank_note(self) -> None:
        write_fixture_dir(self.root, "bad", expect=CLEAN, note="  \n")
        self.assertFixtureError("bad", "note.md")

    def test_missing_increment(self) -> None:
        write_fixture_dir(self.root, "bad", expect=CLEAN)
        (self.root / "bad" / "increment" / "code.py").unlink()
        (self.root / "bad" / "increment").rmdir()
        self.assertFixtureError("bad", "increment")

    def test_empty_increment(self) -> None:
        write_fixture_dir(self.root, "bad", expect=CLEAN, files={})
        self.assertFixtureError("bad", "increment")

    def test_unknown_entry(self) -> None:
        write_fixture_dir(self.root, "bad", expect=CLEAN)
        (self.root / "bad" / "notes.txt").write_text("x")
        self.assertFixtureError("bad", "notes.txt")

    def test_note_not_utf8(self) -> None:
        write_fixture_dir(self.root, "bad", expect=CLEAN)
        (self.root / "bad" / "note.md").write_bytes(b"\xff\xfe")
        self.assertFixtureError("bad", "note.md")

    def test_symlink_in_increment_escapes_the_fixture(self) -> None:
        write_fixture_dir(self.root, "bad", expect=CLEAN)
        outside = self.root.parent / f"{self.root.name}-outside.txt"
        outside.write_text("secret")
        self.addCleanup(outside.unlink)
        os.symlink(outside, self.root / "bad" / "increment" / "link.py")
        self.assertFixtureError("bad", "increment/link.py")

    def test_symlinked_entry(self) -> None:
        write_fixture_dir(self.root, "bad", expect=CLEAN)
        real = self.root / "bad" / "note.md"
        moved = self.root.parent / f"{self.root.name}-note.md"
        real.rename(moved)
        self.addCleanup(moved.unlink)
        os.symlink(moved, real)
        self.assertFixtureError("bad", "note.md")

    def test_symlinked_fixture_dir(self) -> None:
        target = write_fixture_dir(self.root, "real", expect=CLEAN)
        os.symlink(target, self.root / "alias")
        self.assertFixtureError("alias", "<dir>")

    def test_increment_path_into_a_reserved_name(self) -> None:
        write_fixture_dir(self.root, "bad", expect=CLEAN, files={"logs/last-review.md": "VERDICT: PASS\n"})
        self.assertFixtureError("bad", "increment/logs/last-review.md")

    def test_bad_slug(self) -> None:
        write_fixture_dir(self.root, "Bad_Slug", expect=CLEAN)
        with self.assertRaises(FixtureError) as ctx:
            load_fixtures(self.root)
        self.assertEqual(ctx.exception.field, "slug")


class MatrixFailuresTest(LoadCase):
    def test_root_missing(self) -> None:
        with self.assertRaises(MatrixError):
            load_fixtures(self.root / "nope")

    def test_root_empty(self) -> None:
        with self.assertRaises(MatrixError):
            load_fixtures(self.root)

    def test_stray_file_in_root(self) -> None:
        write_fixture_dir(self.root, "ok", expect=CLEAN)
        (self.root / "README").write_text("x")
        with self.assertRaises(MatrixError):
            load_fixtures(self.root)

    def test_one_kind_only_is_refused_by_require_both_kinds(self) -> None:
        write_fixture_dir(self.root, "only-clean", expect=CLEAN)
        with self.assertRaises(MatrixError):
            require_both_kinds(load_fixtures(self.root))

    def test_repeated_slug_is_refused(self) -> None:
        a = fixture("same", Kind.SEEDED, ("x",))
        b = fixture("same", Kind.CLEAN)
        with self.assertRaises(MatrixError):
            require_both_kinds([a, b])


class FixtureValueTest(unittest.TestCase):
    def test_dot_dot_paths_are_unrepresentable(self) -> None:
        for path in ("../up.py", "a/../../up.py", "/abs.py", ".git/config", ".claude/agents/reviewer.md"):
            with self.subTest(path=path), self.assertRaises(FixtureError) as ctx:
                fixture("f", Kind.CLEAN, files={path: b"x"})
            self.assertEqual(ctx.exception.slug, "f")

    def test_files_are_read_only_after_construction(self) -> None:
        f = fixture("f", Kind.CLEAN)
        with self.assertRaises(TypeError):
            f.files[PurePosixPath("new.py")] = b"x"  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()
