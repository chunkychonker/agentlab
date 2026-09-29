"""Run a launcher against a stub `claude` and report the environment it received.

This is the imperative shell: it owns the temp directory, the subprocess and the
filesystem. The classification itself lives in ``verdict.py``.

How it works: a temp directory gets an executable named ``claude`` that records
its argv and environment as JSON and exits 0. That directory is prepended to
``PATH`` and the launcher is run. Every time the launcher (or anything it
spawns) invokes bare ``claude``, one record is written. The output path is baked
into the stub's source rather than passed through an environment variable,
because the launchers under test are exactly the ones that scrub the
environment.

CLI::

    python3 probe.py <launcher> [args...]

prints one verdict line per recorded ``claude`` invocation. Exit 0 only if every
invocation saw ``OFF``; 1 if any did not; 2 if the probe itself failed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from verdict import ENV_VAR, Off, check_ceiling

STUB_NAME = "claude"
DEFAULT_TIMEOUT_S = 30.0

EXIT_ALL_OFF = 0
EXIT_NOT_OFF = 1
EXIT_PROBE_FAILED = 2

_STUB_TEMPLATE = """#!{python}
import json, os, sys
records = {records!r}
n = 0
while True:
    path = os.path.join(records, "%04d.json" % n)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        n += 1
        continue
    with os.fdopen(fd, "w") as fh:
        json.dump({{"argv": sys.argv, "env": dict(os.environ)}}, fh)
    break
"""


class ProbeError(Exception):
    """The probe could not observe a `claude` invocation.

    Raised instead of reporting "unset", because "the launcher never reached the
    stub" and "the launcher reached it without the variable" are different
    findings.
    """


@dataclass(frozen=True)
class Invocation:
    """One recorded call of the stub: its argv and the environment it saw."""

    argv: tuple[str, ...]
    env: Mapping[str, str]


def probe(
    launcher: Sequence[str],
    workdir: Path,
    base_env: Mapping[str, str],
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> tuple[Invocation, ...]:
    """Run ``launcher`` in ``workdir`` with a stub `claude` first on PATH.

    ``base_env`` is the environment the launcher starts from, minus ``ENV_VAR``:
    the variable is always stripped, so the result reflects what the launcher
    itself sets, not what the caller happened to inherit. ``PATH`` is the stub
    directory followed by ``base_env["PATH"]`` (if any).

    Returns every recorded invocation in call order; never empty.

    Failure modes (all ``ProbeError``; the temp directory is removed in every
    case, so a retry starts clean):
    * the launcher exits non-zero -- e.g. it scrubbed PATH and `claude` was not
      found (exit 127). Records written before the failure are discarded.
    * the launcher exits 0 but never invoked bare `claude` -- e.g. it used an
      absolute path to a real binary.
    * the launcher cannot be started (``OSError``) or exceeds ``timeout_s``.
    """
    if not launcher:
        raise ProbeError("launcher command is empty")

    with tempfile.TemporaryDirectory(prefix="ceiling-probe-") as tmp:
        bin_dir = Path(tmp) / "bin"
        records_dir = Path(tmp) / "records"
        bin_dir.mkdir()
        records_dir.mkdir()
        stub = bin_dir / STUB_NAME
        stub.write_text(_STUB_TEMPLATE.format(python=sys.executable, records=str(records_dir)))
        stub.chmod(0o755)

        env = {k: v for k, v in base_env.items() if k != ENV_VAR}
        inherited_path = base_env.get("PATH")
        env["PATH"] = str(bin_dir) if not inherited_path else f"{bin_dir}{os.pathsep}{inherited_path}"

        try:
            completed = subprocess.run(
                list(launcher),
                cwd=workdir,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            raise ProbeError(f"launcher did not finish within {timeout_s}s") from exc
        except OSError as exc:
            raise ProbeError(f"could not start launcher {launcher[0]!r}: {exc}") from exc

        if completed.returncode != 0:
            tail = completed.stderr.strip().splitlines()[-3:]
            raise ProbeError(
                f"launcher exited {completed.returncode}; stderr tail: {' | '.join(tail) or '(empty)'}"
            )

        records = sorted(records_dir.glob("*.json"))
        if not records:
            raise ProbeError(
                "launcher exited 0 but never invoked bare `claude` -- it used an "
                "absolute path or a different binary, so nothing was observed"
            )
        return tuple(_load(path) for path in records)


def _load(path: Path) -> Invocation:
    data = json.loads(path.read_text())
    return Invocation(argv=tuple(data["argv"]), env=dict(data["env"]))


def _format(invocation: Invocation) -> str:
    verdict = check_ceiling(invocation.env)
    shown = " ".join([STUB_NAME, *invocation.argv[1:]])
    return f"{verdict.label:<12} {verdict!r:<28} {shown}"


def main(argv: Sequence[str]) -> int:
    if not argv:
        print("usage: python3 probe.py <launcher> [args...]", file=sys.stderr)
        return EXIT_PROBE_FAILED
    try:
        invocations = probe(argv, Path.cwd(), dict(os.environ))
    except ProbeError as exc:
        print(f"PROBE FAILED  {exc}", file=sys.stderr)
        return EXIT_PROBE_FAILED
    for invocation in invocations:
        print(_format(invocation))
    all_off = all(isinstance(check_ceiling(i.env), Off) for i in invocations)
    return EXIT_ALL_OFF if all_off else EXIT_NOT_OFF


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
