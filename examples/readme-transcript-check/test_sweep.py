"""Offline self-test for the repo-wide transcript sweep. Stdlib only, no network.

Run:
    python3 test_sweep.py

Each test is one acceptance criterion from the research note's behavioral spec
(research/2026-09-09-transcript-check-sweep.md), asserted against the public
surface - `command_script`, `plan_target`, `summarize`, `build_interpreter`,
`run_sweep` - never against internals.

Two tests pin choices the note left to the builder:

  * `test_the_directive_must_sit_above_the_marker` fixes the opt-out directive's
    placement (above the marker line, not between the marker and the fence).
  * `test_build_interpreter_reuses_a_ready_venv` fixes that virtualenvs are
    cached by requirements content rather than rebuilt per example.

The load-bearing one is `test_end_to_end_sweeps_two_real_examples`: it sweeps
two real example directories in this repo with no fixtures at all. The note
named `minimal-agent-loop` as one of the pair; it is swapped for `skill-anatomy`
here because `minimal-agent-loop` ships a `requirements.txt` for its *live* run,
so sweeping it would build a virtualenv and hit the network - which criterion 11
forbids. `minimal-agent-loop` is still covered end to end, by
`test_check_transcript.py` and transitively by the sweep of this directory.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import types
from pathlib import Path

import check_transcript as ct
import sweep

HERE = Path(__file__).resolve().parent
EXAMPLES_DIR = HERE.parent

# The reason string used by the opt-out fixtures, and asserted verbatim.
FIXTURE_REASON = "billed live-API run, not reproducible offline"

# A README in this repo's ordinary shape: prose, a run block, the transcript.
FIXTURE_README = """# Fixture example

Some prose about the example.

```bash
cd examples/fixture
python test_x.py
```

Expected output:

```
ok  alpha

All 1 self-tests passed.
```
"""

# The `mcp-connect-claude-code` shape: the block above the transcript runs a
# shell script, so there is no `*.py` token to find.
SHELL_ONLY_README = """# Fixture example

```bash
cd ../mcp-connect-claude-code
./run_e2e.sh
```

Expected output (verified during this build):

```
PASS: mcp server connected
```
"""


def _with_directive(readme: str, marker_line: str, reason: str = FIXTURE_REASON) -> str:
    """Insert the opt-out directive on the line above ``marker_line``."""
    # An em dash separator, as the real directive uses; escaped to keep this
    # file ASCII. `-` and `:` are equally acceptable separators.
    directive = f"{sweep.DIRECTIVE_PREFIX} \u2014 {reason} -->"
    assert marker_line in readme, marker_line
    return readme.replace(marker_line, f"{directive}\n{marker_line}")


# --------------------------------------------------------------------------- #
# 1-3. Reading the command out of the README (pure)
# --------------------------------------------------------------------------- #


def test_command_script_reads_the_script_from_the_block_above() -> None:
    assert sweep.command_script(FIXTURE_README) == "test_x.py"

    # The interpreter in the README is ignored - the sweep brings its own - and
    # the `cd` line above it must not be mistaken for the command.
    real = (EXAMPLES_DIR / "typed-tool-registry" / "README.md").read_text(
        encoding="utf-8"
    )
    assert sweep.command_script(real) == "test_agent.py"

    print("ok  command_script takes the script out of the block above the transcript")


def test_command_script_takes_the_last_py_token() -> None:
    venv_block = """# Fixture

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python test_server.py
```

Expected output:

```
ok  alpha
```
"""
    assert sweep.command_script(venv_block) == "test_server.py"

    print("ok  command_script takes the last .py token, past pip and venv noise")


def test_command_script_refuses_a_block_with_no_script() -> None:
    try:
        sweep.command_script(SHELL_ONLY_README)
    except sweep.CommandNotFound as exc:
        assert ".py" in str(exc), str(exc)
    else:
        raise AssertionError("expected CommandNotFound for a block with no .py token")

    no_block_at_all = "# Fixture\n\nExpected output:\n\n```\nok  alpha\n```\n"
    try:
        sweep.command_script(no_block_at_all)
    except sweep.CommandNotFound:
        pass
    else:
        raise AssertionError("expected CommandNotFound when no block precedes")

    print("ok  command_script refuses to guess when the README names no script")


# --------------------------------------------------------------------------- #
# 4-7. Planning what to do with a README (pure)
# --------------------------------------------------------------------------- #


def test_plan_target_reports_no_transcript() -> None:
    without_marker = "# Fixture\n\nProse.\n\n```bash\npython test_x.py\n```\n"

    assert sweep.plan_target(without_marker) == sweep.NoTranscript()

    print("ok  a README with no marked block plans to NoTranscript, not a failure")


def test_plan_target_reports_opt_out_with_its_reason() -> None:
    opted_out = _with_directive(SHELL_ONLY_README, "Expected output (verified")

    target = sweep.plan_target(opted_out)

    assert target == sweep.OptOut(reason=FIXTURE_REASON), target
    # The opt-out is decided before the command is looked for, which is why the
    # `./run_e2e.sh` shape above never reports CommandMissing once it opts out.
    assert not isinstance(target, sweep.CommandMissing)

    print("ok  the skip directive plans to OptOut, carrying the author's reason")


def test_the_directive_must_sit_above_the_marker() -> None:
    """Pins the note's first open question: above the marker, not below it."""
    directive = f"{sweep.DIRECTIVE_PREFIX} - {FIXTURE_REASON} -->"
    below = FIXTURE_README.replace(
        "Expected output:", f"Expected output:\n\n{directive}"
    )

    # Between the marker and the fence, the directive *becomes* the fence's
    # nearest preceding line, so the block stops being marked at all. It fails
    # loudly (nothing is checked) rather than silently checking the block.
    assert sweep.plan_target(below) == sweep.NoTranscript()
    assert sweep.plan_target(_with_directive(FIXTURE_README, "Expected output:")) == (
        sweep.OptOut(reason=FIXTURE_REASON)
    )

    print("ok  the directive is read above the marker line, and only there")


def test_plan_target_reports_a_checkable_script() -> None:
    assert sweep.plan_target(FIXTURE_README) == sweep.Checkable(script="test_x.py")

    # A transcript with no runnable command is a finding, not a silent skip.
    assert sweep.plan_target(SHELL_ONLY_README) == sweep.CommandMissing()

    print("ok  an ordinary README plans to Checkable with its script")


def test_plan_target_reports_ambiguity_with_a_count() -> None:
    two_blocks = FIXTURE_README + "\nExpected output:\n\n```\nok  delta\n```\n"

    assert sweep.plan_target(two_blocks) == sweep.Ambiguous(count=2)

    # Ambiguity outranks the opt-out: a README with two blocks, one of which is
    # skipped, still has no single transcript to check.
    ambiguous_and_opted_out = _with_directive(two_blocks, "Expected output:")
    assert isinstance(sweep.plan_target(ambiguous_and_opted_out), sweep.Ambiguous)

    print("ok  two marked blocks plan to Ambiguous, naming the count")


# --------------------------------------------------------------------------- #
# 8-9. The report (pure)
# --------------------------------------------------------------------------- #


def test_summarize_emits_one_fail_line_per_finding() -> None:
    drift = ct.Drift(
        expected="All 4 self-tests passed.\n",
        actual="All 6 self-tests passed.\n",
        diff="(unused here)",
    )
    report = sweep.summarize(
        [
            ("foo", drift),
            ("bar", ct.Match()),
            ("baz", sweep.OptOut(reason=FIXTURE_REASON)),
            ("qux", sweep.NoTranscript()),
        ]
    )

    fails = [line for line in report.splitlines() if line.startswith(sweep.FAIL_PREFIX)]
    assert len(fails) == 1, fails
    assert fails[0].startswith(f"{sweep.FAIL_PREFIX}examples/foo/"), fails[0]
    # The line is lifted out of the report on its own, so it has to say what
    # kind of finding it is and carry the detail with it.
    assert "README transcript drift:" in fails[0], fails[0]
    assert "All 4 self-tests passed." in fails[0], fails[0]
    assert "All 6 self-tests passed." in fails[0], fails[0]

    for clean in ("bar", "baz", "qux"):
        assert f"examples/{clean}/" not in "\n".join(fails), clean
    # ... and the two non-findings are still reported, just not as failures.
    assert "OPT-OUT" in report and FIXTURE_REASON in report
    assert "NO TRANSCRIPT" in report

    print("ok  summarize files exactly the findings, and match/opt-out/none are not")


def test_summarize_counts_every_bucket() -> None:
    report = sweep.summarize(
        [
            ("a", ct.Match()),
            ("b", ct.Drift(expected="x\n", actual="y\n", diff="")),
            ("c", ct.Unrunnable(exit_code=1, stderr_tail="ModuleNotFoundError: mcp")),
            ("d", sweep.OptOut(reason=FIXTURE_REASON)),
            ("e", sweep.NoTranscript()),
            ("f", sweep.Ambiguous(count=3)),
            ("g", sweep.CommandMissing()),
        ]
    )
    counts = report.splitlines()[0]

    assert counts == (
        "Transcripts: 1 match / 1 drift / 1 unrunnable / 1 opt-out / "
        "1 no-transcript / 2 error of 7"
    ), counts
    # Ambiguous and CommandMissing share the "error" bucket but are both filed.
    fails = [line for line in report.splitlines() if line.startswith(sweep.FAIL_PREFIX)]
    assert len(fails) == 4, fails

    try:
        sweep.summarize([("h", "Match")])  # type: ignore[list-item]
    except TypeError:
        pass
    else:
        raise AssertionError("expected TypeError rather than a defaulted bucket")

    print("ok  the counts line reports all six buckets, and an unknown one raises")


def test_the_cli_keeps_its_failure_codes_off_the_findings_code() -> None:
    """A flag naming nothing must not exit 1, which means "your README is wrong"."""
    assert sweep.main(["--only", "no-such-example"]) == sweep.EXIT_USAGE
    assert sweep.main(["--examples-root", "/no/such/root"]) == sweep.EXIT_USAGE
    assert sweep.EXIT_USAGE != sweep.EXIT_FINDINGS != sweep.EXIT_SWEEP_ERROR

    print("ok  a bad flag exits 64, never 1, and never sweeps the subset that exists")


# --------------------------------------------------------------------------- #
# 10. End to end over two real examples, no fixtures
# --------------------------------------------------------------------------- #


def test_end_to_end_sweeps_two_real_examples() -> None:
    names = ["readme-transcript-check", "skill-anatomy"]
    readmes = {
        name: (EXAMPLES_DIR / name / ct.README_FILENAME) for name in names
    }
    before = {name: path.read_bytes() for name, path in readmes.items()}

    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp)
        results = sweep.run_sweep(EXAMPLES_DIR, scratch, only=names)

        # Neither example has a requirements.txt, so nothing may be built: this
        # is what keeps the offline suite offline.
        assert list(scratch.iterdir()) == [], list(scratch.iterdir())

    assert [name for name, _ in results] == sorted(names), results
    for name, outcome in results:
        if isinstance(outcome, ct.Drift):
            raise AssertionError(f"{name}'s documented transcript drifted:\n{outcome.diff}")
        if isinstance(outcome, ct.Unrunnable):
            raise AssertionError(
                f"could not run {name}'s self-test (exit {outcome.exit_code}):\n"
                f"{outcome.stderr_tail}"
            )
        assert isinstance(outcome, ct.Match), (name, outcome)

    # Sweeping is read-only, inherited from check_transcript's lack of --update.
    for name, path in readmes.items():
        assert path.read_bytes() == before[name], f"the sweep rewrote {name}'s README"

    assert not sweep.summarize(results).count(sweep.FAIL_PREFIX)

    print("ok  end to end: two real examples sweep to Match, with no venv and no diff")


# --------------------------------------------------------------------------- #
# 11. The suite's own preconditions
# --------------------------------------------------------------------------- #


def test_both_suites_run_on_a_bare_interpreter() -> None:
    # Half of this is proved by arrival: this file is executing under whatever
    # `python3 test_sweep.py` resolved to, with no venv and no key.
    for module in (sweep, ct):
        for name, value in vars(module).items():
            if not isinstance(value, types.ModuleType):
                continue
            assert (
                value.__name__ in sys.stdlib_module_names
                or value.__name__ in {"check_transcript", "sweep"}
            ), f"{module.__name__} imports non-stdlib {value.__name__} as {name}"

    keyless = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    completed = subprocess.run(
        [sys.executable, "test_check_transcript.py"],
        cwd=str(HERE),
        env=keyless,
        capture_output=True,
        text=True,
        timeout=ct.DEFAULT_TIMEOUT_SECONDS,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.rstrip().endswith("All 10 self-tests passed."), (
        completed.stdout
    )

    print("ok  both suites are stdlib-only and pass with no key, network or venv")


# --------------------------------------------------------------------------- #
# 12. The virtualenv cache (pins the note's second open question)
# --------------------------------------------------------------------------- #


def test_build_interpreter_reuses_a_ready_venv() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        stdlib_only = root / "stdlib-only"
        stdlib_only.mkdir()
        assert sweep.build_interpreter(stdlib_only, root / "scratch") == [
            sweep.BARE_INTERPRETER
        ]
        assert not (root / "scratch").exists(), "no requirements, so nothing to build"

        needs_deps = root / "needs-deps"
        needs_deps.mkdir()
        requirements = "anthropic==0.121.0\n"
        (needs_deps / sweep.REQUIREMENTS_FILENAME).write_text(requirements)

        scratch = root / "scratch"
        venv_dir = scratch / "needs-deps"
        python = venv_dir / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.write_text("#!/bin/sh\n")
        (venv_dir / sweep.READY_MARKER).write_text(requirements)

        # A venv already built from these exact requirements is reused as-is:
        # one sweep builds each example's environment at most once. Nothing is
        # created, which is also why this test needs no network.
        assert sweep.build_interpreter(needs_deps, scratch) == [str(python)]
        assert not (venv_dir / "pyvenv.cfg").exists(), "no venv was built"

        # A marker that does not match the requirements is not a cache hit, so a
        # stale or half-built scratch directory is rebuilt instead of trusted.
        assert sweep._venv_is_ready(venv_dir, requirements)
        (venv_dir / sweep.READY_MARKER).write_text("anthropic==0.120.0\n")
        assert not sweep._venv_is_ready(venv_dir, requirements)
        (venv_dir / sweep.READY_MARKER).unlink()
        assert not sweep._venv_is_ready(venv_dir, requirements)

    print("ok  a venv is built once per requirements content, and rebuilt when stale")


# --------------------------------------------------------------------------- #


def main() -> int:
    tests = [
        test_command_script_reads_the_script_from_the_block_above,
        test_command_script_takes_the_last_py_token,
        test_command_script_refuses_a_block_with_no_script,
        test_plan_target_reports_no_transcript,
        test_plan_target_reports_opt_out_with_its_reason,
        test_the_directive_must_sit_above_the_marker,
        test_plan_target_reports_a_checkable_script,
        test_plan_target_reports_ambiguity_with_a_count,
        test_summarize_emits_one_fail_line_per_finding,
        test_summarize_counts_every_bucket,
        test_the_cli_keeps_its_failure_codes_off_the_findings_code,
        test_end_to_end_sweeps_two_real_examples,
        test_both_suites_run_on_a_bare_interpreter,
        test_build_interpreter_reuses_a_ready_venv,
    ]
    for test in tests:
        test()
    print(f"\nAll {len(tests)} self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
