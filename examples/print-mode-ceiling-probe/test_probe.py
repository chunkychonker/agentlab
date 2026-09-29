"""Offline tests for the launcher probe (probe.py) and the scan CLI.

Real subprocesses, real `/bin/sh` launchers, a real stub `claude` on PATH.
No key, no network, no real Claude Code.
"""

from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path

import scan_output
from probe import ProbeError, main as probe_main, probe
from verdict import ENV_VAR, Bounded, DefaultCeiling, Invalid, Off, check_ceiling

HERE = Path(__file__).resolve().parent
LAUNCHERS = HERE / "fixtures" / "launchers"
OUTPUTS = HERE / "fixtures" / "outputs"

# Deliberately poisoned: if the probe leaked the caller's value through, every
# "missing" launcher would read OFF and the tests below would fail.
BASE_ENV = {**os.environ, ENV_VAR: "0"}


def _verdicts(name: str) -> list:
    invocations = probe(["/bin/sh", str(LAUNCHERS / name)], HERE, BASE_ENV)
    return [check_ceiling(i.env) for i in invocations]


def test_good_launchers_propagate_off() -> None:
    assert _verdicts("good_export.sh") == [Off()]
    assert _verdicts("env_i_allowlist_good.sh") == [Off()]
    print("ok  export and allowlisted env -i both deliver OFF to claude")


def test_missing_and_scrubbed_launchers_get_default() -> None:
    assert _verdicts("missing.sh") == [DefaultCeiling()]
    assert _verdicts("env_i_allowlist_bad.sh") == [DefaultCeiling()]
    print("ok  missing export and env -i without it both leave the 600s default")


def test_grep_for_export_line_is_fooled() -> None:
    text = (LAUNCHERS / "env_i_allowlist_bad.sh").read_text()
    assert f"export {ENV_VAR}=0" in text
    assert _verdicts("env_i_allowlist_bad.sh") != [Off()]
    print("ok  the bad env -i launcher passes a grep yet claude never sees =0")


def test_subshell_and_empty_value() -> None:
    assert _verdicts("set_after_subshell.sh") == [DefaultCeiling()]
    assert _verdicts("empty_value.sh") == [Invalid("")]
    print("ok  a subshell-only export is DEFAULT_600S, an empty value INVALID")


def test_every_invocation_is_recorded_in_order() -> None:
    invocations = probe(["/bin/sh", str(LAUNCHERS / "two_phases_one_missing.sh")], HERE, BASE_ENV)
    assert [i.argv[1:] for i in invocations] == [("-p", "research phase"), ("-p", "review phase")]
    assert [check_ceiling(i.env) for i in invocations] == [Off(), DefaultCeiling()]
    print("ok  each claude call is recorded in order; one bad phase is visible")


def test_unobserved_launchers_raise() -> None:
    for name, expected in (
        ("never_calls_claude.sh", "never invoked"),
        ("scrubbed_path.sh", "exited 127"),
    ):
        try:
            probe(["/bin/sh", str(LAUNCHERS / name)], HERE, BASE_ENV)
        except ProbeError as exc:
            assert expected in str(exc), (name, str(exc))
        else:
            raise AssertionError(f"{name}: expected ProbeError, got a result")
    print("ok  a launcher that never reaches bare claude raises ProbeError")


def test_probe_cli_exit_codes() -> None:
    cases = (
        ("good_export.sh", 0),
        ("two_phases_one_missing.sh", 1),
        ("never_calls_claude.sh", 2),
    )
    saved = os.environ.get(ENV_VAR)
    os.environ[ENV_VAR] = "0"  # the CLI must strip this too
    try:
        for name, code in cases:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                got = probe_main(["/bin/sh", str(LAUNCHERS / name)])
            assert got == code, (name, got)
    finally:
        if saved is None:
            del os.environ[ENV_VAR]
        else:
            os.environ[ENV_VAR] = saved
    print("ok  probe CLI exits 0 only if every call saw OFF; 1 if not; 2 if unobserved")


def test_scan_cli_exit_codes() -> None:
    with contextlib.redirect_stdout(io.StringIO()) as out:
        assert scan_output.main([str(OUTPUTS / "clean.txt")]) == 0
        assert scan_output.main([str(OUTPUTS / "clean.txt"), str(OUTPUTS / "truncated.txt")]) == 1
    assert "truncated.txt:2  killed after 600s" in out.getvalue()
    with contextlib.redirect_stderr(io.StringIO()):
        assert scan_output.main([str(OUTPUTS / "does-not-exist.txt")]) == 2
    print("ok  scan CLI: clean 0, truncated 1, unreadable 2 (never 'clean')")


def test_bounded_value_passes_through() -> None:
    invocations = probe(
        ["/bin/sh", "-c", f"{ENV_VAR}=1200000 claude -p x"], HERE, BASE_ENV
    )
    assert [check_ceiling(i.env) for i in invocations] == [Bounded(1_200_000)]
    print("ok  a launcher-set finite ceiling arrives as BOUNDED")


TESTS = [
    test_good_launchers_propagate_off,
    test_missing_and_scrubbed_launchers_get_default,
    test_grep_for_export_line_is_fooled,
    test_subshell_and_empty_value,
    test_every_invocation_is_recorded_in_order,
    test_unobserved_launchers_raise,
    test_probe_cli_exit_codes,
    test_scan_cli_exit_codes,
    test_bounded_value_passes_through,
]


if __name__ == "__main__":
    for test in TESTS:
        test()
    print(f"\nAll {len(TESTS)} self-tests passed.")
