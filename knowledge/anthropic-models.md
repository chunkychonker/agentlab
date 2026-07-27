# Anthropic model IDs (current)

Verified 2026-07-27 from the [models overview](https://platform.claude.com/docs/en/about-claude/models/overview).
Every ID is a **pinned snapshot** — from the 4.6 generation on, IDs are dateless
but still pinned (not evergreen pointers). Don't guess model ids from memory;
re-check this page.

| Model | API ID (alias) | $/MTok in | $/MTok out | Notes |
|---|---|---|---|---|
| Claude Fable 5 | `claude-fable-5` | 10 | 50 | Top tier, long-running agents |
| Claude Opus 5 | `claude-opus-5` | 5 | 25 | Complex agentic coding default |
| Claude Sonnet 5 | `claude-sonnet-5` | 3 | 15 | $2/$10 intro thru 2026-08-31 |
| Claude Haiku 4.5 | `claude-haiku-4-5-20251001` (`claude-haiku-4-5`) | 1 | 5 | Fastest/cheapest |

- Legacy but still callable: `claude-opus-4-8`, `claude-opus-4-7`,
  `claude-opus-4-6`, `claude-sonnet-4-6`, `claude-sonnet-4-5-20250929`, etc.
- **For cheap runnable examples, default to `claude-haiku-4-5`.** Put the model
  id in one constant so switching tiers is a one-line change.
- Auth: env var `ANTHROPIC_API_KEY`; `anthropic.Anthropic()` reads it with no args.

Related: [[tool-use-loop]], [[anthropic-python-sdk]]
