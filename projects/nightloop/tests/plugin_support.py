"""Shared paths and helpers for the milestone-8 plugin tests (not a test module)."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

PROJECT = Path(__file__).resolve().parent.parent
PLUGIN = PROJECT / "plugin"
GUARD = PLUGIN / "hooks" / "publish_guard.py"
RUN_SILENT = PLUGIN / "bin" / "run_silent"
PLUGIN_CONFIG = PROJECT / "examples" / "plugin.nightloop.toml"
AGENTS = PLUGIN / "agents"

# macOS's system interpreter, which `python3` on PATH may be when Claude Code
# runs the hook. The plugin's scripts promise 3.9 compatibility; tests that
# check it skip when this interpreter is absent.
SYSTEM_PYTHON = Path("/usr/bin/python3")

# Bound for any one subprocess in these tests; far above their real run time.
SUBPROCESS_TIMEOUT_S = 30


def load_guard() -> ModuleType:
    """Import publish_guard.py by path, without writing a __pycache__ into the plugin."""
    spec = importlib.util.spec_from_file_location("nightloop_test_publish_guard", GUARD)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def run_guard(stdin: str, *, python: str = sys.executable) -> subprocess.CompletedProcess[str]:
    """Run the guard script as Claude Code would, with ``stdin`` as the payload."""
    return subprocess.run(
        [python, "-I", "-B", str(GUARD)],
        input=stdin, capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT_S,
    )


def copy_plugin(dest_parent: Path) -> Path:
    """A copy of the plugin under ``dest_parent``, modes kept, no __pycache__; returns its root."""
    dest = dest_parent / "plugin"
    shutil.copytree(PLUGIN, dest, ignore=shutil.ignore_patterns("__pycache__"))
    return dest


def system_python_differs() -> bool:
    """True if the system interpreter exists and is not the one running the tests."""
    return SYSTEM_PYTHON.exists() and os.path.realpath(SYSTEM_PYTHON) != os.path.realpath(sys.executable)
