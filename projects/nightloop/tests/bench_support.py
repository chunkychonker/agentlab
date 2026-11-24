"""Shared builders for the milestone-11 benchmark tests (not a test module).

The fake reviewer is a ``FakeRunner`` step that writes the verdict file in
the trial repo it is given, then reports a successful CLI run, so every
trial goes through the real ``run_phase`` and the real ``evaluate`` gate.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping
from unittest import mock

from nightloop.bench.fixture import Fixture, Kind
from nightloop.config import Config, load_config
from nightloop.phase.fake_runner import cli_success
from nightloop.phase.request import PhaseRequest
from nightloop.phase.runner import RawRun

PROJECT = Path(__file__).resolve().parent.parent
PLUGIN_CONFIG = PROJECT / "examples" / "plugin.nightloop.toml"
SHIPPED_FIXTURES = PROJECT / "bench" / "fixtures"
REVIEWER_TEMPLATE = PROJECT / "plugin" / "agents" / "reviewer.md"
VERDICT = Path("logs") / "last-review.md"
TEMPLATE = b"---\nname: reviewer\ndescription: test reviewer\n---\nReview it.\n"


def plugin_config() -> Config:
    return load_config(PLUGIN_CONFIG)


def fixture(slug: str, kind: Kind, patterns: tuple[str, ...] = (), *,
            files: Mapping[str, bytes] | None = None, note: str = "# note\n\n## Build proposal\n") -> Fixture:
    if files is None:
        files = {"code.py": f"# {slug}\n".encode()}
    return Fixture(slug=slug, kind=kind, patterns=patterns, note=note,
                   files={PurePosixPath(k): v for k, v in files.items()})


def write_fixture_dir(root: Path, slug: str, *, expect: str, note: str = "# note\n",
                      files: Mapping[str, str] | None = None) -> Path:
    """Write ``<root>/<slug>/`` with the given ``expect.toml`` text, note and increment files."""
    d = root / slug
    (d / "increment").mkdir(parents=True)
    (d / "expect.toml").write_text(expect, encoding="utf-8")
    (d / "note.md").write_text(note, encoding="utf-8")
    for rel, text in (files if files is not None else {"code.py": "x = 1\n"}).items():
        path = d / "increment" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return d


def writes_verdict(text: str) -> Callable[[PhaseRequest], RawRun]:
    """A FakeRunner step: the reviewer writes ``text`` to the trial repo's verdict file and exits 0."""
    def step(req: PhaseRequest) -> RawRun:
        path = req.cwd / VERDICT
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return cli_success("reviewed")
    return step


def exits_nonzero(req: PhaseRequest) -> RawRun:
    """A FakeRunner step: the CLI failed before writing any verdict."""
    return RawRun(returncode=1, stdout="", stderr="boom", timed_out=False)


def tree_digest(root: Path) -> str:
    """Every path, mode, size, mtime and content under ``root``, skipping bytecode caches."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if "__pycache__" in path.parts:
            continue
        digest.update(str(path.relative_to(root)).encode())
        st = path.lstat()
        digest.update(str((st.st_mode, st.st_size, st.st_mtime_ns)).encode())
        if path.is_file() and not path.is_symlink():
            digest.update(path.read_bytes())
    return digest.hexdigest()


FAIL = "VERDICT: FAIL\nRan: run_silent tests -- python3 -m unittest\nFindings:\n- {}\n"
PASS = "VERDICT: PASS\nRan: run_silent tests -- python3 -m unittest\nFindings:\n- No blockers.\n"


class IsolatedGitCase(unittest.TestCase):
    """Gives each test ``self.tmp`` (resolved temp dir) with git's user/system config kept out."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.enterContext(mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}))
