# Knowledge base

Durable, cross-linked notes that outlive a single build cycle — patterns,
gotchas, and distilled learnings the agents accumulate over time so each cycle
starts smarter than the last. This is the project's "long-term memory," separate
from `research/` (which is per-cycle and dated).

## It's an Obsidian vault — but you don't need Obsidian for it to work

This whole repo is plain Markdown, so the agents read and write these notes with
ordinary file tools regardless of whether Obsidian is installed. Obsidian is
purely an optional **human** GUI over the same files: backlinks, graph view, and
fast search.

**No account, no purchase, no setup service required.** Obsidian's core app is
free and fully local — a "vault" is just a folder. To browse this knowledge base
graphically: install Obsidian, choose **"Open folder as vault,"** and point it at
`~/agentlab` (or just this `knowledge/` folder). Obsidian will create its own
`.obsidian/` config on first open; that's fine to leave un-tracked.

You'd only ever pay for the optional **Sync** ($5/mo) or **Publish** ($10/mo)
add-ons — neither is needed here, since git already versions and backs up the folder.

## Conventions

- Link notes with `[[wikilink]]` syntax (Obsidian resolves these; they're also
  just readable text). Link liberally — a link to a note that doesn't exist yet
  marks a topic worth writing later.
- One idea per note. Keep [`INDEX.md`](INDEX.md) pointing at the main entry points.
