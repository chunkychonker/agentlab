"""The docs drift guard (milestone 12): the README and ``docs/*.md`` cannot name what the code no longer has.

Intent. nightloop's docs name subcommands, flags, exit codes, module paths,
test files, incident sources and command output. Each of these can go
stale silently when the code changes, and a diff-scoped review does not
see it (``knowledge/doc-transcript-drift.md``). These tests take each fact
from the code or the filesystem, never from a second hand-kept list, and
fail naming the doc and the stale reference. Out of scope: prose
accuracy, external URLs (no network), and ``plugin/README.md``.

Spec (from ``research/2026-11-25-nightloop-docs.md``, D1-D8):

- D1. Every relative link in README.md and docs/*.md resolves to an
  existing file or directory. An anchor (``#x``) on a Markdown target must
  be one of that file's GitHub heading anchors. URLs with a scheme are not
  checked.
- D2. Every ``nightloop <subcommand> [<action>] [--flags]`` command in the
  docs' code (fences and inline spans, not prose) is accepted by the real
  parser: ``main([<words>, "--help"])`` exits 0, and every ``--flag`` in
  the command appears in that help. A subcommand with no positional
  arguments takes no action word. The README's command table names every
  subcommand ``nightloop --help`` lists.
- D3. The README's exit-code table equals ``ExitCode`` (names and values).
- D4. Every ``## Gate:`` section of architecture.md has one Module line and
  one Tests line, each naming at least one existing file (tests under
  ``tests/test_*.py``).
- D5. Every gate section has one Incident line. Its value is exactly "no
  recorded incident" with no Source line, or the section has one Source
  line whose links all resolve (at least one).
- D6. The quickstart's marked dry-run command, run through ``main`` with
  the clock at 02:30 local, prints exactly the marked output block, with
  the stand-in repo path substituted. The same command as a subprocess
  (``python3 -m nightloop ...`` from projects/nightloop) exits 0 and prints
  the same lines after the first (the first is the clock's window line).
- D7. The README says "has not been run live" and links to PLAN.md's
  current state.
- D8. No doc is empty, and none contains TODO/TBD/FIXME/XXX or lorem ipsum.

Interfaces: the pure parsers live in ``tests/docs_support.py`` (``links``,
``heading_anchors``, ``invocations``, ``exit_code_table``,
``gate_sections``, ``marked_fence``, ``forbidden_words``). The research
note's ``subcommands_in(md) -> set[str]`` became ``invocations(md) ->
list[Invocation]``, so the flags and the action word of each command can be
checked too. File reads and ``main`` calls happen only in this module.
"""

from __future__ import annotations

import contextlib
import functools
import io
import os
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from nightloop.cli.exit_codes import ExitCode
from nightloop.cli.main import main

from .docs_support import (
    NO_INCIDENT,
    backticked_paths,
    exit_code_table,
    forbidden_words,
    gate_sections,
    heading_anchors,
    invocations,
    is_external,
    links,
    marked_fence,
    split_target,
)

PROJECT = Path(__file__).resolve().parent.parent
README = PROJECT / "README.md"
ARCHITECTURE = PROJECT / "docs" / "architecture.md"
QUICKSTART = PROJECT / "docs" / "quickstart.md"
# The repo path the quickstart's dry-run command and output are written with.
REPO_STANDIN = "/path/to/your/checkout"
# The quickstart says "At 02:30"; the dry run's first line depends on the hour.
DRY_RUN_HOUR, DRY_RUN_MINUTE = 2, 30
COMMAND_PREFIX = ["python3", "-m", "nightloop"]
LIVE_STATUS = "has not been run live"
STATUS_LINK = "PLAN.md#current-state"


def docs() -> list[Path]:
    """The docs under guard: the README, then docs/*.md in name order."""
    return [README, *sorted((PROJECT / "docs").glob("*.md"))]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@functools.lru_cache(maxsize=None)
def help_of(words: tuple[str, ...]) -> tuple[int, str]:
    """``main([*words, "--help"])``'s exit code and everything it printed."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main([*words, "--help"])
    return code, out.getvalue() + err.getvalue()


def takes_action(subcommand: str) -> bool:
    """True if ``subcommand`` has positional arguments (``schedule``'s install/uninstall/status)."""
    return "positional arguments:" in help_of((subcommand,))[1]


def has_flag(help_text: str, flag: str) -> bool:
    return any(token.strip("[],|") == flag for token in help_text.split())


def link_problem(doc: Path, target: str) -> str | None:
    """Why ``target`` (a link in ``doc``) does not resolve, or ``None`` if it does."""
    path_part, anchor = split_target(target)
    resolved = doc if path_part == "" else (doc.parent / path_part).resolve()
    if not resolved.exists():
        return f"{target}: {resolved} does not exist"
    if anchor is None:
        return None
    if resolved.suffix != ".md" or not resolved.is_file():
        return f"{target}: an anchor on a non-Markdown target cannot be checked"
    if anchor not in heading_anchors(read(resolved)):
        return f"{target}: no heading with anchor #{anchor} in {resolved.name}"
    return None


class LinksResolve(unittest.TestCase):
    """D1."""

    def test_every_relative_link_and_anchor_resolves(self) -> None:
        checked = 0
        for doc in docs():
            for target in links(read(doc)):
                if is_external(target):
                    continue
                checked += 1
                with self.subTest(doc=doc.name, link=target):
                    self.assertIsNone(link_problem(doc, target))
        self.assertGreater(checked, 0, "no relative links found: the parser is broken")


class CommandsExist(unittest.TestCase):
    """D2."""

    def test_every_named_command_and_flag_is_accepted_by_the_parser(self) -> None:
        seen = 0
        for doc in docs():
            for inv in invocations(read(doc)):
                seen += 1
                with self.subTest(doc=doc.name, command=inv.text):
                    sub = inv.words[0]
                    code, _ = help_of((sub,))
                    self.assertEqual(code, ExitCode.OK, f"'nightloop {sub}' is not a subcommand")
                    words = inv.words
                    if len(words) == 2 and not takes_action(sub):
                        self.fail(f"'nightloop {sub}' takes no action word, but the doc gives {words[1]!r}")
                    code, text = help_of(words)
                    self.assertEqual(code, ExitCode.OK, f"'nightloop {' '.join(words)}' is not a command")
                    for flag in inv.flags:
                        self.assertTrue(has_flag(text, flag),
                                        f"'nightloop {' '.join(words)}' has no {flag}")
        self.assertGreater(seen, 0, "no nightloop commands found: the parser is broken")

    def test_the_readme_command_table_names_every_subcommand(self) -> None:
        _, text = help_of(())
        usage = text.split("\n", 1)[0]  # usage: nightloop [-h] {run,report,...} ...
        listed = usage[usage.index("{") + 1:usage.index("}")].split(",")
        named = {inv.words[0] for inv in invocations(read(README))}
        for sub in listed:
            with self.subTest(subcommand=sub):
                self.assertEqual(help_of((sub,))[0], ExitCode.OK)
                self.assertIn(sub, named, f"README names no 'nightloop {sub}' command")


class ExitCodeTable(unittest.TestCase):
    """D3."""

    def test_the_readme_table_equals_exit_code(self) -> None:
        self.assertEqual(exit_code_table(read(README)), {code.name: code.value for code in ExitCode})


class GateSections(unittest.TestCase):
    """D4 and D5."""

    def setUp(self) -> None:
        self.sections = gate_sections(read(ARCHITECTURE))

    def test_there_are_gate_sections(self) -> None:
        self.assertGreater(len(self.sections), 0, "no '## Gate:' sections: the parser is broken")

    def test_every_gate_names_an_existing_module_and_test_file(self) -> None:
        for section in self.sections:
            for label in ("Module", "Tests"):
                with self.subTest(gate=section.name, field=label):
                    values = section.fields.get(label, ())
                    self.assertEqual(len(values), 1, f"needs exactly one {label} line")
                    paths = backticked_paths(values[0])
                    self.assertGreater(len(paths), 0, f"{label} names no `path`")
                    for rel in paths:
                        self.assertTrue((PROJECT / rel).is_file(), f"{label}: {rel} does not exist")
                        if label == "Tests":
                            self.assertRegex(rel, r"^tests/test_[a-z0-9_]+\.py$")

    def test_every_incident_cites_a_resolving_source_or_says_none_is_recorded(self) -> None:
        for section in self.sections:
            with self.subTest(gate=section.name):
                incident = section.fields.get("Incident", ())
                sources = section.fields.get("Source", ())
                self.assertEqual(len(incident), 1, "needs exactly one Incident line")
                self.assertTrue(incident[0], "the Incident line is empty")
                if incident[0] == NO_INCIDENT:
                    self.assertEqual(sources, (), f"'{NO_INCIDENT}' with a Source line")
                    continue
                self.assertEqual(len(sources), 1, "an incident needs exactly one Source line")
                targets = [t for t in links(sources[0]) if not is_external(t)]
                self.assertGreater(len(targets), 0, "the Source line links to no file in the repo")
                for target in targets:
                    self.assertIsNone(link_problem(ARCHITECTURE, target))


class QuickstartDryRun(unittest.TestCase):
    """D6."""

    def setUp(self) -> None:
        self.text = read(QUICKSTART)
        command = marked_fence(self.text, "dry-run-command")
        self.assertEqual(len(command.lines), 1, "the dry-run command block must be one line")
        tokens = shlex.split(command.lines[0])
        self.assertEqual(tokens[:3], COMMAND_PREFIX)
        self.assertIn(REPO_STANDIN, tokens, "the command must use the stand-in repo path")
        self.expected = "\n".join(marked_fence(self.text, "dry-run-output").lines)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name)
        self.argv = [str(self.repo) if t == REPO_STANDIN else t for t in tokens[3:]]

    def standin(self, output: str) -> str:
        return output.replace(str(self.repo.resolve()), REPO_STANDIN).rstrip("\n")

    def test_the_doc_states_the_clock_the_output_was_taken_at(self) -> None:
        self.assertIn(f"At {DRY_RUN_HOUR:02d}:{DRY_RUN_MINUTE:02d}", self.text)

    def test_main_prints_exactly_the_documented_output(self) -> None:
        def never(*_args: object) -> object:
            raise AssertionError("a dry run must build no runner, workspace, shipper or claim port")

        clock = time.mktime((2026, 11, 25, DRY_RUN_HOUR, DRY_RUN_MINUTE, 0, 0, 0, -1))
        out, err = io.StringIO(), io.StringIO()
        cwd = os.getcwd()
        os.chdir(PROJECT)  # the quickstart runs it from projects/nightloop
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main(self.argv, clock=lambda: clock, runner_factory=never,
                            workspace_factory=never, shipper_factory=never, claims_factory=never)
        finally:
            os.chdir(cwd)
        self.assertEqual((code, err.getvalue()), (ExitCode.OK, ""))
        self.assertEqual(self.standin(out.getvalue()), self.expected)

    def test_the_command_as_written_exits_0_from_projects_nightloop(self) -> None:
        done = subprocess.run([sys.executable, "-m", "nightloop", *self.argv], cwd=PROJECT,
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, ExitCode.OK, done.stderr)
        self.assertEqual(self.standin(done.stdout).split("\n")[1:], self.expected.split("\n")[1:])


class ReadmeStatus(unittest.TestCase):
    """D7."""

    def test_the_readme_says_it_has_not_run_live_and_links_the_known_limits(self) -> None:
        text = read(README)
        self.assertIn(LIVE_STATUS, text)
        self.assertIn(STATUS_LINK, links(text))


class NoPlaceholders(unittest.TestCase):
    """D8."""

    def test_no_doc_is_empty_or_holds_placeholder_text(self) -> None:
        for doc in docs():
            with self.subTest(doc=doc.name):
                text = read(doc)
                self.assertTrue(text.strip(), "empty doc")
                self.assertEqual(forbidden_words(text), [])


if __name__ == "__main__":
    unittest.main()
