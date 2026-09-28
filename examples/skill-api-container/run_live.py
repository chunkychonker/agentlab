"""Live run (imperative shell): upload the Skill, call Claude with it, save the transcript, delete the Skill.

Everything here is I/O. The decisions it relies on live in the pure modules:
`frontmatter.parse_skill_md`, `experiment.check_experiment_design`, and
`load_evidence.load_verdict`.

Run:
    pip install -r requirements.txt
    export ANTHROPIC_API_KEY=sk-ant-...
    python run_live.py

No key -> prints SKIPPED and exits 0 without touching the network.

Beta headers: this script deliberately sends NO `anthropic-beta` header and
uses the plain `client.skills.*` / `client.messages.create` methods (not
`client.beta.*`). If the calls succeed, that settles the open question in the
research note: the Skills API needs no beta header.

Exit codes: 0 = LOADED (or SKIPPED, no key); 1 = ran, but the transcript
does not prove the Skill body was read (see printed verdict). Anything else
raises with a traceback.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from experiment import CANARY, PROMPT, SKILL_DIR_NAME, SKILL_MD_FILENAME, check_experiment_design
from frontmatter import parse_skill_md
from load_evidence import Verdict, load_verdict

# Cheapest verified current model that supports the code execution tool. See
# knowledge/anthropic-models.md. One constant so switching tiers is one line.
MODEL = "claude-sonnet-5"
MAX_TOKENS = 2048
CODE_EXECUTION_TOOL = {"type": "code_execution_20250825", "name": "code_execution"}

HERE = Path(__file__).resolve().parent
SKILL_DIR = HERE / "skills" / SKILL_DIR_NAME
TRANSCRIPT_DIR = HERE / "transcripts"


def main() -> int:
    """Entry point. The only place the environment is read.

    Failure modes:
        - Missing ANTHROPIC_API_KEY: prints SKIPPED, returns 0, and makes no
          request.
        - Invalid SKILL.md or leaky canary design: raises before any upload.
        - Upload fails: raises. Nothing was created, so there is nothing to
          clean up.
        - Upload succeeds but a later step fails: the skill id has already
          been printed to stderr. The `finally` block still deletes the Skill.
          If that delete also fails, both tracebacks are shown and the id in
          stderr is what you delete by hand.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print(
            "SKIPPED (not a pass): ANTHROPIC_API_KEY is not set, so the live check was NOT run.\n"
            "Run 'python3 -m unittest discover -v' for the offline self-test."
        )
        return 0

    skill = parse_skill_md((SKILL_DIR / SKILL_MD_FILENAME).read_text(encoding="utf-8"))
    check_experiment_design(skill, PROMPT, CANARY)

    import anthropic  # lazy: the offline self-test must not need the SDK
    from anthropic.lib import files_from_dir

    client = anthropic.Anthropic(api_key=api_key)
    created = client.skills.create(files=files_from_dir(SKILL_DIR))
    print(f"created skill_id={created.id} (delete this by hand if cleanup below fails)", file=sys.stderr)
    try:
        verdict = _call_and_judge(client, created.id)
    finally:
        _delete_skill(client, created.id)
        print(f"deleted skill_id={created.id}", file=sys.stderr)

    print(f"verdict: {verdict.name} ({verdict.value})")
    return 0 if verdict is Verdict.LOADED else 1


def _call_and_judge(client, skill_id: str) -> Verdict:
    """Make the one messages.create call, save the raw response, then judge it.

    Raises:
        anthropic.APIError: the request failed. No transcript is written.
        RuntimeError: stop_reason is 'pause_turn'. The transcript IS saved
            first. Continuing a paused server-tool turn is out of scope here.
        load_evidence.TranscriptError: the response is malformed (saved first).
    """
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        container={"skills": [{"type": "custom", "skill_id": skill_id, "version": "latest"}]},
        tools=[CODE_EXECUTION_TOOL],
        messages=[{"role": "user", "content": PROMPT}],
    )
    raw = response.model_dump(mode="json")
    path = _save_transcript(raw)
    print(f"transcript saved: {path.relative_to(HERE)}")
    if raw.get("stop_reason") == "pause_turn":
        raise RuntimeError("stop_reason=pause_turn; continuation is out of scope. See the saved transcript.")
    for block in raw["content"]:
        if block.get("type") == "text":
            print(f"claude: {block.get('text', '').strip()}")
    return load_verdict(raw, SKILL_MD_FILENAME, CANARY)


def _save_transcript(raw: dict) -> Path:
    """Write the response JSON to transcripts/<UTC timestamp>-live.json. Raises OSError on write failure."""
    TRANSCRIPT_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    path = TRANSCRIPT_DIR / f"{stamp}-live.json"
    path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    return path


def _delete_skill(client, skill_id: str) -> None:
    """Delete every version, then the Skill itself.

    Versions are deleted first because the SDK docstring for skills.delete
    does not say whether it cascades. Deleting versions first works either
    way. Raises anthropic.APIError on any failed delete. If it fails partway,
    some versions may already be gone and the Skill still exists.
    """
    # Materialize first so deleting doesn't shift the pagination cursor underneath us.
    version_ids = [v.id for v in client.skills.versions.list(skill_id)]
    for version_id in version_ids:
        client.skills.versions.delete(version_id, skill_id=skill_id)
    client.skills.delete(skill_id)


if __name__ == "__main__":
    raise SystemExit(main())
