"""Acceptance criteria 1-6, 8 and 9 from research/2026-11-06-citations-verifier.md.

Offline, stdlib only. Classes C1..C9 map one-to-one onto the note's numbered
criteria (C7, the stream accumulator, lives in test_stream.py).
"""

from __future__ import annotations

import io
import unittest
from unittest import mock

import verifier
from sources import CustomContent, Opaque, PlainText
from verifier import Status, Verdict, count_by_status, uncited_text_blocks, verify_response

# The docs' own plain-text example document: "The grass is green. " is [0, 20).
GRASS = "The grass is green. The sky is blue."
CHUNKS = ("First chunk. ", "Second chunk. ", "Third chunk.")
SOURCES = (PlainText(GRASS), CustomContent(CHUNKS))


def char(cited: str, start: int, end: int, doc: int = 0) -> dict:
    return {
        "type": "char_location",
        "cited_text": cited,
        "document_index": doc,
        "document_title": "Example Document",
        "start_char_index": start,
        "end_char_index": end,
    }


def blocks(cited: str, start: int, end: int, doc: int = 1) -> dict:
    return {
        "type": "content_block_location",
        "cited_text": cited,
        "document_index": doc,
        "document_title": "Chunks",
        "start_block_index": start,
        "end_block_index": end,
    }


def status_of(citation: dict, sources=SOURCES) -> Status:
    content = [{"type": "text", "text": "claim", "citations": [citation]}]
    [verdict] = verify_response(sources, content)
    return verdict.status


class C1CharMatch(unittest.TestCase):
    def test_span_equal_to_slice_is_match(self):
        self.assertIs(status_of(char("The grass is green. ", 0, 20)), Status.MATCH)

    def test_span_ending_at_document_end_is_match(self):
        self.assertIs(status_of(char("The sky is blue.", 20, len(GRASS))), Status.MATCH)


class C2CharFailures(unittest.TestCase):
    def test_altered_cited_text_is_mismatch(self):
        self.assertIs(status_of(char("The grass is blue. ", 0, 20)), Status.MISMATCH)

    def test_whitespace_difference_is_mismatch(self):
        # The open question in the note: a trailing-space difference must not pass.
        self.assertIs(status_of(char("The grass is green.", 0, 20)), Status.MISMATCH)

    def test_end_past_document_is_out_of_range(self):
        self.assertIs(status_of(char("The sky is blue.", 20, len(GRASS) + 1)), Status.OUT_OF_RANGE)

    def test_empty_span_is_out_of_range(self):
        self.assertIs(status_of(char("", 5, 5)), Status.OUT_OF_RANGE)

    def test_negative_start_and_reversed_span_are_out_of_range(self):
        self.assertIs(status_of(char("x", -1, 3)), Status.OUT_OF_RANGE)
        self.assertIs(status_of(char("x", 10, 4)), Status.OUT_OF_RANGE)


class C3DocumentIndex(unittest.TestCase):
    def test_index_equal_to_document_count_is_bad(self):
        self.assertIs(status_of(char("The grass is green. ", 0, 20, doc=2)), Status.BAD_DOCUMENT_INDEX)

    def test_negative_index_is_bad(self):
        # Each would MATCH if Python's negative indexing wrapped it onto a real document.
        self.assertIs(status_of(char("The grass is green. ", 0, 20, doc=-2)), Status.BAD_DOCUMENT_INDEX)
        self.assertIs(status_of(blocks("Second chunk. ", 1, 2, doc=-1)), Status.BAD_DOCUMENT_INDEX)

    def test_location_kind_that_does_not_fit_the_document_is_bad(self):
        self.assertIs(status_of(blocks("First chunk. ", 0, 1, doc=0)), Status.BAD_DOCUMENT_INDEX)
        self.assertIs(status_of(char("First", 0, 5, doc=1)), Status.BAD_DOCUMENT_INDEX)


class C4ContentBlocks(unittest.TestCase):
    def test_single_block_is_match(self):
        self.assertIs(status_of(blocks("Second chunk. ", 1, 2)), Status.MATCH)

    def test_multi_block_span_is_plain_concatenation(self):
        self.assertIs(status_of(blocks("First chunk. Second chunk. ", 0, 2)), Status.MATCH)

    def test_last_block_is_match(self):
        self.assertIs(status_of(blocks("Third chunk.", 2, 3)), Status.MATCH)

    def test_end_is_exclusive(self):
        # [1, 3) is blocks 1 AND 2, so citing only block 1's text is a mismatch.
        self.assertIs(status_of(blocks("Second chunk. ", 1, 3)), Status.MISMATCH)

    def test_wrong_text_is_mismatch(self):
        self.assertIs(status_of(blocks("Second chunk.", 1, 2)), Status.MISMATCH)

    def test_out_of_range_and_empty(self):
        self.assertIs(status_of(blocks("Third chunk.", 2, 4)), Status.OUT_OF_RANGE)
        self.assertIs(status_of(blocks("", 1, 1)), Status.OUT_OF_RANGE)


class C5Unverifiable(unittest.TestCase):
    def test_page_location_is_never_match(self):
        page = {
            "type": "page_location",
            "cited_text": "The grass is green. ",
            "document_index": 0,
            "start_page_number": 1,
            "end_page_number": 2,
        }
        self.assertIs(status_of(page), Status.UNVERIFIABLE)

    def test_unknown_type_is_unverifiable_without_raising(self):
        self.assertIs(status_of({"type": "web_search_result_location", "url": "x"}), Status.UNVERIFIABLE)

    def test_citation_into_opaque_source_is_unverifiable(self):
        sources = (Opaque("'base64' source: no offline text (PDF/file)"),)
        self.assertIs(status_of(char("anything", 0, 3), sources), Status.UNVERIFIABLE)


class C6MalformedRaises(unittest.TestCase):
    def test_each_missing_char_location_field_is_named(self):
        for field in ("cited_text", "document_index", "start_char_index", "end_char_index"):
            citation = char("The grass is green. ", 0, 20)
            del citation[field]
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, repr(field)):
                status_of(citation)

    def test_each_missing_content_block_field_is_named(self):
        for field in ("start_block_index", "end_block_index"):
            citation = blocks("Second chunk. ", 1, 2)
            del citation[field]
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, repr(field)):
                status_of(citation)

    def test_missing_type_raises(self):
        with self.assertRaisesRegex(ValueError, "'type'"):
            status_of({"cited_text": "x"})

    def test_wrongly_typed_fields_raise(self):
        with self.assertRaisesRegex(ValueError, "start_char_index must be an int"):
            status_of(char("x", True, 2))
        with self.assertRaisesRegex(ValueError, "cited_text must be a str"):
            status_of(char(None, 0, 2))

    def test_malformed_blocks_raise(self):
        with self.assertRaises(ValueError):
            verify_response(SOURCES, [{"text": "no type"}])
        with self.assertRaises(ValueError):
            verify_response(SOURCES, [{"type": "text"}])
        with self.assertRaises(ValueError):
            verify_response(SOURCES, [{"type": "text", "text": "t", "citations": "oops"}])


class C8Uncited(unittest.TestCase):
    def test_uncited_text_blocks_are_flagged_and_other_blocks_ignored(self):
        content = [
            {"type": "text", "text": "According to the document, "},
            {"type": "text", "text": "the grass is green", "citations": [char("The grass is green. ", 0, 20)]},
            {"type": "tool_use", "id": "toolu_1", "name": "x", "input": {}},
            {"type": "text", "text": " and that is all.", "citations": None},
            {"type": "text", "text": "", "citations": []},
        ]
        self.assertEqual(uncited_text_blocks(content), [0, 3, 4])


class C9MutationCheck(unittest.TestCase):
    """Swap the exclusive-end slice for an inclusive one; the suite must notice."""

    def test_inclusive_end_slice_fails_the_suite(self):
        def inclusive(seq, start, end):
            return seq[start : end + 1]

        loader = unittest.TestLoader()
        suite = unittest.TestSuite(
            loader.loadTestsFromTestCase(case) for case in (C1CharMatch, C2CharFailures, C4ContentBlocks)
        )
        with mock.patch.object(verifier, "_span", inclusive):
            result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
        self.assertGreater(len(result.failures), 0, "an inclusive-end slice went unnoticed")
        self.assertEqual(result.errors, [])

    def test_same_suite_passes_unmutated(self):
        loader = unittest.TestLoader()
        suite = unittest.TestSuite(
            loader.loadTestsFromTestCase(case) for case in (C1CharMatch, C2CharFailures, C4ContentBlocks)
        )
        result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
        self.assertTrue(result.wasSuccessful())


class ResponseLevel(unittest.TestCase):
    def test_docs_example_response_verifies_in_order(self):
        # Shape of the response example on the citations docs page.
        content = [
            {"type": "text", "text": "According to the document, "},
            {"type": "text", "text": "the grass is green", "citations": [char("The grass is green. ", 0, 20)]},
            {"type": "text", "text": " and "},
            {
                "type": "text",
                "text": "the sky is blue",
                "citations": [char("The sky is blue.", 20, 36), char("The sky is green.", 20, 36)],
            },
            {"type": "text", "text": ".", "citations": [blocks("Third chunk.", 2, 3)]},
        ]
        verdicts = verify_response(SOURCES, content)
        self.assertEqual(
            [(v.block_index, v.citation_index, v.status) for v in verdicts],
            [(1, 0, Status.MATCH), (3, 0, Status.MATCH), (3, 1, Status.MISMATCH), (4, 0, Status.MATCH)],
        )
        counts = count_by_status(verdicts)
        self.assertEqual(counts[Status.MATCH], 3)
        self.assertEqual(counts[Status.MISMATCH], 1)
        self.assertEqual(set(counts), set(Status))

    def test_mismatch_detail_shows_both_texts(self):
        [v] = verify_response(SOURCES, [{"type": "text", "text": "t", "citations": [char("The grass", 0, 20)]}])
        self.assertIn("'The grass is green. '", v.detail)
        self.assertIn("'The grass'", v.detail)

    def test_no_citations_means_no_verdicts(self):
        self.assertEqual(verify_response(SOURCES, [{"type": "text", "text": "hi"}]), [])

    def test_verdict_is_frozen(self):
        v = Verdict(0, 0, Status.MATCH, "")
        with self.assertRaises(AttributeError):
            v.status = Status.MISMATCH  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
