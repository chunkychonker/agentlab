"""Milestone 11, criterion 8: the shipped fixtures load, their own tests pass, and each seeded bug is real.

Every fixture is materialized by the real ``TempGitWorkspace`` and its tests
run the way a reviewer would (``python -m unittest discover`` at the repo
root). A seeded fixture whose own tests failed would measure ``run_silent``,
not review, so seeded tests must pass too. A probe per seeded fixture then
shows the planted bug really happens.
"""

from __future__ import annotations

import re
import subprocess
import sys
import textwrap
from pathlib import Path

from nightloop.bench.fixture import Kind
from nightloop.bench.load import load_fixtures
from nightloop.bench.workspace import TempGitWorkspace

from .bench_support import SHIPPED_FIXTURES, TEMPLATE, IsolatedGitCase

PORTED = {"self-reference-bare-mention", "subdirectory-bare-mention-overmatch"}

# What each seeded fixture's bug does, run in the materialized repo. Each
# prints "BUG" only if the planted behaviour happens.
PROBES = {
    "self-reference-bare-mention": """
        from pathlib import Path
        from check_bare_mentions import check_skill
        errors = check_skill(Path("skills/demo-skill"))
        # The demo skill is correct, so any error is the bug.
        print("BUG" if errors else "ok", errors)
    """,
    "subdirectory-bare-mention-overmatch": """
        from pathlib import Path
        from check_bare_mentions import check_skill
        errors = check_skill(Path("skills/demo-skill"))
        assert Path("skills/demo-skill/reference/finance.md").is_file()
        print("BUG" if any("finance.md" in e for e in errors) else "ok", errors)
    """,
    "settings-malformed-swallowed": """
        import tempfile
        from pathlib import Path
        from settings import Settings, SettingsError, load_settings
        bad = Path(tempfile.mkdtemp()) / "settings.json"
        bad.write_text("{not json")
        try:
            got = load_settings(bad)
        except SettingsError:
            print("ok raised")
        else:
            print("BUG" if got == Settings() else "ok", got)
    """,
    "path-containment-prefix": """
        import tempfile
        from pathlib import Path
        from containment import EscapeError, resolve_under
        top = Path(tempfile.mkdtemp())
        (top / "data").mkdir()
        (top / "data-old").mkdir()
        (top / "data-old" / "secret.txt").write_text("s")
        try:
            got = resolve_under(top / "data", "../data-old/secret.txt")
        except EscapeError:
            print("ok raised")
        else:
            print("BUG", got)
    """,
}


def _python(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-B", *args], cwd=repo, capture_output=True, text=True, timeout=120)


class ShippedFixturesTest(IsolatedGitCase):
    def setUp(self) -> None:
        super().setUp()
        self.fixtures = load_fixtures(SHIPPED_FIXTURES)
        self.workspace = TempGitWorkspace(TEMPLATE, "reviewer", parent=self.tmp)

    def test_the_matrix_has_what_the_milestone_requires(self) -> None:
        seeded = {f.slug for f in self.fixtures if f.kind is Kind.SEEDED}
        clean = {f.slug for f in self.fixtures if f.kind is Kind.CLEAN}
        self.assertLessEqual(PORTED, seeded)
        self.assertGreaterEqual(len(seeded - PORTED), 2)
        self.assertGreaterEqual(len(clean), 3)
        self.assertEqual(set(PROBES), seeded, "every seeded fixture needs a probe showing its bug")

    def test_every_fixtures_own_tests_pass(self) -> None:
        for f in self.fixtures:
            with self.subTest(fixture=f.slug), self.workspace.materialize(f) as repo:
                done = _python(repo, "-m", "unittest", "discover", "-v")
                self.assertEqual(done.returncode, 0, done.stderr[-2000:])
                ran = re.search(r"^Ran (\d+) tests?", done.stderr, re.MULTILINE)
                self.assertIsNotNone(ran, done.stderr[-2000:])
                self.assertGreater(int(ran.group(1)), 0)

    def test_every_seeded_bug_is_real(self) -> None:
        for f in self.fixtures:
            if f.kind is not Kind.SEEDED:
                continue
            with self.subTest(fixture=f.slug), self.workspace.materialize(f) as repo:
                done = _python(repo, "-c", textwrap.dedent(PROBES[f.slug]))
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertTrue(done.stdout.startswith("BUG"), done.stdout)

    def test_the_correct_twin_does_not_have_the_bug(self) -> None:
        (twin,) = [f for f in self.fixtures if f.slug == "path-containment-correct"]
        (seeded,) = [f for f in self.fixtures if f.slug == "path-containment-prefix"]
        self.assertEqual(twin.note, seeded.note)
        with self.workspace.materialize(twin) as repo:
            done = _python(repo, "-c", textwrap.dedent(PROBES["path-containment-prefix"]))
        self.assertEqual(done.stdout.strip(), "ok raised", done.stderr)

    def test_no_shipped_increment_names_its_bug_or_slug(self) -> None:
        for f in self.fixtures:
            for path, content in f.files.items():
                text = content.decode("utf-8")
                with self.subTest(fixture=f.slug, path=str(path)):
                    self.assertNotIn(f.slug, text)
                    self.assertNotIn(f.slug, str(path))
            self.assertNotIn(f.slug, f.note)
