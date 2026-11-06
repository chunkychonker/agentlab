# Citation verifier: checking that a citation is true

With [citations](https://platform.claude.com/docs/en/build-with-claude/citations)
enabled on a `document` block, Claude's answer comes back as text blocks that
each carry pointers into your documents: a character range, a block range or a
page range, plus the `cited_text` the API extracted. The docs say the pointers
are "guaranteed to be valid", but that guarantee covers only the API's own
parse. Once an agent caches, rewrites, reorders or forwards those documents,
nothing checks that the pointers still match the text.

This example adds that check. A small pure verifier re-applies every pointer
to the source and compares the result with `cited_text`, exactly. A citation
counts as MATCH only when the two are equal.

From the research note:
[`research/2026-11-06-citations-verifier.md`](../../research/2026-11-06-citations-verifier.md).
Knowledge entry: [`knowledge/citations.md`](../../knowledge/citations.md).

## What a verdict means

| Status | When |
|---|---|
| `MATCH` | `0 <= start < end <= len(source)` and the re-sliced source equals `cited_text` byte for byte |
| `MISMATCH` | The range is valid but the text differs (whitespace counts) |
| `OUT_OF_RANGE` | Empty, reversed, negative or past-the-end range. The end is exclusive, so `start == end` is invalid. |
| `BAD_DOCUMENT_INDEX` | `document_index` is not in `[0, len(sources))`, or it points at the wrong kind of document (a `char_location` into custom content) |
| `UNVERIFIABLE` | `page_location` (no PDF parser offline), a citation into a PDF, file or URL source, or an unknown citation type. It is never silently counted as MATCH. |

A malformed citation (missing `type`, a missing field for a known type, or a
field of the wrong type) raises `ValueError` naming the block, the citation
and the field. Unknown citation types are reported, not raised, so a future
location kind doesn't crash the checker.

## Design (layers 1-3)

- **Intent:** make "this answer is grounded" checkable by code by re-deriving
  each citation's span from the request's own documents. Out of scope: PDF page
  verification, `search_result` and web-search citations, structured outputs,
  a RAG pipeline, and non-text blocks in streams.
- **Spec:** the 9 acceptance criteria in the research note. Test classes
  `C1`...`C9` map one-to-one onto them (`C7` is in `test_stream.py`). The
  failure modes are in each function's docstring.
- **Interfaces:**
  - `sources.sources_from_messages(messages) -> list[Source]`, with `Source = PlainText | CustomContent | Opaque`
  - `verifier.verify_response(sources: Sequence[Source], content: Sequence[Mapping]) -> list[Verdict]`
  - `verifier.uncited_text_blocks(content) -> list[int]`
  - `verifier.count_by_status(verdicts) -> dict[Status, int]` (every status present, zeros included)
  - `stream.accumulate(events: Iterable[Mapping]) -> list[dict]`
  - `Verdict(block_index, citation_index, status: Status, detail)`, frozen. `Status` is a `StrEnum`.

| File | One job |
|------|---------|
| `sources.py` | Turns a request's document blocks into typed sources, in `document_index` order. Pure. |
| `verifier.py` | Checks each citation in a response against those sources. Pure. |
| `stream.py` | Rebuilds cited text blocks from `text_delta` / `citations_delta` events. Pure. |
| `live_demo.py` | Live entry point: reads the key, holds `MODEL`, makes one call, prints the table. |
| `test_*.py` | Offline self-test, stdlib only. |
| `sdk_contract_check.py` | `live_demo.run` through the real SDK over mocked HTTP. Needs the SDK, no key. |

## Run the self-test (no API key, no network, stdlib only)

```bash
cd examples/citation-verifier
python3 -m unittest -v
```

Output from this build (Python 3.13.1, test-id suffixes trimmed):

```
test_document_inside_tool_result_is_refused ... ok
test_documents_are_numbered_across_messages_in_order ... ok
test_live_demo_request_maps_to_its_two_sources ... ok
test_malformed_document_blocks_raise ... ok
test_source_types_reject_wrong_payloads ... ok
test_unmodelable_custom_content_is_opaque_not_guessed ... ok
test_interleaved_fragments_and_uncited_neighbours ... ok
test_text_then_two_citations_then_stop ... ok
test_block_started_twice_raises ... ok
test_citations_delta_without_citation_raises ... ok
test_delta_for_unopened_block_raises ... ok
test_event_after_message_stop_raises ... ok
test_gap_in_block_indices_raises ... ok
test_non_text_block_is_out_of_scope ... ok
test_truncated_stream_raises ... ok
test_unknown_delta_and_event_types_raise ... ok
test_span_ending_at_document_end_is_match ... ok
test_span_equal_to_slice_is_match ... ok
test_altered_cited_text_is_mismatch ... ok
test_empty_span_is_out_of_range ... ok
test_end_past_document_is_out_of_range ... ok
test_negative_start_and_reversed_span_are_out_of_range ... ok
test_whitespace_difference_is_mismatch ... ok
test_index_equal_to_document_count_is_bad ... ok
test_location_kind_that_does_not_fit_the_document_is_bad ... ok
test_negative_index_is_bad ... ok
test_end_is_exclusive ... ok
test_last_block_is_match ... ok
test_multi_block_span_is_plain_concatenation ... ok
test_out_of_range_and_empty ... ok
test_single_block_is_match ... ok
test_wrong_text_is_mismatch ... ok
test_citation_into_opaque_source_is_unverifiable ... ok
test_page_location_is_never_match ... ok
test_unknown_type_is_unverifiable_without_raising ... ok
test_each_missing_char_location_field_is_named ... ok
test_each_missing_content_block_field_is_named ... ok
test_malformed_blocks_raise ... ok
test_missing_type_raises ... ok
test_wrongly_typed_fields_raise ... ok
test_uncited_text_blocks_are_flagged_and_other_blocks_ignored ... ok
test_inclusive_end_slice_fails_the_suite ... ok
test_same_suite_passes_unmutated ... ok
test_docs_example_response_verifies_in_order ... ok
test_mismatch_detail_shows_both_texts ... ok
test_no_citations_means_no_verdicts ... ok
test_verdict_is_frozen ... ok

----------------------------------------------------------------------
Ran 47 tests in 0.002s

OK
```

`C9` is the note's mutation check, written as a test. It swaps the
verifier's exclusive-end slice for an inclusive one, re-runs the `C1`, `C2`
and `C4` classes, and asserts that at least one of them fails. During the
build, 9 more bugs were injected by hand into a scratch copy. One survived at
first: allowing a negative `document_index`. Python's `sources[-1]` wrapped
onto a document of the other kind, so the kind check rejected it anyway.
`test_negative_index_is_bad` now uses indices that would wrap onto a matching
document, and it catches the bug. The other 8 failed the suite on the first
try:

- allowing an empty span
- counting a citation into an opaque source as MATCH
- joining blocks with a space
- leaving `citations: null` blocks out of the uncited list
- always emitting a `citations` key
- reversing the order of streamed citations
- tolerating a truncated stream
- guessing that string custom content is a single block

## Check the SDK boundary (needs the SDK, still no key)

```bash
cd examples/citation-verifier
pip install -r requirements.txt
python3 sdk_contract_check.py
```

Output from this build, against `anthropic==2.6.0`:

```
ok  SDK 2.6.0 (anthropic-version 2026-10-21): both documents sent with citations enabled; 2/2 MATCH, exit 0
ok  off-by-one answer -> MISMATCH, exit 1; answer with zero citations -> exit 1
ok  accumulate() == SDK accumulate_event() on a 2-block, 2-citations_delta stream

All 3 SDK contract checks passed.
```

The fake server computes its offsets from the request body it receives, so
check 1 exercises the real request-building, SDK parsing, `model_dump`,
`sources_from_messages` and `verify_response` path. Check 3 imports the SDK's
private `anthropic.lib.streaming._messages.accumulate_event`. If an SDK
upgrade moves it, that check fails at import time and the offline suite is
unaffected. SDK 2.x depends on `httpx2`, not `httpx`.

## Run it live (needs a key, billed: one short Haiku call)

```bash
cd examples/citation-verifier
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python3 live_demo.py
```

It sends one plain-text document (three sentences about Lake Mendota), one
custom-content document (three blocks about the Memorial Union terrace) and a
question that needs both. It then prints the answer and one row per citation.
A MATCH row includes the `repr` of the re-sliced text, so trailing whitespace
is visible. It exits 0 only if there was **at least one** citation and every
citation is MATCH. Zero citations exits 1, because "nothing to check" must not
read as "all checked". Without `ANTHROPIC_API_KEY` it prints a skip note and
exits 0. That path was run for this build.

**Not run for this build.** No key was available, so the research note's two
open questions are still open:

1. Is `cited_text` byte-identical to `doc[start:end]` for plain text, trailing
   space included?
2. Is a multi-block `content_block_location`'s `cited_text` the plain
   concatenation of the blocks, with no separator?

The verifier assumes **yes** to both, and it is strict about it: if the API
normalises whitespace or inserts a separator, the live run reports MISMATCH
and exits 1. It never quietly passes. A live run settles both questions;
record the answer in `knowledge/citations.md`. The demo's custom-content
blocks have no trailing spaces, so a multi-block citation would show whether
the API adds a separator.

## Decisions and assumptions

- **Typed sources instead of `str | Sequence[str]`.** The note's interface took
  `Sequence[str | Sequence[str]]`. A `str` is itself a `Sequence[str]`, so that
  union cannot tell a plain document from a custom-content document of
  one-character blocks. `PlainText`, `CustomContent` and `Opaque` make the
  mix-up unrepresentable. `sources_from_messages` builds them from the same
  `messages` list that was sent, so the document list can't drift from the
  request.
- **Wrong document kind becomes `BAD_DOCUMENT_INDEX`.** The note's status set
  has no separate slot for this. The pointer names a document that cannot hold
  that location kind, which is an index problem, and the `detail` says which
  kind it found.
- **Unmodelable custom content becomes `Opaque`.** This covers custom content
  given as a bare string, or containing image blocks. How the API numbers
  blocks there isn't documented, so citations into it are UNVERIFIABLE with a
  stated reason. They are not guessed.
- **Documents nested in `tool_result` raise.** Whether they share the
  `document_index` numbering is undocumented, and a wrong guess would silently
  shift every later index.
- **`accumulate` is text-only and strict.** It raises on any other block
  type, on a missing `message_stop`, on a block left open, and on the
  stream's `error` event. It never returns a partial result.
  [`../streaming-tool-loop/`](../streaming-tool-loop/) handles tool and
  thinking blocks.
- **Model.** `MODEL = "claude-haiku-4-5"`. The research note says citations
  support all active models, and the id is in SDK 2.6.0's model list. The
  `claude-api` skill is not installed here, so the id was not checked against
  it.
- **Incompatibility to remember.** Citations combined with
  `output_config.format` (structured outputs) returns a 400. This example
  doesn't use structured outputs.
