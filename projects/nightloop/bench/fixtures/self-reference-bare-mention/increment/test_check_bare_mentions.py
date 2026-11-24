import shutil
import tempfile
import unittest
from pathlib import Path

from check_bare_mentions import check_skill


class TestCheckBareMentions(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_clean_skill_with_one_leaf_reference_passes(self):
        (self.tmp / "SKILL.md").write_text(
            "---\nname: demo\n---\n\n"
            "# Demo\n\nSee [REFERENCE.md](REFERENCE.md) for details.\n"
        )
        (self.tmp / "REFERENCE.md").write_text("# Reference\n\nSome content.\n")
        self.assertEqual(check_skill(self.tmp), [])

    def test_broken_link_is_detected(self):
        (self.tmp / "SKILL.md").write_text(
            "---\nname: demo\n---\n\n"
            "See [MISSING.md](MISSING.md) for details.\n"
        )
        errors = check_skill(self.tmp)
        self.assertEqual(len(errors), 1)
        self.assertIn("does not exist", errors[0])


if __name__ == "__main__":
    unittest.main()
