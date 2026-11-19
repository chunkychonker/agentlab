"""Milestone 7: the metrics file edge (``append_sink``, ``read_lines``)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nightloop.cli.metrics_file import MetricsFileError, append_sink, read_lines


class MetricsFileTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_the_sink_creates_logs_lazily_and_appends_one_line_per_call(self) -> None:
        path = self.root / "logs" / "metrics.jsonl"
        sink = append_sink(path)
        self.assertFalse(path.parent.exists(), "building the sink must not touch the disk")
        sink('{"a":1}')
        sink('{"b":2}')
        self.assertEqual(path.read_text(encoding="utf-8"), '{"a":1}\n{"b":2}\n')

    def test_the_sink_keeps_what_was_there(self) -> None:
        path = self.root / "metrics.jsonl"
        path.write_text('{"old":0}\n', encoding="utf-8")
        append_sink(path)('{"new":1}')
        self.assertEqual(read_lines(path), ['{"old":0}', '{"new":1}'])

    def test_a_line_with_a_newline_is_refused_and_nothing_is_written(self) -> None:
        path = self.root / "metrics.jsonl"
        sink = append_sink(path)
        for bad in ('{"a":1}\n{"b":2}', '{"a":1}\r'):
            with self.subTest(line=bad), self.assertRaises(ValueError):
                sink(bad)
        self.assertFalse(path.exists())

    def test_reading_a_missing_file_or_a_directory_names_the_path(self) -> None:
        for path in (self.root / "nope.jsonl", self.root):
            with self.subTest(path=path), self.assertRaises(MetricsFileError) as caught:
                read_lines(path)
            self.assertIn(str(path), str(caught.exception))

    def test_bytes_that_are_not_utf8_are_replaced_not_raised(self) -> None:
        path = self.root / "metrics.jsonl"
        path.write_bytes(b'{"a":1}\n\xff\xfe torn\n')
        lines = read_lines(path)
        self.assertEqual(lines[0], '{"a":1}')
        self.assertEqual(len(lines), 2)


if __name__ == "__main__":
    unittest.main()
