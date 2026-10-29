# Anthropic API 2026-10-21 and Python SDK 2.x

Source: [official migration guide](https://platform.claude.com/docs/en/api/migration-2026-10-21) (read 2026-10-29). PyPI latest `anthropic` 2.3.0.

- `anthropic` 2.0.0 is the first SDK sending `anthropic-version: 2026-10-21` (was `2023-06-01`). Any `anthropic>=0.x` pin now resolves to 2.x.
- 400s on the wire: `temperature`/`top_p`/`top_k` (even default values; SDK kwargs removed), `document` block directly in an assistant message, non-empty `citations` on user `text` blocks.
- Python SDK: `MessageParam(...)` call/subclass -> `TypeError` (dict literals fine); `models.list(betas=)` -> `TypeError` (use `client.beta.models`); API key sent as `Authorization: Bearer` (proxies reading `X-Api-Key` break); two credentials on one client -> `ValueError`.
- Bedrock: new version only for Claude 4.7+.
- Lab exposure: no runtime-breaking code found by grep, but 9 of 16 example `requirements.txt` are open-ended `anthropic>=`; untested on 2.x (see `research/2026-10-29-sdk2-compat-scan.md`).

Related: [[anthropic-python-sdk]], [[anthropic-models]], [[compaction]], [[sdk-retry-behavior]]
