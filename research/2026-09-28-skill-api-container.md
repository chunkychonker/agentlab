# Agent Skills through the raw Messages API (not Claude Code)

Date: 2026-09-28

## Question

Every `examples/skill-*` directory in the lab so far uses Claude Code's
filesystem-based Skills mechanism (`~/.claude/skills/`, `.claude/skills/`).
Anthropic also ships a *second*, API-hosted Skills surface — upload a Skill
through `/v1/skills`, then reference it in a `messages.create()` call via a
`container` parameter and the code execution tool. `knowledge/agent-skills.md`
already names this surface but has never exercised it, and records a beta-header
requirement that current docs no longer show. Does the API surface actually
work the way today's docs describe, and can a builder prove — from the wire
transcript, not just the docs — that Claude really reads `SKILL.md` out of the
sandboxed container rather than answering from the prompt alone?

## Backlog note: why this topic

`BACKLOG.md`'s **Coding agents**, **Skills**, and **MCP** sections currently
have zero plain `[ ]` items — every entry is `[done #N]` or `[stranded
cycle/...]`. `gh pr list --state open` returns `[]` (nothing open to check for
duplication), and the stranded items in those three sections (MCP Streamable
HTTP transport, MCP resources through the real host, Cargo.toml manifest
scanning) already have completed research notes and built branches waiting on
a human merge decision — re-researching any of them would be the exact
PR #5/#6 duplication the researcher instructions warn against. Per the
2026-08-11 precedent (`research/2026-08-11-context-editing-preview.md`, filed
when the backlog was similarly drained), I filed a new item instead of
re-touching a stranded one. I picked Skills because `knowledge/agent-skills.md`
flags the API surface as a known gap ("**API / claude.ai Skills** run in a
sandboxed code-execution container... need the `code-execution` tool + the
`skills-2025-10-02` beta header") that has sat unverified since 2026-08-06 —
and because my own fresh docs fetch today contradicts that claim (see
Findings §3), which is exactly the kind of docs-vs-reality gap this lab has
repeatedly found worth nailing down live (`knowledge/claude-code-mcp-connection.md`,
`knowledge/agent-skills.md`'s own `${CLAUDE_SKILL_DIR}` finding).

No `claude-api` skill is installed in this environment (`~/.claude/skills/`
only has `graphify`; the `synced/` bucket has `docs`/`docx`/`mcp-builder`/
`pdf`/etc., no `claude-api`). I fetched Anthropic's actual open-source
`claude-api` skill directly from `github.com/anthropics/skills` instead
(raw file, not a search snippet) to get its API-drift table and model-id
guidance, per the instruction to consult that skill rather than trust memory.

## Findings

### 1. Two distinct Skills products share the `SKILL.md` format

Confirmed again today, matching what `knowledge/agent-skills.md` already
recorded: **Claude API / claude.ai Skills** run inside Anthropic's own
sandboxed code-execution container and are uploaded via `/v1/skills`;
**Claude Code Skills** are pure filesystem objects Claude Code discovers under
`~/.claude/skills/` or `.claude/skills/`, no upload, no container. Every
`examples/skill-*` directory in this repo is the second kind. Source:
[Agent Skills overview](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview)
(fetched 2026-09-28): "The Claude API supports both pre-built Agent Skills and
custom Skills... specify the relevant `skill_id` in the `container` parameter
along with the code execution tool... **Prerequisites:** Using Skills through
the API requires the code execution tool, whose container Skills run in."
Also: "Skills on the API run in a sandboxed container with **no network
access and no runtime package installation**."

### 2. The exact wire shape, from the current docs (fetched 2026-09-28)

Upload a custom Skill — [Using Agent Skills with the API](https://platform.claude.com/docs/en/build-with-claude/skills-guide):

```bash
curl -X POST "https://api.anthropic.com/v1/skills" \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -F "files[]=@financial_skill/SKILL.md;filename=financial_skill/SKILL.md" \
  -F "files[]=@financial_skill/analyze.py;filename=financial_skill/analyze.py"
```

```python
from anthropic.lib import files_from_dir
client = anthropic.Anthropic()
skill = client.skills.create(files=files_from_dir("financial_skill"))
# skill.id -> "skill_01AbCdEfGhIjKlMnOpQrStUv"
# skill.latest_version_id
```

Use it in a request — same page, verbatim:

```python
response = client.messages.create(
    model="claude-opus-5-5",
    max_tokens=4096,
    container={
        "skills": [
            {"type": "custom", "skill_id": "skill_01AbCdEfGhIjKlMnOpQrStUv", "version": "latest"}
        ]
    },
    messages=[{"role": "user", "content": "Generate and process a large sample dataset"}],
    tools=[{"type": "code_execution_20250825", "name": "code_execution"}],
)
```

Note `client.messages.create` (not `.beta.messages.create`) and
`client.skills.create` (not `.beta.skills.create`) — plain, non-beta client
methods, no `anthropic-beta` header in either example on this page.

Pre-built Anthropic Skills use `"type": "anthropic"` with short IDs (`pptx`,
`xlsx`, `docx`, `pdf`) and date-versioned `version` strings instead of a
generated `skill_id`.

`SKILL.md` frontmatter is **server-validated** on upload — same two required
fields and constraints Claude Code uses informally: `name` (≤64 chars,
lowercase/digits/hyphens only, no XML tags, can't contain `"anthropic"` or
`"claude"` as a substring), `description` (non-empty, ≤1024 chars, no XML
tags). Max upload 30 MB uncompressed. Source: [Agent Skills overview](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview),
"Skill structure" section, fetched 2026-09-28 — matches
`knowledge/agent-skills.md`'s existing frontmatter table, so this part of the
prior note holds up.

Skill lifecycle beyond create (found via search, **not yet fetched from a
primary page directly** — my direct `WebFetch` on
`platform.claude.com/docs/en/api/skills-create` 404'd): `GET /v1/skills`
(list), `POST /v1/skills/{skill_id}/versions` (new version),
`GET/DELETE /v1/skills/{skill_id}/versions/{version}`, and
`DELETE /v1/skills/{skill_id}` ("removes every version with the Skill").
Treat the exact request/response field names here as **unconfirmed** until
the builder reads the live reference page directly — see Open questions.

### 3. A real docs-vs-reality gap on beta headers — not yet resolved live

`knowledge/agent-skills.md` (written 2026-08-05/06) states the API surface
needs "the `code-execution` tool + the `skills-2025-10-02` beta header." A
[Spring AI blog post](https://spring.io/blog/2026/01/28/apring-ai-anthropic-agentic-skills/)
dated 2026-01-28 corroborates that this used to be true and names **three**
betas: `skills-2025-10-02`, `code-execution-2025-08-25`,
`files-api-2025-04-14`.

But every current official page I fetched today (2026-09-28) shows **no beta
header at all** in its request examples:

- [Code execution tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/code-execution-tool)
  (redirected from the stale `docs.anthropic.com` URL), "Tool versions"
  section, verbatim: *"None of the three tool versions requires an
  `anthropic-beta` header. The legacy code execution beta headers remain
  valid opt-ins."* — and its "Upgrade to latest tool version" table lists
  the **legacy** beta header as `code-execution-2025-05-22`, marked "Current:
  None required."
- [Skills guide](https://platform.claude.com/docs/en/build-with-claude/skills-guide)
  — no `anthropic-beta` line in either the upload or the messages.create
  example (confirmed by asking specifically for verbatim beta-header
  mentions; none were found on the page).
- The real `anthropics/skills` `claude-api` SKILL.md
  ([raw source](https://raw.githubusercontent.com/anthropics/skills/main/skills/claude-api/SKILL.md),
  cache-dated 2026-06-24 inside the file, fetched today), its own API-drift
  table: *"Files API / Skills | `client.beta.files.*` / `client.beta.skills.*`
  with beta `files-api-2025-04-14` / `skills-2025-10-02` | **Out of beta**:
  `client.files.*` / `client.skills.*`, no beta header."*

So three independent, dated sources agree the three betas from the
January 2026 blog post graduated to GA sometime before this skill's
2026-06-24 cache and are still GA as of today. **The one inconsistency**: the
same `claude-api` SKILL.md, later in its own body (line 560), tells the model
to call `client.beta.messages.create` with the `code-execution-2025-08-25`
beta specifically for Skills — contradicting its own drift table two
paragraphs earlier. This reads like a stale prose paragraph the file's
maintainers didn't update when the drift-table row was added, not a real
requirement — but I have not made a live call to settle it, and no example in
this lab has ever exercised this surface. That's the live-verification gap
this cycle's build proposal targets.

### 4. Response shape and pricing (code-execution-tool docs, fetched 2026-09-28)

- Claude reads a Skill's `SKILL.md` the same way it reads any container file:
  a `server_tool_use` block naming `bash_code_execution` or
  `text_editor_code_execution`, paired with a
  `bash_code_execution_tool_result` / `text_editor_code_execution_tool_result`
  block. A `bash` read looks like `{"command": "cat skill_name/SKILL.md"}` →
  `{"stdout": "...", "stderr": "", "return_code": 0, "content": []}`. This is
  the literal, checkable signal that a Skill's body was actually loaded, not
  just its name+description (which sit in the system prompt from the
  `container.skills` list, "Level 1" cost, no proof of a real read).
- Top-level `container: {"id": ..., "expires_at": ...}` on the response;
  passing that `id` back as the top-level `container` request field reuses
  the same sandbox (out of scope this cycle — see below).
- Pricing: code execution is billed by execution time, **not** part of
  token pricing. Minimum billing unit 5 minutes; **1,550 free hours per
  organization per month**; $0.05/hour beyond that. A single short call in
  this lab is effectively free unless the org has already burned its monthly
  free-hours elsewhere (no other example in this lab touches code execution,
  so that's unlikely). Token costs for the `messages.create` call itself are
  billed normally regardless.

### 5. What's explicitly out of scope for one day's increment

- **Pre-built Anthropic Skills** (`pptx`/`xlsx`/`docx`/`pdf`) — a different,
  larger surface (real document generation); the custom-skill path alone is
  enough to prove the mechanism.
- **Container reuse** across multiple requests / **programmatic tool
  calling** / REPL state persistence (`code_execution_20260120` or later) —
  a real follow-up, not needed to prove "Claude reads a custom Skill from the
  API."
- **claude.ai skill upload**, **enterprise content scanning**, **Claude
  Platform on AWS / Microsoft Foundry** parity — different surfaces this
  cycle doesn't touch.
- Full CRUD (`list`/`versions`/`delete`) beyond what's needed to upload once
  and clean up after the one live call.

## Build proposal

**What**: `examples/skill-api-container/` — upload one minimal custom Skill
through the real `/v1/skills` endpoint, invoke it through a live
`messages.create()` call using `container.skills` + the code execution tool,
and prove from the response transcript that Claude actually read `SKILL.md`
out of the sandbox rather than answering from context alone. Corrects
`knowledge/agent-skills.md`'s untested beta-header claim with what the live
call actually needs.

**Intent**: prove the API-hosted Skills wire contract end-to-end and record
whatever it actually requires, the way `examples/skill-permission-suppression`
proved the `${CLAUDE_SKILL_DIR}` claim live instead of trusting the docs.
Out of scope: everything in Findings §5.

**Behavioral spec**:

- *Inputs*: a fixture Skill directory,
  `examples/skill-api-container/skills/hello-skill/SKILL.md`, with valid
  frontmatter (`name`/`description` per the constraints in §2) and a body
  containing one **canary string** not derivable from general knowledge (e.g.
  an arbitrary codeword the instructions say to include verbatim in any
  reply). The canary is what proves the body was read, not just the
  name/description matched.
- *Process*:
  1. A pure validator, `validate_skill_dir(path) -> None`, raising
     `ValueError` with a specific message per violated constraint (bad
     `name` pattern/length/reserved substring, empty/too-long `description`,
     XML tags in either) — runs before any network call. Boundary validation
     per repo Protocol §4; same constraints `knowledge/agent-skills.md`
     already documents for Claude Code Skills, now enforced because the API
     enforces them server-side too.
  2. A live-run script that: validates the fixture, uploads it
     (`client.skills.create`), calls `client.messages.create` with
     `container={"skills": [{"type": "custom", "skill_id": ..., "version":
     "latest"}]}` and `tools=[{"type": "code_execution_20250825", "name":
     "code_execution"}]` and a prompt that should trigger the Skill, saves
     the full raw response JSON as a dated transcript file, then deletes the
     Skill (`client.skills.delete`) in a `finally` block. Logs the created
     `skill_id` *before* the messages.create call so a human can clean up
     manually if a later step raises — the one realistic partial-failure
     mode (create succeeds, something after it doesn't) per Protocol §4's
     "state failure modes" rule.
  3. A pure parser, `skill_was_loaded(response_json: dict) -> bool`, that
     scans `content` for a `bash_code_execution_tool_result` or
     `text_editor_code_execution_tool_result` block whose paired
     `server_tool_use` input references the Skill's `SKILL.md` path, **and**
     confirms the canary string appears in the final assistant text.
- *Outputs*: the saved live transcript (dated filename); a boolean verdict
  from `skill_was_loaded`; corrected knowledge-base text once the live run
  settles the beta-header question from Findings §3.
- *Invariants*: the validator never makes a network call; nothing in the
  example hardcodes `ANTHROPIC_API_KEY` (read once at the script's entry
  point per repo Protocol §3).
- *Failure modes*: missing API key → fail loudly before any request; invalid
  fixture frontmatter → `ValueError` from the validator, no upload attempted;
  Skill uploads but never fires (`skill_was_loaded` returns `False`) → the
  script must report this as a clear, non-silent failure of the live check,
  not a passing test — the canary-string design exists specifically so a
  "the model just guessed a plausible answer" false positive isn't possible.

**Interfaces** (builder fills in bodies):

```python
def validate_skill_dir(path: Path) -> None: ...
    """Raises ValueError with the specific violated constraint; makes no I/O beyond reading local files."""

def skill_was_loaded(response_json: dict, skill_md_filename: str, canary: str) -> bool: ...
    """Pure function; no network. True iff a code-execution tool_use/tool_result
    pair references skill_md_filename AND canary appears in the final text."""
```

**Tests** (offline, no key, no network — same fixture-replay discipline as
`examples/readme-transcript-check` and the streaming-accumulator examples):

- `validate_skill_dir` accepts a well-formed fixture; rejects each documented
  violation individually (uppercase/too-long/`"claude"`-containing `name`;
  empty/too-long/XML-tagged `description`) with a distinct assertion per
  case.
- `skill_was_loaded` returns `True` against a hand-built fixture JSON shaped
  like the documented `bash_code_execution_tool_result` response (§4) whose
  `stdout`/command references the Skill's `SKILL.md` and whose final text
  block contains the canary; returns `False` against a second fixture where
  the model answers without any code-execution block, and a third where the
  code-execution block exists but the canary is absent (proves the check
  isn't satisfied by tool use alone).

**"It works" means**: both offline test files pass; the one live run
produces a saved transcript showing a genuine `SKILL.md` read plus the canary
string, `skill_was_loaded` returns `True` against that real transcript (not
just the synthetic fixtures), and the created Skill is deleted afterward
(verify via `client.skills.list()` returning empty, or just checking the
delete call didn't raise). README states the live run's real cost plainly —
one `messages.create` call's token cost, code-execution time almost certainly
inside the monthly free-hours allowance — the same disclosure style as
`examples/skill-permission-suppression/README.md` and
`examples/mcp-connect-claude-code/run_e2e.sh`. README also records whatever
the live call actually needed on the beta-header question (Findings §3), and
`knowledge/agent-skills.md`'s API-Skills line gets corrected with that
result, the same docs-vs-reality discipline
`knowledge/claude-code-mcp-connection.md` already established.

## Open questions

- Exact request/response shapes for `GET /v1/skills`,
  `POST /v1/skills/{id}/versions`, and `DELETE /v1/skills/{id}` are known
  only from search-result summaries, not a direct fetch of the reference
  page (`platform.claude.com/docs/en/api/skills-create` 404'd for me). The
  builder should hit the live reference page directly (try
  `platform.claude.com/docs/en/api/beta/skills` or navigate from
  `/docs/en/api/overview`) before writing the create/delete calls, rather
  than trusting the search snippet's paraphrase.
- Does the live call actually need zero beta headers (per today's docs), or
  does the `claude-api` skill's own line 560 reflect something the public
  docs pages haven't caught up to? This is exactly what the live run in the
  build proposal settles — don't guess, verify.
- "Up to 20 Skills per request" was reported by a search-result summary
  (not fetched directly) — irrelevant to this cycle's one-Skill increment,
  but worth a source check if a future cycle builds multi-skill composition.
- Whether the free 1,550 code-execution hours/month are organization-wide
  and shared with any other billing activity on this lab's account is not
  something I can verify from docs alone — the README should say the run is
  cheap, not claim it's free.

## Sources

- [Agent Skills overview](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview) — fetched 2026-09-28
- [Using Agent Skills with the API (skills-guide)](https://platform.claude.com/docs/en/build-with-claude/skills-guide) — fetched 2026-09-28
- [Code execution tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/code-execution-tool) — fetched 2026-09-28 (redirected from `docs.anthropic.com`)
- [`anthropics/skills` `claude-api` SKILL.md, raw](https://raw.githubusercontent.com/anthropics/skills/main/skills/claude-api/SKILL.md) — fetched 2026-09-28, internal cache date 2026-06-24
- [Spring AI: Anthropic Agent Skills support](https://spring.io/blog/2026/01/28/apring-ai-anthropic-agentic-skills/) — dated 2026-01-28
- `knowledge/agent-skills.md` (this repo) — written 2026-08-05/06, the note this cycle corrects
