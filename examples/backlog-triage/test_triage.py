"""Offline self-test for the backlog triage classifier. Stdlib only, no network, no key.

Run:
    python3 test_triage.py

Each test is one acceptance criterion from
research/2026-10-25-backlog-stale-health-triage.md, asserted against the public
surface (`classify`, `triage`, `apply`, the CLI), never against internals. The
fixture is the real `## Health-check findings` section of this repo's
BACKLOG.md as committed on 2026-10-25.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

from backlog_lines import apply, parse_unclaimed
from classify import Actionable, Historical, Kind, classify
from triage import triage

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "fixtures" / "health-findings-2026-10-25.md"
CLI = HERE / "triage.py"
README = HERE / "README.md"
TODAY = date(2026, 10, 25)

GAP_LINE = (
    "- [ ] fix (health 2026-09-02): no run log for 2026-08-17 through 2026-08-28 — 12 "
    "consecutive scheduled nights with no logs/run-*.log present. logs/pause-resume.log "
    "records a manual pause/resume dated 2026-08-18/19; whether the nightly job ran on any "
    "of these dates cannot be det..."
)
RUN_LINE = (
    "- [ ] fix (health 2026-10-23): run-2026-10-22_020001 — external:tardyai opened 0/1, "
    '"VERIFY: FAIL (1): routes stay in corridors — cycle 1 does not ship"'
)
DRIFT_LINE = "- [ ] fix (health 2020-01-01): examples/foo/ — README transcript drift (seen 2020-01-01)"


def _read_fixture() -> str:
    with FIXTURE.open(encoding="utf-8", newline="") as handle:
        return handle.read()


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *args], capture_output=True, text=True, timeout=30, check=False
    )


def test_unloggable_gap_night_is_historical() -> None:
    verdict = classify(GAP_LINE, 1, TODAY)
    assert isinstance(verdict, Historical), verdict
    assert verdict.kind is Kind.GAP_NIGHT
    assert verdict.newest == date(2026, 8, 28), verdict.newest
    print("ok  the 2026-08-17..28 'no run log' finding is a historical gap-night, newest 2026-08-28")


def test_recent_run_outcome_respects_threshold() -> None:
    fresh = classify(RUN_LINE, 1, TODAY, max_age_days=7)
    assert isinstance(fresh, Actionable) and fresh.kind is Kind.RUN_OUTCOME, fresh
    assert fresh.newest == date(2026, 10, 22), "the run id's own date must count"
    stale = classify(RUN_LINE, 1, TODAY, max_age_days=2)
    assert isinstance(stale, Historical) and stale.kind is Kind.RUN_OUTCOME, stale
    print("ok  a 3-day-old run outcome is actionable at max age 7 and historical at max age 2")


def test_file_subject_is_never_historical() -> None:
    for today in (TODAY, date(2099, 1, 1)):
        verdict = classify(DRIFT_LINE, 1, today, max_age_days=0)
        assert isinstance(verdict, Actionable) and verdict.kind is Kind.OTHER, verdict
    # The illegal state itself cannot be built.
    try:
        Historical(1, Kind.OTHER, TODAY, "r", "t")
    except ValueError:
        pass
    else:
        raise AssertionError("Historical(kind=OTHER) must be unrepresentable")
    print("ok  an examples/ transcript-drift finding is OTHER and never historical, however old")


def test_non_health_line_is_never_touched() -> None:
    text = "## Coding agents\n- [ ] Some feature from 2020-01-01 — 9x\n"
    verdicts = triage(text, TODAY, max_age_days=0)
    assert len(verdicts) == 1 and isinstance(verdicts[0], Actionable), verdicts
    assert verdicts[0].reason == "not a health finding"
    assert apply(text, verdicts, TODAY) == text
    print("ok  a non-health '- [ ] ' item is always actionable and apply leaves it byte-identical")


def test_dateless_kind_is_kept() -> None:
    line = "- [ ] fix (health 2026-01-01): phase 'health' exited non-zero — 2x (session-limit)"
    verdict = classify(line, 1, TODAY, max_age_days=0)
    assert isinstance(verdict, Actionable) and verdict.kind is Kind.PHASE_EXIT, verdict
    assert verdict.newest is None
    print("ok  a recognised kind with no date in its body stays actionable (fail safe)")


def test_apply_is_prefix_only_and_idempotent() -> None:
    old = "- [ ] fix (health 2026-09-15): no run log for 2026-09-04"
    text = (
        "## Health-check findings\r\n"
        "- [done #12] fix (health 2026-09-15): no run log for 2026-09-01\n"
        "- [researching] fix (health 2026-09-15): no run log for 2026-09-02\n"
        "  - [ ] fix (health 2026-09-15): no run log for 2026-09-03\n"
        f"{old}\r\n"
        "  continuation text that mentions 2026-01-01\n"
        "- [ ] fix (health 2026-09-15): phase 'health' exited non-zero — 3x: 2026-09-08"
    )
    once = apply(text, triage(text, TODAY), TODAY)
    before, after = text.split("\n"), once.split("\n")
    assert len(before) == len(after)
    changed = [i for i, (b, a) in enumerate(zip(before, after)) if b != a]
    assert changed == [4, 6], changed
    assert after[4] == "- [done by hand 2026-10-25: stale gap-night] " + old[len("- [ ] "):] + "\r"
    for i in changed:
        marker_end = after[i].index("] ") + 2
        assert after[i][marker_end:] == before[i][len("- [ ] "):], "body must be untouched"
    assert triage(once, TODAY) == [], "no unclaimed lines remain"
    assert apply(once, triage(once, TODAY), TODAY) == once
    # Verdicts computed against other text are refused, not misapplied.
    try:
        apply(once, triage(text, TODAY), TODAY)
    except ValueError:
        pass
    else:
        raise AssertionError("stale verdicts must raise")
    print("ok  apply rewrites only the marker, keeps CRLF, skips [done #N]/[researching]/indented, is idempotent")


def test_fixture_counts_are_pinned() -> None:
    text = _read_fixture()
    verdicts = triage(text, TODAY)
    historical = [v for v in verdicts if isinstance(v, Historical)]
    actionable = [v for v in verdicts if isinstance(v, Actionable)]
    assert len(parse_unclaimed(text)) == 47 == len(verdicts)
    assert len(actionable) == 9, len(actionable)
    per_kind = {k: sum(v.kind is k for v in historical) for k in Kind}
    assert per_kind == {
        Kind.GAP_NIGHT: 9,
        Kind.RUN_OUTCOME: 17,
        Kind.PHASE_EXIT: 6,
        Kind.REPEATED_MESSAGE: 6,
        Kind.OTHER: 0,
    }, per_kind
    print("ok  real backlog fixture: 47 raw unclaimed, 38 historical, 9 actionable at 2026-10-25")


def test_markers_stay_compatible_with_backlog_sh() -> None:
    text = _read_fixture()
    verdicts = triage(text, TODAY)
    closed = {v.text[len("- [ ] "):] for v in verdicts if isinstance(v, Historical)}
    out = apply(text, verdicts, TODAY)
    # backlog_has_unresolved: grep '^- \[' | grep -v '^- \[done '
    still_queued = [l for l in out.split("\n") if l.startswith("- [") and not l.startswith("- [done ")]
    assert not any(body in l for l in still_queued for body in closed)
    # backlog_count_unclaimed: grep -c '^- \[ \] '
    assert sum(l.startswith("- [ ] ") for l in out.split("\n")) == 9
    # the health check's PR-number test: grep -c '\[done #'
    def lines_with_done_pr(t: str) -> int:
        return sum("[done #" in l for l in t.split("\n"))

    assert lines_with_done_pr(out) == lines_with_done_pr(text) == 15
    print("ok  closed lines leave backlog_has_unresolved, count as 9 unclaimed, and add no '[done #'")


def test_cli_dry_run_apply_and_errors() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "BACKLOG.md"
        shutil.copyfile(FIXTURE, copy)
        original = copy.read_bytes()

        dry = _run_cli(str(copy), "--today", "2026-10-25")
        assert dry.returncode == 0 and "dry run: pass --apply to close 38" in dry.stdout, dry
        assert copy.read_bytes() == original, "dry run must write nothing"

        bad = _run_cli(str(copy), "--today", "2026-13-45", "--apply")
        assert bad.returncode == 2 and "not a YYYY-MM-DD date" in bad.stderr, bad
        assert copy.read_bytes() == original

        missing = _run_cli(str(Path(tmp) / "nope.md"), "--today", "2026-10-25")
        assert missing.returncode == 2 and "cannot read" in missing.stderr, missing

        first = _run_cli(str(copy), "--today", "2026-10-25", "--apply")
        assert first.returncode == 0 and "applied: closed 38 item(s)" in first.stdout, first
        after_first = copy.read_bytes()
        assert after_first != original
        assert sorted(p.name for p in Path(tmp).iterdir()) == ["BACKLOG.md"], "no temp file left"

        second = _run_cli(str(copy), "--today", "2026-10-25", "--apply")
        assert second.returncode == 0 and "nothing to apply" in second.stdout, second
        assert copy.read_bytes() == after_first, "second --apply must change nothing"
    print("ok  CLI: dry run writes nothing, bad input exits 2, --apply closes 38 then is a no-op")


def test_readme_cli_block_is_real_output() -> None:
    readme = README.read_text(encoding="utf-8")
    marker = "CLI output on the committed fixture:\n\n```\n"
    start = readme.index(marker) + len(marker)
    documented = readme[start : readme.index("```\n", start)]
    # Run from this directory with the relative path, exactly as the README says.
    actual = subprocess.run(
        [sys.executable, "triage.py", "fixtures/health-findings-2026-10-25.md", "--today", "2026-10-25"],
        cwd=HERE, capture_output=True, text=True, timeout=30, check=True,
    )
    assert documented == actual.stdout, "README CLI block drifted from real output"
    print("ok  the README's CLI block is byte-identical to the real CLI output")


def main() -> int:
    tests = [
        test_unloggable_gap_night_is_historical,
        test_recent_run_outcome_respects_threshold,
        test_file_subject_is_never_historical,
        test_non_health_line_is_never_touched,
        test_dateless_kind_is_kept,
        test_apply_is_prefix_only_and_idempotent,
        test_fixture_counts_are_pinned,
        test_markers_stay_compatible_with_backlog_sh,
        test_cli_dry_run_apply_and_errors,
        test_readme_cli_block_is_real_output,
    ]
    for test in tests:
        test()
    print(f"\nAll {len(tests)} self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
