#!/bin/bash
# Termination classification for the agentlab pipeline's own run logs.
#
# This file decides how a `logs/run-*.log` ENDED — one file at a time
# (`classify_run_log`), or for every log in an observation window at once
# (`run_log_manifest`, which is the same question asked of a set and answered by
# delegating each file back to `classify_run_log`). It reads; it never writes,
# never runs a phase, never calls a model, and never touches git — which is what
# makes it testable offline (see .pipeline/test_run_log.sh).
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
# Sourcing this file defines three public functions (classify_run_log,
# run_log_in_window, run_log_manifest), three private helpers and four
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

# --- Window membership ------------------------------------------------------

# The sentinel <cutoff> meaning "no date filter at all". run.sh's PIPE_CUTOFF
# defaults to exactly this string when no previous observation exists to bound
# the window with, and its phase prompt already promises that it covers every
# run log present. Named once because both functions below have to agree with
# run.sh on the spelling.
RUN_LOG_CUTOFF_ALL='ALL'

# A run log's filename as run.sh writes it: `run-$TS.log`, with
# TS=`date +%Y-%m-%d_%H%M%S`. Captures \1 = the embedded YYYY-MM-DD date.
#
# The optional `_<tag>` tail is not decoration. Five real logs on this machine
# carry a hand-added tag between the timestamp and `.log`
# (`run-2026-08-06_153448_resume.log`, `run-2026-07-29_221707_fixcycle.log`,
# `run-2026-07-29_232807_skills-research.log`, ...) from resume and fix-cycle
# invocations. They are `run-*.log` files with a real date in them, so the
# window has to be able to judge them: treating them as unparseable would make
# a dated window quietly narrower than the `logs/run-*.log` set run.sh's phase
# prompt promises to cover.
RUN_LOG_NAME_PATTERN='^run-\([0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]\)_[0-9][0-9][0-9][0-9][0-9][0-9]\(_[^/]*\)\{0,1\}\.log$'

# Whether the run log named <basename> falls in the observation window that
# starts at <cutoff>.
#
# Pure string logic: opens no file, lists no directory, reads no clock. A
# leading path on <basename> is ignored (only the final component is read), so
# `logs/run-X.log` and `run-X.log` answer identically. <cutoff> is either the
# sentinel `ALL` or a `YYYY-MM-DD` date.
#
# The window is INCLUSIVE of <cutoff>: run.sh's PIPE_CUTOFF is the date of the
# last observation, whose own night was excluded from that run (its log had no
# closing line yet) and is therefore only covered now — see the comment above
# PIPELINE_OBSERVER_CADENCE_DAYS in run.sh, and the "dated on or after" wording
# of the phase prompt. This function does not invent that boundary.
#
# ISO 8601 dates compare correctly as strings, which is the property run.sh's
# own PIPE_LAST_DATE/LAST_DATE cadence extraction already relies on inline in
# two places; pinning it in one tested function narrows that duplication rather
# than adding a technique. SHAPE only, not calendar validity:
# `run-2026-13-45_999999.log` is a well-formed name with an impossible date and
# is compared as text. Nothing downstream does arithmetic on it, so a nonsense
# date sorts somewhere harmless instead of aborting a whole listing.
#
# `ALL` is answered BEFORE the name is parsed, deliberately: the sentinel means
# there is no date to compare, so there is no judgement to fail, and an ALL
# window must cover every run log present (that is what run.sh's prompt says it
# means). A name with no parseable date is thus IN an ALL window and
# unjudgeable only against a dated one.
#
# Prints nothing on stdout. The return code is the answer:
#   0  in the window
#   1  before the window (the embedded date is strictly older than <cutoff>)
#   2  cannot be judged, and deliberately not guessed either way — either
#      <basename> carries no parseable `run-YYYY-MM-DD_HHMMSS[_tag].log` stem,
#      or <cutoff> is neither `ALL` nor a `YYYY-MM-DD` date. The second is a
#      caller bug and says so on stderr; the first is a fact about a filename
#      and is silent, because its caller (run_log_manifest) reports it as a
#      manifest line instead.
run_log_in_window () {
  local name="${1:-}" cutoff="${2:-}"

  case "$cutoff" in
    "$RUN_LOG_CUTOFF_ALL")
      return 0
      ;;
    [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9])
      ;;
    *)
      printf '%s\n' "run_log_in_window: cutoff must be '$RUN_LOG_CUTOFF_ALL' or YYYY-MM-DD, got '$cutoff'" >&2
      return 2
      ;;
  esac

  local base date
  base="${name##*/}"
  date="$(printf '%s\n' "$base" | sed -n "s|${RUN_LOG_NAME_PATTERN}|\1|p")"
  if [ -z "$date" ]; then
    return 2
  fi

  # String comparison, never -lt: the two dates come out of a filename and a
  # prompt variable as text, and a malformed pair must not reach arithmetic
  # evaluation to be judged (same rule classify_run_log applies to the shipped
  # counts).
  if [ "$date" \< "$cutoff" ]; then
    return 1
  fi
  return 0
}

# --- Manifest ---------------------------------------------------------------

# <path> with its directory component resolved to a physical absolute path, on
# stdout, for equality comparison between two spellings of one file. bash 3.2
# has no `realpath`; `cd` + `pwd -P` in a command substitution is the portable
# equivalent and resolves symlinked directories too.
#
# `CDPATH=''` is load-bearing, not hygiene: with CDPATH exported, `cd logs`
# can resolve to an entirely different directory AND echo the path it chose on
# stdout, which would both misresolve the comparison and inject a line into the
# manifest.
#
# Failure modes: if the directory cannot be entered (missing, or not
# searchable) the path is printed unchanged. Equality then fails instead of
# matching the wrong file, which is the safe direction here — a run log that
# should have been excluded shows up as one visible extra line, whereas a
# wrong match would hide a real run log with no trace.
_run_log_resolved () {
  local path="${1:-}" dir base abs
  base="${path##*/}"
  case "$path" in
    */*) dir="${path%/*}" ;;
    *)   dir="." ;;
  esac
  [ -n "$dir" ] || dir="/"
  abs="$(CDPATH='' cd -P "$dir" 2>/dev/null && pwd -P)" || abs=""
  if [ -n "$abs" ]; then
    printf '%s\n' "${abs%/}/$base"
  else
    printf '%s\n' "$path"
  fi
}

# How every run log in <logs_dir> that falls in <cutoff>'s window ended, one
# line per file on stdout:
#
#   <basename>|<STATE>|<reason>
#
# where `<STATE>|<reason>` is byte-for-byte what classify_run_log printed for
# that file. This function adds no classification logic of its own and matches
# no wording from run.sh — the same coupling-avoidance rule the constants at
# the top of this file document. A consumer splits on the first two delimiters
# only: the reason can itself contain `|`.
#
# Lines are sorted by filename, which for `run-$TS.log` sorts chronologically,
# oldest first.
#
# <exclude_path> is dropped from the listing: run.sh passes the log of the run
# that is calling this, which has not written its closing line yet and would
# otherwise be classified ABORTED every single time. Both sides are compared
# with their directory resolved, so `logs/run-X.log` and
# `/Users/.../agentlab/logs/run-X.log` are recognised as one file. The empty
# string excludes nothing.
#
# Two line shapes report a file the manifest could not judge, instead of
# dropping it. A manifest that is silently short is worse than the by-eye
# reading it replaces, because its consumer cannot tell "checked, nothing
# there" from "never looked":
#
#   <basename>|UNREADABLE|-   classify_run_log could not read the file (its 3)
#   <basename>|UNDATED|-      the name carries no parseable date to compare
#                             against a dated cutoff (run_log_in_window's 2)
#
# Nothing else reaches stdout. An empty window prints nothing and succeeds: no
# run log in range is a result, not a failure — the same posture as backlog.sh's
# zero-unclaimed-items case. classify_run_log's own stderr line for an
# unreadable file is left on stderr for the caller's log; only stdout is the
# contract.
#
# Failure modes:
#   0  a listing that can be trusted, including an empty one
#   1  a listing that cannot be trusted, with one line on stderr saying which:
#      <logs_dir> is missing, not a directory, or not readable/searchable;
#      <cutoff> is neither `ALL` nor a `YYYY-MM-DD` date; or this was called
#      with other than three arguments. None of these is ever reported as an
#      empty manifest, which a consumer would read as "checked, and there is
#      nothing".
run_log_manifest () {
  if [ "$#" -ne 3 ]; then
    printf '%s\n' "run_log_manifest: usage: run_log_manifest <logs_dir> <cutoff> <exclude_path>" >&2
    return 1
  fi
  local dir="$1" cutoff="$2" exclude="$3"

  # -x as well as -r: a directory can be readable and not searchable, in which
  # case the glob below silently expands to nothing and an empty manifest would
  # claim the window was checked.
  if [ ! -d "$dir" ] || [ ! -r "$dir" ] || [ ! -x "$dir" ]; then
    printf '%s\n' "run_log_manifest: not a listable directory: '$dir'" >&2
    return 1
  fi

  # Validated once, here at the boundary, so the per-file loop below cannot see
  # run_log_in_window's cutoff error at all: every 2 it returns inside this
  # function is about a filename.
  case "$cutoff" in
    "$RUN_LOG_CUTOFF_ALL"|[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) ;;
    *)
      printf '%s\n' "run_log_manifest: cutoff must be '$RUN_LOG_CUTOFF_ALL' or YYYY-MM-DD, got '$cutoff'" >&2
      return 1
      ;;
  esac

  local exclude_resolved=""
  if [ -n "$exclude" ]; then
    exclude_resolved="$(_run_log_resolved "$exclude")"
  fi

  local path base out rc
  for path in "$dir"/run-*.log; do
    # An unmatched glob stays literal in bash 3.2 without nullglob, and this
    # file will not set nullglob: a sourced library must not change its
    # caller's shell options.
    [ -e "$path" ] || continue

    if [ -n "$exclude_resolved" ] && [ "$(_run_log_resolved "$path")" = "$exclude_resolved" ]; then
      continue
    fi

    base="${path##*/}"
    run_log_in_window "$base" "$cutoff"
    rc=$?
    if [ "$rc" -eq 1 ]; then
      continue
    fi
    if [ "$rc" -eq 2 ]; then
      printf '%s\n' "$base|UNDATED|-"
      continue
    fi

    out="$(classify_run_log "$path")"
    rc=$?
    if [ "$rc" -eq 3 ]; then
      printf '%s\n' "$base|UNREADABLE|-"
      continue
    fi
    printf '%s\n' "$base|$out"
  done
  return 0
}

# CLI: `bash .pipeline/run_log.sh <path-to-run-log>` prints the one line
# classify_run_log would and exits with its code — useful by hand on one log.
# The manifest over a whole window has no CLI: its only caller is run.sh, which
# sources this file in its library loop and splices the manifest into the
# pipeline-observer phase prompt. Sourcing takes the false branch, prints
# nothing and leaves rc 0.
if [ "${BASH_SOURCE[0]:-}" = "$0" ]; then
  if [ "$#" -ne 1 ]; then
    printf '%s\n' "usage: bash ${BASH_SOURCE[0]:-run_log.sh} <path-to-run-log>" >&2
    exit 3
  fi
  classify_run_log "$1"
  exit $?
fi
