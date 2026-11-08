# nightloop m1: `nightloop.toml` schema and loader

Mode: project:nightloop, milestone 1 (Config). Date: 2026-11-08.

## Question

What should `nightloop.toml` contain, and how should the loader fail, so that a
bad config is rejected at load with the offending field named, using only the
Python standard library?

## Findings

**tomllib (stdlib, Python 3.11+).** Source: [docs.python.org tomllib](https://docs.python.org/3/library/tomllib.html), read 2026-11-08.
- `tomllib.load(fp)` needs a *binary* file object (`open(p, "rb")`); `loads(str)` takes text. Both return a plain `dict` and raise `TOMLDecodeError` (a `ValueError` subclass).
- The `lineno`/`colno`/`pos` attributes on `TOMLDecodeError` were only added in **3.14**. The project floor is 3.11, so the loader must wrap with `str(exc)` and not depend on those attributes. The docs do not specify the `str()` format.
- TOML 1.1 support only arrives in 3.15, so a schema should use TOML 1.0 syntax only.
- The docs warn that parsing untrusted input can burn CPU/memory. Config is repo-owned, but the loader should still cap file size (proposed: 64 KiB) before parsing. That is cheap.
- tomllib returns `bool` for TOML booleans and `int` for integers, but `bool` is a subclass of `int` in Python. `cycles = true` passes an `isinstance(x, int)` check, so the validator must reject bools explicitly.

**What goes in `allowed_tools`.** Source: [Claude Code permissions docs](https://code.claude.com/docs/en/permissions), read 2026-11-08. The strings are passed to `claude -p --allowedTools`, so the loader should catch rules that Claude Code would silently ignore. Verified facts from the page:
- `Bash(git log *)` is the current prefix form, and `Bash(git log:*)` is equivalent. The `:*` form is only recognised at the end of a pattern. Elsewhere the colon is literal.
- Allow rules accept tool-name globs only after a literal `mcp__<server>__` prefix. An unanchored allow glob such as `"*"`, `"B*"` or `"mcp__*"` is **skipped and approves nothing**.
- A settings-file `mcp__` rule with parentheses is skipped. The docs state this for settings files. Whether `--allowedTools` behaves the same is unconfirmed, so the loader rejects it conservatively.
- A `*` before the subcommand (`Bash(git * main)`) is accepted but emits a startup warning, and it matches far more than it looks like (`git -c ...`). That is an overbroad allowlist, so the loader rejects it.
- Bare `Bash` / `Bash(*)` allows every command. In an unattended `dontAsk` loop that defeats the point of an allowlist, so the loader requires an explicit opt-in field (`allow_unrestricted_bash = true`) and otherwise rejects it.
- `dontAsk` mode auto-denies anything that would prompt unless pre-approved, which is why per-phase `allowed_tools` is a required field and not optional.
- Compound commands (`&&`, `;`, `|`) require every subcommand to be allowed. So a rule list is per command, not per script.

**Reference values from agentlab's live pipeline** (read from this repo, not the web):
- `.pipeline/schedule.sh`: window `WINDOW_START_HOUR=1`, `WINDOW_END_HOUR=6`, start inclusive and end **exclusive** (a job that starts at 06:00 runs into the morning). The schema should carry the same semantics.
- `.pipeline/cycles`: a positive integer, parsed as `^[1-9][0-9]*$` in `run.sh:429`.
- `.pipeline/postcondition.sh` defines two real postconditions, `research_note_fresh` and `increment_built`, plus `phase_no_postcondition`. The review phase is gated by `verdict.sh`, which is a verdict parse (milestone 2) and not an artifact check.
- `run.sh:656-659` uses `sonnet` for research and `opus` for build, and the agents' own frontmatter pins `tools:` lists.

**Design consequence.** Postconditions are a closed set of names in the config and are implemented in milestone 2. Config only validates the name, so the pure loader has no dependency on the gates. A name unknown to the enum fails at load, which is the same fail-closed posture as the bash gates.

No HN search was needed. This is a stdlib schema task and there is no library to judge.

## Build proposal

Directory: `projects/nightloop/` (extends the plan, not `examples/`). Nothing exists there yet beyond `PLAN.md`. `gh pr list --state open` shows only #77 (pipeline metrics), so there is no collision.

### 1. Intent
Provide a pure `parse_config(text) -> Config` and a thin `load_config(path)` for `nightloop.toml`, rejecting any invalid value at load with a `ConfigError` that names the field path. Out of scope: running anything, the gates themselves (m2), CLI (m6), writing config, env-var reads, `[tool]` defaults inheritance, JSON/YAML.

### 2. Behavioral spec
Layout (all keys below are the whole schema; **unknown keys at any level are an error**, since a typo like `cylces` must not silently default):

```toml
schema_version = 1            # required, must equal 1

[repo]
backlog = "BACKLOG.md"        # required, relative path, no "..", not absolute

[run]
cycles = 1                    # required, int >= 1, not bool
window_start_hour = 1         # required, int 0..23
window_end_hour = 6           # required, int 1..24, must be > start (end exclusive)

[phase.research]              # phases: research, build, review (required, exactly these)
agent = "agentlab-researcher" # required, non-empty, matches ^[a-z0-9][a-z0-9-]*$
model = "sonnet"              # required, non-empty string (alias or full id; not checked against a list)
postcondition = "research_note_fresh"   # required, one of the Postcondition enum
allowed_tools = ["Read", "Write", "Bash(git log *)"]  # required, non-empty list of unique non-empty strings
allow_unrestricted_bash = false  # optional, default false
```

Postcondition enum: `research_note_fresh`, `increment_built`, `review_verdict`, `none`. Each phase has one allowed subset (research: `research_note_fresh`/`none`; build: `increment_built`/`none`; review: `review_verdict` only, since the review must never be ungated).

`allowed_tools` rules, each reported with the index, e.g. `phase.build.allowed_tools[2]`:
- reject `"*"`, any string with a `*` in the tool-name position (before any `(`), and `mcp__*` unanchored. `mcp__<server>__*` is allowed.
- reject any `mcp__` entry containing `(`.
- reject `Bash` / `Bash(*)` unless `allow_unrestricted_bash = true`.
- reject a `Bash(...)` pattern whose first `*` comes before the first space-delimited subcommand word (the `Bash(git * main)` shape).
- a `:*` suffix is accepted only at the end of the pattern.

Errors: `ConfigError(field: str, message: str)`. `str(e)` is `"<field>: <message>"`. TOML syntax errors use field `"<toml>"` and carry `str(TOMLDecodeError)`. Missing file and size over 64 KiB raise `ConfigError` with field `"<file>"`. The only exception type the module raises is `ConfigError`.

Invariants: `Config` and nested types are frozen dataclasses (tuples, not lists), so a loaded config cannot be mutated. `parse_config` does no I/O, reads no env and no clock. Loading is idempotent. Parsing the same text twice yields equal values.

Acceptance criteria (each is a test):
1. A fully valid file, matching agentlab's real values (cycles 1, window 1..6, three phases), loads and the fields round-trip.
2. For each required key, deleting it raises `ConfigError` with `.field` equal to that key's dotted path.
3. Unknown key at top level, in `[run]`, and in a phase each raise with that key's path.
4. `cycles` of `0`, `-1`, `true`, `"2"`, `1.0` each fail with field `run.cycles`.
5. `window_end_hour <= window_start_hour`, start `24`, end `0`, end `25` each fail naming the right field. End `24` with start `1` is valid.
6. Phase table missing, or an extra phase such as `phase.deploy`, fails.
7. `review` with `postcondition = "none"` fails. `build` with `review_verdict` fails.
8. Each allowlist rule above has one reject test and one accept test. Examples: reject `"*"`, `"mcp__*"`, `"mcp__x__tool(arg)"`, `"Bash"`, `"Bash(git * main)"`. Accept `"mcp__hn-search__*"`, `"Bash(git log *)"`, `"Bash(git log:*)"`, and `"Bash"` with the opt-in set.
9. Bad TOML (`cycles = `) raises `ConfigError` with field `<toml>`, never a raw `TOMLDecodeError`. A missing file and a 65 KiB file each raise `ConfigError`.
10. Mutation attempts on a loaded `Config` raise `FrozenInstanceError`. `parse_config` is called with a bytes-like input and fails with `ConfigError`, not `TypeError`.

### 3. Interfaces (no bodies)
```python
# projects/nightloop/nightloop/config.py   (public surface: these 6 names)
class ConfigError(ValueError):
    field: str
class Postcondition(enum.Enum): ...   # the 4 values above
@dataclass(frozen=True) class Window:  start_hour: int; end_hour: int
@dataclass(frozen=True) class Phase:   agent: str; model: str; postcondition: Postcondition; allowed_tools: tuple[str, ...]; allow_unrestricted_bash: bool
@dataclass(frozen=True) class Config:  backlog: str; cycles: int; window: Window; research: Phase; build: Phase; review: Phase
def parse_config(text: str) -> Config: ...      # pure
def load_config(path: Path) -> Config: ...      # reads <= 64 KiB, then parse_config
```
Suggested file split (one reason to change each): `errors.py`, `model.py` (dataclasses and enum), `tool_rules.py` (pure allowlist rule checks), `config.py` (`parse_config`, `load_config`). Tests in `projects/nightloop/tests/test_config.py` use `unittest` (stdlib only, decision 2026-11-06), run with `python3 -I -m unittest discover` from `projects/nightloop/`. Ship an `examples/agentlab.nightloop.toml` mirroring the live pipeline, and a test that loads it. `pyproject.toml` with `requires-python = ">=3.11"` and no dependencies.

Failure domain: a library module only. If it is wrong, later milestones reject good configs or accept bad ones, and nothing runs. No effect on the live `.pipeline/`.

## Open questions
- Whether `--allowedTools` (CLI flag) ignores `mcp__` rules with parentheses the way settings files do. Docs say settings files only. The loader rejects them conservatively. A live check is a human step per the plan.
- The `str(TOMLDecodeError)` format on 3.11 to 3.13 is unspecified, so tests assert only the field name and not the message text.
- Whether `review` should be allowed a model other than opus is a policy choice. Left open: the schema does not constrain it.
- Whether a fourth `maintain` phase belongs in the schema. The live pipeline has one, but the plan's milestone text lists research, build and review. Left out and can be added later with an expand step.
