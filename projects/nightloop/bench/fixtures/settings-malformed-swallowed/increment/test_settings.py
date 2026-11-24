import json
import tempfile
import unittest
from pathlib import Path

from settings import Settings, SettingsError, load_settings


class LoadSettingsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def write(self, doc):
        path = self.dir / "tool.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        return path

    def test_a_missing_file_gives_the_defaults(self):
        self.assertEqual(load_settings(self.dir / "absent.json"), Settings())

    def test_the_defaults(self):
        self.assertEqual(Settings(), Settings(retries=3, verbose=False, name="tool"))

    def test_an_object_overrides_key_by_key(self):
        loaded = load_settings(self.write({"retries": 5, "verbose": True}))
        self.assertEqual(loaded, Settings(retries=5, verbose=True, name="tool"))

    def test_a_non_object_raises_naming_the_file(self):
        path = self.write([1, 2])
        with self.assertRaises(SettingsError) as caught:
            load_settings(path)
        self.assertIn(str(path), str(caught.exception))

    def test_an_unknown_key_raises(self):
        with self.assertRaises(SettingsError):
            load_settings(self.write({"retry": 5}))

    def test_bad_values_raise(self):
        for doc in ({"retries": 11}, {"retries": -1}, {"retries": True}, {"retries": "3"},
                    {"verbose": 1}, {"name": ""}, {"name": 7}):
            with self.subTest(doc=doc), self.assertRaises(SettingsError):
                load_settings(self.write(doc))


if __name__ == "__main__":
    unittest.main()
