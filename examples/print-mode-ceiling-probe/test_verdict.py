"""Offline tests for the pure core (verdict.py). Stdlib only, no key, no network."""

from __future__ import annotations

from pathlib import Path

from verdict import (
    ENV_VAR,
    Bounded,
    DefaultCeiling,
    Invalid,
    Off,
    TruncationFinding,
    check_ceiling,
    find_truncation,
)

OUTPUTS = Path(__file__).resolve().parent / "fixtures" / "outputs"


def test_ceiling_classes() -> None:
    assert check_ceiling({ENV_VAR: "0"}) == Off()
    assert check_ceiling({}) == DefaultCeiling()
    assert check_ceiling({"PATH": "/usr/bin"}) == DefaultCeiling()
    assert check_ceiling({ENV_VAR: "1200000"}) == Bounded(1_200_000)
    print("ok  \"0\" is OFF, unset is DEFAULT_600S, a positive integer is BOUNDED")


def test_unset_and_empty_are_distinct() -> None:
    assert check_ceiling({ENV_VAR: ""}) == Invalid("")
    assert check_ceiling({ENV_VAR: ""}) != check_ceiling({})
    print("ok  an empty value is INVALID, never confused with unset")


def test_non_canonical_values_are_invalid() -> None:
    for raw in ("off", "-1", " 0", "0 ", "00", "600s", "1e3", "false"):
        assert check_ceiling({ENV_VAR: raw}) == Invalid(raw), raw
    print("ok  non-canonical values (off, -1, ' 0', 00, 600s) are INVALID")


def test_truncation_found_in_real_log_text() -> None:
    finding = find_truncation((OUTPUTS / "truncated.txt").read_text())
    assert finding == TruncationFinding(waited_seconds=600, line_number=2), finding
    print("ok  the verbatim 600s kill line is found, with its line number")


def test_clean_output_has_no_finding() -> None:
    assert find_truncation((OUTPUTS / "clean.txt").read_text()) is None
    assert find_truncation("") is None
    print("ok  clean output yields None, not a default finding")


def test_non_default_ceiling_seconds_are_read() -> None:
    text = "ok\nBackground tasks still running after 1200s; terminating.\n"
    assert find_truncation(text) == TruncationFinding(waited_seconds=1200, line_number=2)
    print("ok  a non-default ceiling's seconds are read from the line")


TESTS = [
    test_ceiling_classes,
    test_unset_and_empty_are_distinct,
    test_non_canonical_values_are_invalid,
    test_truncation_found_in_real_log_text,
    test_clean_output_has_no_finding,
    test_non_default_ceiling_seconds_are_read,
]


if __name__ == "__main__":
    for test in TESTS:
        test()
    print(f"\nAll {len(TESTS)} self-tests passed.")
