#!/bin/bash
# Termination classification for the agentlab pipeline's own run logs.
#
# This file decides how a `logs/run-*.log` ENDED. It reads one file it is
# handed; it never writes, never runs a phase, never calls a model, and never
# touches git — which is what makes it testable offline (see
# .pipeline/test_run_log.sh).
#
# Why it exists: .claude/agents/agentlab-pipeline-observer.md classifies every
# run log into OK / PARTIAL / ABORTED by reading the raw text by eye, every
# run, and its contract states that an aborted run's log ends with a line
# ending in `Aborting.`. That is false for at least one real incident —
# `run-2026-08-29_114701.log` ends at `--- phase: cycle 2/2: review (model:
# opus) ---` with nothing after it at all, because the foreground `$CLAUDE`
# child of run_phase stopped existing and nothing was left to print a reason.
# A trap in run.sh cannot fix that (bash defers a pending trap until the
# foreground child it is blocked on returns, and cannot catch SIGKILL at all —
# measured on this exact bash 3.2.57; see
# knowledge/pipeline-run-log-shapes.md). Structure therefore has to be read off
# the file after the fact, and reading it mechanically is both cheaper and more
# reliable than asking a model that keeps running out of session budget on this
# very phase.
#
# This is STRUCTURE, not CAUSE. "The review phase started and nothing followed"
# is a fact on disk. "The session hit its limit" is a diagnosis, and is
# deliberately not this file's job — the observer's own posture is that a
# confident wrong diagnosis is worse than a count.
#
# Sourcing this file defines one public function, two private helpers and two
# constants. It runs nothing, prints nothing, and touches no files. It is
# written to be safe to source into a shell with `set -uo pipefail` on, so
# every expansion below is guarded and every pipeline that may legitimately
# exit non-zero says so explicitly.
#
# bash 3.2 ONLY (macOS system bash is 3.2.57) — see
# knowledge/bash-3.2-testable-scripts.md.

# The two structural markers run.sh already treats as load-bearing itself, as
# BREs, named once. Both are deliberately the ONLY things matched: the specific
# wording of run.sh's failure lines (`... Aborting.`, `phase '<name>' exited
# non-zero — see <log>`) is NOT matched anywhere in this file, because coupling
# a classifier to another file's prose is coupling-by-meaning (CLAUDE.md §2) —
# a harmless rewording over there would silently break classification here with
# no test catching it, since the two files are not edited together. Unmatched
# endings are reported by quoting the log's last line verbatim instead, which
# is the same diagnostic value without the coupling.

# run.sh's one closing line: `=== done $TS === (shipped $SHIPPED/$CYCLES, ...)`.
# Captures \1 = shipped, \2 = cycles. $TS never contains a space.
RUN_LOG_DONE_PATTERN='^=== done [^ ][^ ]* === (shipped \([0-9][0-9]*\)/\([0-9][0-9]*\),'

# run_phase's announcement: `--- phase: $name (model: $model) ---`.
# Captures \1 = phase name, \2 = model.
#
# Only the model-bearing form is matched, and that is the meaningful set rather
# than an accident: run_phase is the one place that hands control to a
# foreground `$CLAUDE` child, so it is the one place a run can die with no
# further output. run.sh's other `--- phase: ... ---` lines (auto-merge, the
# two reconcile passes, the findings-filing passes, the `... skipped` notices)
# print no model because they run no model, and a run that dies during one of
# those is reported through the verbatim-last-line branch below.
RUN_LOG_PHASE_PATTERN='^--- phase: \(.*\) (model: \([^)]*\)) ---$'

# The log's last line with anything but whitespace on it, on stdout; empty if
# there is none. `-a` is load-bearing: a phase's captured output can contain a
# NUL byte, and without it grep answers "Binary file ... matches" instead of
# the line. Failure modes: an unreadable path yields empty output here, which
# is why classify_run_log validates before calling this.
_run_log_last_content_line () {
  local out
  out="$(grep -a -v '^[[:space:]]*$' "$1" | tail -1)" || out=""
  printf '%s\n' "$out"
}

# The LAST `--- phase: NAME (model: MODEL) ---` line in the log, on stdout;
# empty if there is none. Last, not first: a run log holds one such line per
# phase per cycle, and only the final one describes what was running when the
# log stopped. Failure modes: as above.
_run_log_last_phase_line () {
  local out
  out="$(grep -a "$RUN_LOG_PHASE_PATTERN" "$1" | tail -1)" || out=""
  printf '%s\n' "$out"
}

# How the run log at <path> ended, on stdout, as exactly one line
# `<STATE>|<reason>` with STATE one of OK / PARTIAL / ABORTED.
#
# The reason may itself contain `|` (it can quote an arbitrary line of a
# phase's output), so a consumer must split on the FIRST delimiter only —
# `state="${line%%|*}"`, `reason="${line#*|}"` — or branch on the return code,
# which carries the same information.
#
# OK and PARTIAL are decided from run.sh's closing line and only when it is the
# log's LAST content line. Anywhere else it is not evidence the run finished:
# the pipeline-observer phase reads old run logs and echoes their closing lines
# into the log of the run it is currently part of. Fails closed — a closing
# line this function cannot parse with certainty is ABORTED, not OK.
#
# ABORTED's reason is ALWAYS non-empty and is built structurally, never by
# matching run.sh's failure wording (see the constants above).
#
# Failure modes (the return code mirrors the state, so a caller may branch on
# either, and 3 is distinguishable from every classification):
#   0  OK       last content line is the closing line, shipped N of N
#   1  PARTIAL  last content line is the closing line, shipped X of N, X != N
#               (X > N cannot come out of run.sh; it fails closed to here
#               rather than claiming everything shipped)
#   2  ABORTED  no parseable closing line as the last content line. Reason is
#               one of: the last model phase started with nothing after it;
#               the last model phase named plus the verbatim last line; no
#               model phase at all plus the verbatim last line; or, for a log
#               with no content, `(empty log)` / `(no content: ...)`
#   3  input error — <path> is missing, empty-string, a directory, or
#               unreadable. Nothing on stdout, one line on stderr. An
#               unreadable log is not evidence about a run, so this is not
#               folded into ABORTED.
classify_run_log () {
  local path="${1:-}"

  if [ ! -f "$path" ] || [ ! -r "$path" ]; then
    printf '%s\n' "classify_run_log: not a readable file: '$path'" >&2
    return 3
  fi

  # printf, not echo, for every line below: the reason can embed an arbitrary
  # line of a phase's output, and `echo` would eat one beginning with -n/-e.
  local last
  last="$(_run_log_last_content_line "$path")"
  if [ -z "$last" ]; then
    if [ -s "$path" ]; then
      printf '%s\n' "ABORTED|(no content: log is whitespace only)"
    else
      printf '%s\n' "ABORTED|(empty log)"
    fi
    return 2
  fi

  local counts
  counts="$(printf '%s\n' "$last" | sed -n "s|${RUN_LOG_DONE_PATTERN}.*|\1 \2|p")"
  if [ -n "$counts" ]; then
    local shipped cycles
    shipped="${counts%% *}"
    cycles="${counts##* }"
    # String equality, not -eq: the counts come out of the log as text, and a
    # malformed pair must not reach arithmetic evaluation to be judged.
    if [ "$shipped" = "$cycles" ]; then
      printf '%s\n' "OK|shipped $shipped/$cycles"
      return 0
    fi
    printf '%s\n' "PARTIAL|shipped $shipped/$cycles"
    return 1
  fi

  local phase_line
  phase_line="$(_run_log_last_phase_line "$path")"
  if [ -z "$phase_line" ]; then
    printf '%s\n' "ABORTED|no model phase started; last line: $last"
    return 2
  fi

  local name model
  name="$(printf '%s\n' "$phase_line" | sed -n "s|${RUN_LOG_PHASE_PATTERN}|\1|p")"
  model="$(printf '%s\n' "$phase_line" | sed -n "s|${RUN_LOG_PHASE_PATTERN}|\2|p")"

  # The silent shape: the announcement of a phase is the last thing in the
  # file. Nothing printed a reason because nothing was left running to print
  # one. This is the 2026-08-29 incident exactly.
  if [ "$phase_line" = "$last" ]; then
    printf '%s\n' "ABORTED|phase '$name' (model: $model) started, nothing followed"
    return 2
  fi

  printf '%s\n' "ABORTED|phase '$name' (model: $model) last started; last line: $last"
  return 2
}

# CLI: `bash .pipeline/run_log.sh <path-to-run-log>` prints the one line
# classify_run_log would and exits with its code. Useful by hand today; this
# file is not wired into run.sh or any agent yet. Sourcing takes the false
# branch, prints nothing and leaves rc 0.
if [ "${BASH_SOURCE[0]:-}" = "$0" ]; then
  if [ "$#" -ne 1 ]; then
    printf '%s\n' "usage: bash ${BASH_SOURCE[0]:-run_log.sh} <path-to-run-log>" >&2
    exit 3
  fi
  classify_run_log "$1"
  exit $?
fi
