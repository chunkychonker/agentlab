# heldout.sh — researcher-written acceptance tests the build phase does not
# get to read, run as a gate after the builder's own suite.
#
# Why it exists: the builder writes both the increment and its tests, and the
# verify gate and the reviewer re-run that same suite. An agent optimises for
# the tests it can see (survey claim 6 in
# research/2026-08-16-agent-architecture-discourse-vs-agentlab.md, citing
# SpecBench). A held-out file, written by the researcher against the same
# layer-2 criteria the note states, checks the increment against cases the
# builder did not choose. See research/2026-11-28-held-out-acceptance-tests.md.
#
# THIS HIDES. IT DOES NOT ENFORCE. The build phase has Bash, and a permission
# deny rule matches command text, so `cat` or `python3 -c` reads anything a
# Read deny names. What this does is move $HELDOUT_DIR out of the worktree to
# a random directory under a fresh mktemp root while the build (and the fix
# round) runs, and phase_tools.sh adds Read/Edit denies for the path on top. A
# builder that goes looking can still find it with `find $TMPDIR`. A real
# boundary needs the Claude Code sandbox or a separate OS user; both are out
# of scope.
#
# The contract with the researcher (who writes the files; the pipeline owns
# nothing inside the directory):
#   heldout/<suite>/run.sh   one suite per directory, run as
#                            `bash heldout/<suite>/run.sh` from the repo root.
#                            Exit 0 is a pass. <suite> is [A-Za-z0-9._-]+.
#   What the builder is shown on a FAIL: only output lines that start with
#   `FAIL` or `ERROR` (followed by `:` or a space), lines that are a bare
#   exception (`AssertionError: 2 != 3`), and a timeout notice. Tracebacks,
#   source lines and everything else are withheld, so a test's source never
#   reaches the fix round. Python's unittest prints exactly that shape.
#   The research note must not paste held-out test bodies or exact expected
#   values: the builder reads the note.
#
# Lifetime: $HELDOUT_DIR is git-ignored (/heldout/ in .gitignore), so the
# maintainer never ships it and it is gone after the night (scrub_artifacts
# runs `git clean -fdx`, and run_cycle removes it at the start of each cycle).
#
# Blast radius: heldout_run executes researcher-written code in the caller's
# shell environment, as verify_run already does for builder-written code. A
# hung suite costs at most VERIFY_TIMEOUT_S. A broken $HELDOUT_DIR costs one
# cycle (ERROR stops it), never a silent pass.
#
# Sourcing this file defines functions and constants. It runs nothing, prints
# nothing, and touches no files. Safe under `set -uo pipefail`. Needs
# verify.sh sourced as well (heldout_run reuses verify_run and its timeout);
# heldout_run reports ERROR when it is not.
#
# bash 3.2 ONLY (macOS system bash is 3.2.57).

HELDOUT_DIR="heldout"
# What the fix round is handed when a held-out suite fails. Under logs/,
# git-ignored like every handoff file. Names and assertion lines only.
HELDOUT_REPORT_FILE="logs/last-heldout.md"
# Per suite, in the report. Enough for every failing test of a small suite;
# a cap so a suite that prints a FAIL line per input cannot flood the fix.
HELDOUT_REPORT_MAX_LINES=60
HELDOUT_REPORT_MAX_CHARS=300
# What heldout_hidden returns when the command succeeded but the directory
# could not be put back. Distinct from the command's own codes.
HELDOUT_RESTORE_RC=3

# _heldout_is_empty_dir <dir>: rc 0 iff <dir> is a directory with no entries.
_heldout_is_empty_dir () {
  [ -d "$1" ] || return 1
  [ -z "$(ls -A "$1" 2>/dev/null)" ]
}

# heldout_stash <stash-root>
#
# Moves $HELDOUT_DIR (relative to the current directory, the repo root) to a
# new random directory under <stash-root> and prints the moved directory's
# path. <stash-root> must be a directory used for nothing else: an un-restored
# stash is recognised by <stash-root> not being empty.
#   0  printed the stashed path, or `NONE` when there is no $HELDOUT_DIR
#      (nothing is moved and <stash-root> is left empty)
#   1  <stash-root> is not a directory, or mktemp or mv failed; prints
#      nothing. A failed mv leaves $HELDOUT_DIR where it was and removes the
#      empty random directory.
#   2  <stash-root> already holds a stash that was never restored; prints
#      nothing and moves nothing, so a second stash can never hide that the
#      first one was lost.
heldout_stash () {
  local root="$1" dir
  if [ ! -d "$root" ]; then echo "heldout_stash: stash root '$root' is not a directory" >&2; return 1; fi
  if ! _heldout_is_empty_dir "$root"; then
    echo "heldout_stash: '$root' already holds a stash that was never restored" >&2; return 2
  fi
  if [ ! -e "$HELDOUT_DIR" ] && [ ! -L "$HELDOUT_DIR" ]; then echo "NONE"; return 0; fi
  dir="$(mktemp -d "$root/XXXXXXXX")" || return 1
  if ! mv "$HELDOUT_DIR" "$dir/t"; then
    rmdir "$dir" 2>/dev/null
    echo "heldout_stash: could not move '$HELDOUT_DIR' to '$dir'" >&2; return 1
  fi
  echo "$dir/t"
  return 0
}

# heldout_restore <stashed-path>
#
# Moves a path printed by heldout_stash back to $HELDOUT_DIR and removes the
# random directory it sat in, so the stash root is empty again.
#   0  restored
#   1  <stashed-path> does not exist, or mv failed (the stash stays put)
#   2  the worktree already has something at $HELDOUT_DIR (the build wrote
#      one). Nothing is moved and nothing is overwritten; the stash stays
#      where it is, for a human to compare.
heldout_restore () {
  local p="$1"
  if [ -e "$HELDOUT_DIR" ] || [ -L "$HELDOUT_DIR" ]; then
    echo "heldout_restore: '$HELDOUT_DIR' already exists in the worktree — not overwriting it" >&2; return 2
  fi
  if [ ! -e "$p" ]; then echo "heldout_restore: stashed path '$p' does not exist" >&2; return 1; fi
  if ! mv "$p" "$HELDOUT_DIR"; then
    echo "heldout_restore: could not move '$p' back to '$HELDOUT_DIR'" >&2; return 1
  fi
  rmdir "$(dirname "$p")" 2>/dev/null
  return 0
}

# heldout_hidden <stash-root> <log> <command> [args...]
#
# Runs <command> [args...] in the current shell with $HELDOUT_DIR stashed
# under <stash-root>, then puts it back, on the command's failure as well as
# its success. An explicit step, not a trap: bash 3.2 defers traps until a
# foreground child exits (knowledge/pipeline-run-log-shapes.md). The stashed
# path is never written to <log>: the build phase can read the run log.
#   rc of <command>   when stash and restore both worked
#   1                 the stash failed; <command> did not run
#   <command>'s rc    the restore failed after a failing command
#   HELDOUT_RESTORE_RC the restore failed after a command that succeeded.
#                     Either way the cycle must not go on: the gate would
#                     run without the held-out tests.
heldout_hidden () {
  local root="$1" log="$2"
  shift 2
  local stashed rc=0
  if ! stashed="$(heldout_stash "$root" 2>>"$log")"; then
    echo "--- held-out: could not hide $HELDOUT_DIR/ — not running the phase. ---" >>"$log"
    return 1
  fi
  [ "$stashed" != "NONE" ] && echo "--- held-out: $HELDOUT_DIR/ moved out of the worktree for this phase. ---" >>"$log"
  "$@" || rc=$?
  [ "$stashed" = "NONE" ] && return "$rc"
  if ! heldout_restore "$stashed" 2>>"$log"; then
    echo "--- held-out: could not put $HELDOUT_DIR/ back after the phase (did it write $HELDOUT_DIR/?). ---" >>"$log"
    [ "$rc" -ne 0 ] && return "$rc"
    return "$HELDOUT_RESTORE_RC"
  fi
  echo "--- held-out: $HELDOUT_DIR/ restored. ---" >>"$log"
  return "$rc"
}

# heldout_report <suite> <rc> <raw-output-file>
#
# Prints the report section for one failed suite to stdout: a heading with
# the suite name and exit code, then only the lines of <raw-output-file> that
# match the contract in the header (FAIL/ERROR lines, bare exception lines,
# the timeout notice), each cut to HELDOUT_REPORT_MAX_CHARS, at most
# HELDOUT_REPORT_MAX_LINES of them. Says how many lines were withheld.
# Reads one file, writes nothing. An unreadable <raw-output-file> prints the
# heading and "(output unreadable)" and returns 1.
heldout_report () {
  local suite="$1" rc="$2" raw="$3"
  echo "## ✗ $suite (exit code $rc)"
  echo ""
  if [ ! -r "$raw" ]; then echo "(output unreadable)"; echo ""; return 1; fi
  local total kept
  total="$(wc -l < "$raw" | tr -d ' ')"
  kept="$(grep -E '^(FAIL|ERROR)(:| )|^[A-Za-z_][A-Za-z0-9_.]*(Error|Exception|Failure)(:.*)?$|^timed out after [0-9]+s' "$raw" \
          | cut -c "1-$HELDOUT_REPORT_MAX_CHARS")"
  local n=0
  [ -n "$kept" ] && n="$(printf '%s\n' "$kept" | wc -l | tr -d ' ')"
  if [ "$n" -eq 0 ]; then
    echo "(no FAIL/ERROR or assertion lines in its output; all $total lines withheld)"
  else
    printf '%s\n' "$kept" | head -n "$HELDOUT_REPORT_MAX_LINES"
    [ "$n" -gt "$HELDOUT_REPORT_MAX_LINES" ] && \
      echo "($(( n - HELDOUT_REPORT_MAX_LINES )) more matching lines not shown)"
    echo "($(( total - n )) other lines withheld: source and tracebacks are not shown)"
  fi
  echo ""
  return 0
}

# heldout_run <log> <stash-root>
#
# Runs every heldout/<suite>/run.sh, in name order, each under verify_run's
# timeout, even after one fails. One `✓/✗ held-out: <suite>` line per suite
# goes to <log>. Removes HELDOUT_REPORT_FILE first; on FAIL writes it (via
# heldout_report). Raw output is kept only in mktemp files outside the repo
# and deleted. Prints exactly one word; the rc mirrors it:
#   0  PASS   every suite exited 0
#   0  NONE   no $HELDOUT_DIR and nothing stashed: tonight is ungated by
#             held-out tests, and <log> says so. Not the same as PASS.
#   1  FAIL   at least one suite failed or timed out
#   2  ERROR  the tests could not be run as written, which is never a pass:
#             <stash-root> is not an empty directory (a stash was never
#             restored), $HELDOUT_DIR is not a readable directory (or is a
#             symlink), it holds no suite, a suite name is outside
#             [A-Za-z0-9._-], a suite has no readable run.sh, verify_run is
#             not defined, or the report could not be written.
heldout_run () {
  local log="$1" root="$2"
  rm -f "$HELDOUT_REPORT_FILE"
  if ! declare -f verify_run >/dev/null 2>&1; then
    echo "held-out: verify_run is not defined (source verify.sh)" >>"$log"; echo "ERROR"; return 2
  fi
  if ! _heldout_is_empty_dir "$root"; then
    echo "held-out: stash root '$root' is missing or still holds a stash — the tests were never put back" >>"$log"
    echo "ERROR"; return 2
  fi
  if [ ! -e "$HELDOUT_DIR" ] && [ ! -L "$HELDOUT_DIR" ]; then
    echo "held-out: no $HELDOUT_DIR/ — no held-out tests tonight, so this gate checked nothing" >>"$log"
    echo "NONE"; return 0
  fi
  if [ -L "$HELDOUT_DIR" ] || [ ! -d "$HELDOUT_DIR" ] || [ ! -r "$HELDOUT_DIR" ] || [ ! -x "$HELDOUT_DIR" ]; then
    echo "held-out: $HELDOUT_DIR is not a readable directory" >>"$log"; echo "ERROR"; return 2
  fi

  local entry suite suites=""
  for entry in "$HELDOUT_DIR"/*; do
    [ -d "$entry" ] || continue
    suite="${entry#"$HELDOUT_DIR"/}"
    case "$suite" in
      *[!A-Za-z0-9._-]*) echo "held-out: suite name '$suite' is outside [A-Za-z0-9._-]" >>"$log"; echo "ERROR"; return 2 ;;
    esac
    if [ ! -f "$entry/run.sh" ] || [ ! -r "$entry/run.sh" ]; then
      echo "held-out: suite '$suite' has no readable run.sh" >>"$log"; echo "ERROR"; return 2
    fi
    suites="$suites$suite
"
  done
  if [ -z "$suites" ]; then
    echo "held-out: $HELDOUT_DIR/ holds no suite (no <suite>/run.sh)" >>"$log"; echo "ERROR"; return 2
  fi

  local raw rc failed=0 broken=0
  while IFS= read -r suite; do
    [ -n "$suite" ] || continue
    raw="$(mktemp)" || { echo "ERROR"; return 2; }
    rc=0
    verify_run "held-out: $suite" "bash $HELDOUT_DIR/$suite/run.sh" "$raw" "$VERIFY_TIMEOUT_S" >>"$log" 2>&1 || rc=$?
    if [ "$rc" -ne 0 ]; then
      if [ "$failed" -eq 0 ]; then
        { echo "# Held-out acceptance tests: FAIL"
          echo ""
          echo "The researcher wrote these tests against the spec's acceptance criteria and"
          echo "they were hidden from the build. Only the failing test names and assertion"
          echo "lines are shown; the test source is withheld on purpose. Fix the increment"
          echo "so it meets the criterion each name points at, not the test."
          echo ""; } > "$HELDOUT_REPORT_FILE" 2>>"$log" || broken=1
      fi
      failed=1
      heldout_report "$suite" "$rc" "$raw" >> "$HELDOUT_REPORT_FILE" 2>>"$log" || broken=1
    fi
    rm -f "$raw"
  done <<< "$suites"
  if [ "$broken" -eq 1 ]; then
    echo "held-out: could not write $HELDOUT_REPORT_FILE" >>"$log"; echo "ERROR"; return 2
  fi
  if [ "$failed" -eq 1 ]; then echo "FAIL"; return 1; fi
  echo "PASS"; return 0
}

# _heldout_word <text>: <text> if it is one upper-case word, else INVALID, so
# a stray word can never split the gate line.
_heldout_word () {
  case "$1" in
    ''|*[!A-Z_]*) echo "INVALID" ;;
    *) echo "$1" ;;
  esac
}

# heldout_gate <log> <stash-root> <verify-fn> <heldout-fn>
#
# The combined post-build gate. Calls `<verify-fn> <log>` (verify_increment)
# and, only when that printed PASS or NONE, `<heldout-fn> <log> <stash-root>`
# (heldout_run): a red suite of the builder's own goes back first, and a
# held-out run on code that fails its own tests would only tell the fix round
# less. Prints one line, `<gate> <verify-word> <heldout-word>`, where an
# unrun step is SKIPPED and a word that is not one upper-case token is
# INVALID. The rc mirrors <gate>:
#   0  REVIEW       verify PASS|NONE and held-out PASS|NONE
#   1  FIX_VERIFY   verify FAIL; the feedback is VERIFY_REPORT_FILE
#   1  FIX_HELDOUT  verify PASS|NONE, held-out FAIL; the feedback is
#                   HELDOUT_REPORT_FILE. Never REVIEW.
#   2  STOP         any other word from either step (ERROR included)
heldout_gate () {
  local log="$1" root="$2" verify_fn="$3" heldout_fn="$4" v h
  v="$(_heldout_word "$("$verify_fn" "$log")")"
  case "$v" in
    PASS|NONE) ;;
    FAIL) echo "FIX_VERIFY $v SKIPPED"; return 1 ;;
    *) echo "STOP $v SKIPPED"; return 2 ;;
  esac
  h="$(_heldout_word "$("$heldout_fn" "$log" "$root")")"
  case "$h" in
    PASS|NONE) echo "REVIEW $v $h"; return 0 ;;
    FAIL) echo "FIX_HELDOUT $v $h"; return 1 ;;
    *) echo "STOP $v $h"; return 2 ;;
  esac
}
