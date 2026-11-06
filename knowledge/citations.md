# Citations (Claude API)

Source: [Citations docs](https://platform.claude.com/docs/en/build-with-claude/citations), read 2026-11-06. Stable, all active models.

- `citations: {enabled: true}` on each `document` block; all-or-none per request.
- Shapes: plain text -> `char_location` (0-indexed, exclusive end); PDF -> `page_location` (1-indexed pages, exclusive end); custom content -> `content_block_location` (0-indexed blocks, exclusive end). `document_index` is 0-indexed across all documents in the request.
- `cited_text` is extracted by the API, free as output and as re-sent input.
- Streaming: `citations_delta` carries one citation to append to the current text block.
- Citations plus `output_config.format` is a 400; parse one-line-per-field text instead.
- "Guaranteed valid pointers" covers the API's own parse only; a client-side re-slice check is cheap. Whether `cited_text == doc[start:end]` exactly is unverified (see `research/2026-11-06-citations-verifier.md`).

Related: [[anthropic-api-2026-10-21]], [[strict-tool-use]], [[streaming-tool-use]]
