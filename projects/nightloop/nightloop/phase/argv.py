"""Builds the ``claude -p`` command line for one phase.

Pure: no I/O. The flag set mirrors agentlab's live runner
(``.pipeline/run.sh`` ``run_phase`` plus ``.pipeline/phase_tools.sh``), which
runs this combination nightly.

Two rules shape the order:

- The prompt comes straight after ``-p``. ``--allowedTools`` is variadic: it
  takes every following word up to the next flag, so a prompt placed after it
  would be swallowed as one more rule.
- ``--allowedTools`` comes last, one rule per argv element, so nothing can
  follow it by accident.

Never emitted: ``--bare`` (it skips hooks passed via ``--settings`` and
flag-loaded plugins and ignores OAuth; milestone 7's publish guard is a hook)
and ``--dangerously-skip-permissions`` (every phase runs under ``dontAsk``).
"""

from __future__ import annotations

from .request import PhaseRequest

CLI_PROGRAM = "claude"
PRINT_FLAG = "-p"
PERMISSION_MODE = "dontAsk"
OUTPUT_FORMAT = "json"
ALLOWED_TOOLS_FLAG = "--allowedTools"


def build_argv(req: PhaseRequest) -> tuple[str, ...]:
    """Return the full argv for ``req``, starting with ``CLI_PROGRAM``.

    The prompt is always ``argv[2]``. Never raises: ``PhaseRequest`` and
    ``Phase`` have already validated every value used here.
    """
    phase = req.phase
    return (
        CLI_PROGRAM,
        PRINT_FLAG,
        req.prompt,
        "--model",
        phase.model,
        "--output-format",
        OUTPUT_FORMAT,
        "--permission-mode",
        PERMISSION_MODE,
        "--agent",
        phase.agent,
        ALLOWED_TOOLS_FLAG,
        *phase.allowed_tools,
    )
