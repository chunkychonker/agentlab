"""Live demo (billed): the same .env write under acceptEdits, once per gate layout.

Each layout runs in its own fresh temp directory, which is also the session's
working directory. The script asks the model to Write ``<cwd>/.env``, then
checks the filesystem. What the docs predict:

  callback-only  acceptEdits auto-approves Writes inside cwd before
                 can_use_tool is consulted -> .env is written, callback silent
  hook           the PreToolUse hook runs first and denies -> no .env

See the research note this came from:
    research/2026-11-03-agent-sdk-permission-gate.md

Offline self-test (no key, no network):  python test_gates.py
Live run (billed):                        python agent.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import anyio
from claude_agent_sdk import AssistantMessage, ResultError, ResultMessage, ToolUseBlock, query

from blind_spots import ModeledMode
from gates import AuditEntry, build_gates
from options import Layout, build_options
from policy import PROTECTED_BASENAME

API_KEY_ENV = "ANTHROPIC_API_KEY"
PERMISSION_MODE: ModeledMode = "acceptEdits"
PROMPT_TEMPLATE = (
    "Use the Write tool to create the file {path} containing the single line "
    "DEMO=1. Do not use Bash. If the write is refused, reply with the refusal "
    "reason verbatim and stop."
)

EXIT_MATCHES_DOCS = 0
EXIT_RUN_FAILED = 1
EXIT_NO_KEY = 2
EXIT_DOCS_CONTRADICTED = 3
EXIT_USAGE = 4


@dataclass(frozen=True)
class ScenarioResult:
    layout: Layout
    file_written: bool
    tool_calls: tuple[str, ...]
    audit: tuple[AuditEntry, ...]
    final_text: str | None
    is_error: bool


async def run_scenario(layout: Layout, workdir: Path) -> ScenarioResult:
    """Run one layout in ``workdir`` and report what actually happened on disk.

    Raises:
        CLINotFoundError: the bundled Claude Code CLI could not be found.
        ProcessError: the CLI crashed without reporting a result.
        ResultError: the CLI exited non-zero before yielding a ResultMessage.
    """
    audit: list[AuditEntry] = []
    options = build_options(build_gates(audit.append), layout, PERMISSION_MODE, workdir)
    target = workdir / PROTECTED_BASENAME
    tool_calls: list[str] = []
    result: ResultMessage | None = None
    try:
        async for message in query(prompt=PROMPT_TEMPLATE.format(path=target), options=options):
            if isinstance(message, AssistantMessage):
                tool_calls += [b.name for b in message.content if isinstance(b, ToolUseBlock)]
            elif isinstance(message, ResultMessage):
                result = message
    except ResultError:
        # The CLI reports an error result and then exits non-zero; keep the
        # result we already hold, re-raise if there is none.
        if result is None:
            raise
    if result is None:
        raise RuntimeError("the CLI stream ended without a ResultMessage")
    return ScenarioResult(
        layout=layout,
        file_written=target.exists(),
        tool_calls=tuple(tool_calls),
        audit=tuple(audit),
        final_text=result.result,
        is_error=result.is_error,
    )


def matches_docs(callback_only: ScenarioResult, hook: ScenarioResult) -> bool:
    """The documented prediction: only the hook layout stops the write. Pure."""
    return callback_only.file_written and not hook.file_written


def _print(result: ScenarioResult) -> None:
    print(f"== layout={result.layout.value} permission_mode={PERMISSION_MODE}")
    print(f"   tool_calls:   {list(result.tool_calls)}")
    print(f"   .env written: {result.file_written}")
    if not result.audit:
        print("   gate audit:   (no gate was consulted)")
    for entry in result.audit:
        print(f"   gate audit:   {entry.checkpoint.value} {entry.tool_name} -> {entry.verdict}")
    print(f"   is_error:     {result.is_error}")
    print(f"   final text:   {result.final_text!r}")


async def _run_both() -> tuple[ScenarioResult, ScenarioResult]:
    results = []
    for layout in (Layout.CALLBACK_ONLY, Layout.HOOK_AND_CALLBACK):
        with tempfile.TemporaryDirectory(prefix=f"gate-{layout.value}-") as tmp:
            results.append(await run_scenario(layout, Path(tmp).resolve()))
    return results[0], results[1]


def main(argv: Sequence[str], environ: Mapping[str, str]) -> int:
    """Entry point. Requires ``ANTHROPIC_API_KEY`` so the bill goes to an explicit key.

    Exit codes: 0 reality matches the docs, 1 a run ended on an error result,
    2 no key (nothing runs), 3 reality contradicts the docs, 4 bad usage.
    ``CLINotFoundError`` / ``ProcessError`` propagate with a traceback.
    """
    if argv:
        print("usage: python agent.py   (takes no arguments)", file=sys.stderr)
        return EXIT_USAGE
    if not environ.get(API_KEY_ENV):
        print(
            f"{API_KEY_ENV} is not set; refusing the live (billed) run.\n"
            "Run 'python test_gates.py' for the offline self-test.",
            file=sys.stderr,
        )
        return EXIT_NO_KEY
    callback_only, hook = anyio.run(_run_both)
    _print(callback_only)
    _print(hook)
    if callback_only.is_error or hook.is_error:
        return EXIT_RUN_FAILED
    if matches_docs(callback_only, hook):
        print("\nMatches the docs: only the PreToolUse hook stopped the write.")
        return EXIT_MATCHES_DOCS
    print("\nCONTRADICTS the docs: record this in the README.", file=sys.stderr)
    return EXIT_DOCS_CONTRADICTED


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:], os.environ))
