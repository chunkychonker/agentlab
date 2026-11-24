"""The ``nightloop bench`` subcommand: the reviewer benchmark's imperative shell.

Reads the config, the fixtures, the template and the results file; puts the
plugin's ``bin/`` first on PATH (so the reviewer finds ``run_silent``);
builds the runner and the workspace; prints progress, the report, and
writes the results file. ``main`` imports this module only for ``bench``.
Everything it decides with is pure (``nightloop.bench`` and ``bench_text``).
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path
from typing import Callable

from ..bench.fixture import FixtureError, MatrixError, require_both_kinds
from ..bench.fresh import check_fresh, recorded_sha256, template_sha256
from ..bench.load import load_fixtures
from ..bench.ports import TrialWorkspace
from ..bench.report import exit_code, render_report, summarise, to_json
from ..bench.run import review_request, run_bench
from ..bench.workspace import TempGitWorkspace, agent_path
from ..config import ConfigError, load_config
from ..phase.argv import build_argv
from ..phase.execute import Gate
from ..phase.runner import Runner
from .bench_args import PLUGIN_BIN
from .bench_text import render_freshness, render_matrix, render_trial
from .exit_codes import ExitCode

PROG = "nightloop"
# Shown in the dry-run argv in place of a temp repo that does not exist yet.
PLACEHOLDER_REPO = Path("/TEMP/nightloop-bench-REPO")
_FRESHNESS_EXIT = {"FRESH": ExitCode.OK, "STALE": ExitCode.BENCH_STALE, "NEVER_RUN": ExitCode.BENCH_NEVER_RUN}

WorkspaceFactory = Callable[[bytes, str], TrialWorkspace]


def run_bench_cmd(
    args: argparse.Namespace,
    *,
    clock: Callable[[], float],
    runner_factory: Callable[[], Runner],
    workspace_factory: WorkspaceFactory,
    gate: Gate,
    verdict_path: Path,
    plugin_bin: Path = PLUGIN_BIN,
) -> int:
    """Run ``bench`` and return an ``ExitCode``.

    ``--check-fresh``: ``OK`` / ``BENCH_STALE`` / ``BENCH_NEVER_RUN`` for a
    results file recorded for this template / another one / no file.
    ``--dry-run``: prints the matrix and ``OK``; builds no runner or
    workspace and writes nothing. Otherwise: runs every trial, prints the
    report, and returns ``UNGRADED`` if any trial was ungraded (the results
    file is then left as it was) or ``OK`` after writing the results file.

    ``USAGE`` (one line on stderr, nothing run): the config does not load,
    a fixture is invalid, the fixtures lack a seeded or a clean one, the
    template or the results file cannot be read or is malformed, or
    ``plugin_bin`` has no executable ``run_silent`` (not checked for
    ``--dry-run``/``--check-fresh``). ``ENVIRONMENT``: ``git`` or ``claude``
    could not be started, or a trial repo could not be built. ``OSError``
    from writing the results file propagates.
    """
    try:
        template = args.template.read_bytes()
    except OSError as exc:
        return _usage(f"--template {args.template}: {exc}")
    if args.check_fresh:
        return _check_fresh(template, args.results, args.template)
    try:
        cfg = load_config(args.config)
        fixtures = load_fixtures(args.fixtures)
        require_both_kinds(fixtures)
    except ConfigError as exc:
        return _usage(f"config {args.config}: {exc}")
    except (FixtureError, MatrixError) as exc:
        return _usage(f"--fixtures {args.fixtures}: {exc}")
    sha = template_sha256(template)
    agent = cfg.review.agent
    if args.dry_run:
        req = review_request(cfg, repo=PLACEHOLDER_REPO, verdict_path=verdict_path, timeout_s=args.timeout_s)
        print(render_matrix(
            fixtures, trials=args.trials, template=str(args.template), template_sha256=sha,
            agent_file=str(agent_path(agent)), argv=build_argv(req),
            placeholder_repo=str(PLACEHOLDER_REPO), path_prefix=str(plugin_bin),
        ))
        return ExitCode.OK
    run_silent = plugin_bin / "run_silent"
    if not (run_silent.is_file() and os.access(run_silent, os.X_OK)):
        return _usage(f"{run_silent} is missing or not executable; the reviewer runs its tests through it")
    _prepend_path(plugin_bin)
    try:
        results = run_bench(
            fixtures, cfg,
            runner=runner_factory(), workspace=workspace_factory(template, agent),
            trials=args.trials, timeout_s=args.timeout_s, verdict_path=verdict_path,
            now=clock, gate=gate,
            on_trial=lambda r: _progress(render_trial(r, trials=args.trials)),
        )
    except OSError as exc:
        _error(f"environment error, benchmark stopped: {exc}")
        return ExitCode.ENVIRONMENT
    report = summarise(results, template_sha256=sha, trials=args.trials)
    print(render_report(report))
    code = exit_code(report)
    if code != ExitCode.OK:
        _error(f"results not recorded in {args.results}: {report.ungraded_count} ungraded trial(s)")
        return ExitCode.UNGRADED
    _write_atomic(args.results, to_json(report))
    print(f"recorded {args.results}")
    return ExitCode.OK


def _check_fresh(template: bytes, results: Path, template_path: Path) -> int:
    try:
        text = results.read_text(encoding="utf-8")
    except FileNotFoundError:
        recorded = None
    except (OSError, UnicodeDecodeError) as exc:
        return _usage(f"--results {results}: {exc}")
    else:
        try:
            recorded = recorded_sha256(text)
        except ValueError as exc:
            return _usage(f"--results {results}: {exc}")
    state = check_fresh(template, recorded)
    print(render_freshness(state, results=str(results), template=str(template_path)))
    return _FRESHNESS_EXIT[state.name]


def _prepend_path(directory: Path) -> None:
    """Put ``directory`` first on this process's PATH; the runner's children inherit it."""
    current = os.environ.get("PATH", "")
    parts = current.split(os.pathsep) if current else []
    if not parts or parts[0] != str(directory):
        os.environ["PATH"] = os.pathsep.join([str(directory), *parts])


def _write_atomic(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` via a temp file and ``os.replace``; no temp file is left on failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def _usage(message: str) -> int:
    _error(message)
    return ExitCode.USAGE


def _error(message: str) -> None:
    print(f"{PROG}: {message}", file=sys.stderr)


def _progress(text: str) -> None:
    print(f"{PROG}: {text}", file=sys.stderr, flush=True)


__all__ = ["PLACEHOLDER_REPO", "TempGitWorkspace", "WorkspaceFactory", "run_bench_cmd"]
