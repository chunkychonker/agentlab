import os
import tempfile
import unittest
from pathlib import Path

from containment import EscapeError, resolve_under


class ResolveUnderTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve() / "data"
        (self.root / "reports").mkdir(parents=True)
        (self.root / "reports" / "2026.csv").write_text("a,b\n", encoding="utf-8")

    def test_a_file_inside_the_root_is_returned_resolved(self):
        self.assertEqual(resolve_under(self.root, "reports/2026.csv"), self.root / "reports" / "2026.csv")

    def test_the_root_itself_is_inside(self):
        self.assertEqual(resolve_under(self.root, "."), self.root)

    def test_a_file_that_does_not_exist_yet_is_allowed(self):
        self.assertEqual(resolve_under(self.root, "new/file.txt"), self.root / "new" / "file.txt")

    def test_a_dotdot_escape_raises(self):
        with self.assertRaises(EscapeError):
            resolve_under(self.root, "../etc/passwd")

    def test_an_absolute_path_raises(self):
        with self.assertRaises(EscapeError):
            resolve_under(self.root, "/etc/passwd")

    def test_a_symlink_pointing_outside_raises(self):
        os.symlink(self.root.parent, self.root / "out")
        with self.assertRaises(EscapeError):
            resolve_under(self.root, "out/anything")

    def test_an_empty_path_raises(self):
        with self.assertRaises(EscapeError):
            resolve_under(self.root, "")


if __name__ == "__main__":
    unittest.main()
