import shutil
import tempfile
import unittest
from pathlib import Path

from check_bare_mentions import check_skill


class TestCheckBareMentions(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_clean_flat_layout_passes(self):
        (self.tmp / "SKILL.md").write_text(
            "---\nname: demo\n---\n\nSee [notes](notes.md) for details.\n"
        )
        (self.tmp / "notes.md").write_text("# Notes\n\nSome content.\n")
        self.assertEqual(check_skill(self.tmp), [])

    def test_broken_link_at_root_is_detected(self):
        (self.tmp / "SKILL.md").write_text(
            "---\nname: demo\n---\n\nSee [missing](missing.md) for details.\n"
        )
        errors = check_skill(self.tmp)
        self.assertEqual(len(errors), 1)
        self.assertIn("does not exist", errors[0])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
