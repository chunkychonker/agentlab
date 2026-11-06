"""Acceptance criterion 7: replayed stream events rebuild the non-streaming block."""

from __future__ import annotations

import unittest

from stream import accumulate

C1 = {
    "type": "char_location",
    "cited_text": "The grass is green. ",
    "document_index": 0,
    "document_title": "Example Document",
    "start_char_index": 0,
    "end_char_index": 20,
}
C2 = dict(C1, cited_text="The sky is blue.", start_char_index=20, end_char_index=36)


def start(index: int, block: dict | None = None) -> dict:
    return {"type": "content_block_start", "index": index, "content_block": block or {"type": "text", "text": ""}}


def text(index: int, fragment: str) -> dict:
    return {"type": "content_block_delta", "index": index, "delta": {"type": "text_delta", "text": fragment}}


def cite(index: int, citation: dict) -> dict:
    return {"type": "content_block_delta", "index": index, "delta": {"type": "citations_delta", "citation": citation}}


def stop(index: int) -> dict:
    return {"type": "content_block_stop", "index": index}


MESSAGE_START = {"type": "message_start", "message": {"id": "msg_1", "content": []}}
MESSAGE_DELTA = {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 9}}
MESSAGE_STOP = {"type": "message_stop"}


def replay(*body: dict) -> list[dict]:
    return [MESSAGE_START, *body, MESSAGE_DELTA, MESSAGE_STOP]


class C7StreamEqualsNonStreaming(unittest.TestCase):
    def test_text_then_two_citations_then_stop(self):
        events = replay(start(0), text(0, "the grass is green"), cite(0, C1), cite(0, C2), stop(0))
        non_streaming = [{"type": "text", "text": "the grass is green", "citations": [C1, C2]}]
        self.assertEqual(accumulate(events), non_streaming)

    def test_interleaved_fragments_and_uncited_neighbours(self):
        events = replay(
            start(0), text(0, "According to "), text(0, "the document, "), stop(0),
            start(1), cite(1, C1), text(1, "the grass "), {"type": "ping"}, text(1, "is green"), stop(1),
            start(2, {"type": "text", "text": "", "citations": None}), text(2, "."), stop(2),
        )
        self.assertEqual(
            accumulate(events),
            [
                {"type": "text", "text": "According to the document, "},
                {"type": "text", "text": "the grass is green", "citations": [C1]},
                {"type": "text", "text": "."},
            ],
        )


class StreamFailures(unittest.TestCase):
    def assertRejects(self, events: list[dict], pattern: str):
        with self.assertRaisesRegex(ValueError, pattern):
            accumulate(events)

    def test_truncated_stream_raises(self):
        self.assertRejects([MESSAGE_START, start(0), text(0, "half")], "without message_stop")
        self.assertRejects([MESSAGE_START, start(0), text(0, "half"), MESSAGE_STOP], "still open")

    def test_delta_for_unopened_block_raises(self):
        self.assertRejects(replay(cite(0, C1)), "not open")

    def test_block_started_twice_raises(self):
        self.assertRejects(replay(start(0), stop(0), start(0), stop(0)), "started twice")

    def test_non_text_block_is_out_of_scope(self):
        tool = {"type": "tool_use", "id": "toolu_1", "name": "x", "input": {}}
        self.assertRejects(replay(start(0, tool), stop(0)), "out of scope")

    def test_unknown_delta_and_event_types_raise(self):
        bad_delta = {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": "{"}}
        self.assertRejects(replay(start(0), bad_delta, stop(0)), "out of scope")
        self.assertRejects([MESSAGE_START, {"type": "error", "error": {"type": "overloaded_error"}}], "overloaded_error")

    def test_citations_delta_without_citation_raises(self):
        empty = {"type": "content_block_delta", "index": 0, "delta": {"type": "citations_delta"}}
        self.assertRejects(replay(start(0), empty, stop(0)), "'citation'")

    def test_event_after_message_stop_raises(self):
        self.assertRejects(replay(start(0), stop(0)) + [start(1)], "after message_stop")

    def test_gap_in_block_indices_raises(self):
        self.assertRejects(replay(start(0), stop(0), start(2), stop(2)), "contiguous")


if __name__ == "__main__":
    unittest.main()
