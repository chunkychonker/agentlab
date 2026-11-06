"""The request-side boundary: document blocks become typed sources in document_index order."""

from __future__ import annotations

import unittest

from live_demo import LAKE_TEXT, TERRACE_BLOCKS, build_request
from sources import CustomContent, Opaque, PlainText, sources_from_messages


def doc(source: dict) -> dict:
    return {"type": "document", "source": source, "citations": {"enabled": True}}


PLAIN = doc({"type": "text", "media_type": "text/plain", "data": "Alpha. Beta."})
CUSTOM = doc({"type": "content", "content": [{"type": "text", "text": "One."}, {"type": "text", "text": "Two."}]})
PDF = doc({"type": "base64", "media_type": "application/pdf", "data": "JVBERi0="})


class SourcesFromMessages(unittest.TestCase):
    def test_documents_are_numbered_across_messages_in_order(self):
        messages = [
            {"role": "user", "content": [PLAIN, {"type": "text", "text": "q1"}]},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": [PDF, CUSTOM, {"type": "text", "text": "q2"}]},
        ]
        sources = sources_from_messages(messages)
        self.assertEqual(sources[0], PlainText("Alpha. Beta."))
        self.assertIsInstance(sources[1], Opaque)
        self.assertEqual(sources[2], CustomContent(("One.", "Two.")))
        self.assertEqual(len(sources), 3)

    def test_live_demo_request_maps_to_its_two_sources(self):
        sources = sources_from_messages(build_request()["messages"])
        self.assertEqual(sources, [PlainText(LAKE_TEXT), CustomContent(TERRACE_BLOCKS)])

    def test_unmodelable_custom_content_is_opaque_not_guessed(self):
        as_string = doc({"type": "content", "content": "one big string"})
        with_image = doc({"type": "content", "content": [{"type": "image", "source": {}}]})
        unknown = doc({"type": "future_kind"})
        for block in (as_string, with_image, unknown):
            with self.subTest(block=block):
                [source] = sources_from_messages([{"role": "user", "content": [block]}])
                self.assertIsInstance(source, Opaque)

    def test_document_inside_tool_result_is_refused(self):
        nested = {"type": "tool_result", "tool_use_id": "toolu_1", "content": [PLAIN]}
        with self.assertRaisesRegex(ValueError, "tool_result"):
            sources_from_messages([{"role": "user", "content": [nested]}])

    def test_malformed_document_blocks_raise(self):
        cases = [
            {"type": "document"},
            doc({"media_type": "text/plain", "data": "x"}),
            doc({"type": "text", "media_type": "text/plain"}),
            doc({"type": "text", "media_type": "text/plain", "data": 3}),
        ]
        for block in cases:
            with self.subTest(block=block), self.assertRaises(ValueError):
                sources_from_messages([{"role": "user", "content": [block]}])

    def test_source_types_reject_wrong_payloads(self):
        with self.assertRaises(TypeError):
            CustomContent(["a", "b"])  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            PlainText(None)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
