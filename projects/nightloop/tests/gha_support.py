"""Shared builders for the milestone-10 GitHub Actions generator tests (not a test module).

``GOLDEN_FIELDS`` plus ``examples/plugin.nightloop.toml`` is what
``examples/nightloop.workflow.yml`` was rendered from. ``block`` reads the
rendered text by indentation, so structure checks need no YAML parser.
"""

from __future__ import annotations

from pathlib import Path

from nightloop.config import load_config
from nightloop.model import Config
from nightloop.schedule_gha import Credential, ValidSpec, WorkflowSpec, render_workflow, validate_spec

PROJECT = Path(__file__).resolve().parent.parent
PLUGIN_CONFIG = PROJECT / "examples" / "plugin.nightloop.toml"
GOLDEN = PROJECT / "examples" / "nightloop.workflow.yml"
GOLDEN_FIELDS: dict[str, object] = dict(
    nightloop_repo="chunkychonker/agentlab",
    nightloop_sha="fc4f54d69076443f0cfca76357baa8754bab4c41",
    claude_version="2.1.330",
    credential=Credential.OAUTH_TOKEN,
    timezone="America/Chicago",
    hour=None,
    minute=17,
    timeout_s=3600,
    job_timeout_minutes=360,
    config_path="examples/plugin.nightloop.toml",
    workflow_stem="nightloop",
)
# The CLI flags that give GOLDEN_FIELDS (with --repo PROJECT --config PLUGIN_CONFIG).
GOLDEN_FLAGS = (
    "--nightloop-repo", "chunkychonker/agentlab",
    "--nightloop-sha", "fc4f54d69076443f0cfca76357baa8754bab4c41",
    "--claude-version", "2.1.330",
    "--credential", "oauth-token",
    "--timezone", "America/Chicago",
    "--minute", "17",
)


def make_spec(**overrides: object) -> WorkflowSpec:
    fields = dict(GOLDEN_FIELDS)
    fields.update(overrides)
    return WorkflowSpec(**fields)  # type: ignore[arg-type]


def plugin_config() -> Config:
    return load_config(PLUGIN_CONFIG)


def valid(cfg: Config | None = None, **overrides: object) -> ValidSpec:
    return validate_spec(cfg if cfg is not None else plugin_config(), make_spec(**overrides))


def render(cfg: Config | None = None, **overrides: object) -> str:
    return render_workflow(valid(cfg, **overrides))


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def block(text: str, header: str) -> list[str]:
    """The lines nested under the one line equal to ``header`` (indent included), blank lines dropped.

    Raises ``AssertionError`` unless exactly one line equals ``header``.
    """
    lines = text.split("\n")
    hits = [i for i, line in enumerate(lines) if line == header]
    if len(hits) != 1:
        raise AssertionError(f"expected exactly one {header!r} line, found {len(hits)}")
    start, depth = hits[0], _indent(header)
    body: list[str] = []
    for line in lines[start + 1:]:
        if line.strip() == "":
            continue
        if _indent(line) <= depth:
            break
        body.append(line)
    return body


def steps(text: str) -> list[list[str]]:
    """Each step under ``jobs.night.steps`` as its own list of lines (the ``- `` line first)."""
    found: list[list[str]] = []
    for line in block(text, "    steps:"):
        if line.startswith("      - "):
            found.append([line])
        else:
            found[-1].append(line)
    return found


def step_named(text: str, name: str) -> list[str]:
    matches = [s for s in steps(text) if s[0] == f"      - name: {name}"]
    if len(matches) != 1:
        raise AssertionError(f"expected one step named {name!r}, found {len(matches)}")
    return matches[0]
