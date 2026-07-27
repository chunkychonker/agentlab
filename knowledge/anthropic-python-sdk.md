# Anthropic Python SDK

- Package `anthropic` on [PyPI](https://pypi.org/project/anthropic/); as of
  2026-07-27 latest is **0.120.0** (released 2026-07-24), requires Python >=3.9.
- `client = anthropic.Anthropic()` reads `ANTHROPIC_API_KEY` from the env.
- Core call: `client.messages.create(model=..., max_tokens=..., messages=[...])`.
  `max_tokens` is required. `messages` is a list of `{"role","content"}`; content
  is a string or a list of typed blocks.
- Response: `.content` is a list of blocks (`.type` is `"text"`, `"tool_use"`,
  ...); `.stop_reason` drives control flow (`"end_turn"`, `"tool_use"`,
  `"max_tokens"`); `.usage` reports input/output tokens.
- Higher-level helpers exist (`Tool Runner` for the tool loop, streaming,
  `client.beta.messages.*` for beta features) — reach for the hand-written loop
  only when the goal is to understand it.

Related: [[tool-use-loop]], [[anthropic-models]]
