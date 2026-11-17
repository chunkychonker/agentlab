# The verify gate for the agentlab pipeline: run the increment's tests,
# mechanically, between build and review.
#
# Why it exists: until 2026-11-15 the only test runs in a cycle were the ones
# an agent chose to do. The builder is told to verify and the reviewer to run
# the tests, and both usually do, but an instruction is not a gate (the same
# argument as verdict.sh). A build whose tests fail now never reaches the
# reviewer: the failure goes straight back to the builder as a fix round,
# which saves the opus review a red test suite would have wasted.
#
# What the agent sees is deliberately small (HumanLayer's "context-efficient
# backpressure"): a passing command is one line, a failing one is its last
# VERIFY_REPORT_TAIL_LINES lines of output.
#
# What runs is data, not code: VERIFY_CONF maps a path prefix to a command.
# Nothing here discovers test frameworks.
#
# Blast radius: commands run builder-written test code in the caller's shell
# environment, as the reviewer's own test run already does. A hung command
# costs at most VERIFY_TIMEOUT_S before it and its children are killed.
#
# Sourcing this file defines functions and constants. It runs nothing, prints
# nothing, and touches no files. Safe under `set -uo pipefail`.
#
# bash 3.2 ONLY (macOS system bash is 3.2.57). Needs perl (for the timeout;
# macOS has no timeout(1)) and git.

VERIFY_CONF=".pipeline/verify.conf"
# The failed run's output, for the fix phase. Git-ignored, like every handoff
# file under logs/.
VERIFY_REPORT_FILE="logs/last-verify.md"
# Per rule. nightloop's suite takes ~15s and the pipeline suites a few
# minutes; ten minutes means "hung", not "slow".
VERIFY_TIMEOUT_S=600
VERIFY_REPORT_TAIL_LINES=200
# What a timed-out command returns, the same number timeout(1) uses.
VERIFY_TIMEOUT_RC=124

# _verify_trim: stdin with leading and trailing blanks removed.
_verify_trim () { sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'; }

# verify_plan <conf> <changed-paths-file>
#
# Which rules apply to this change. Reads the two files; writes nothing.
# Prints `label<TAB>command` for every rule whose prefix starts at least one
# changed path, in conf order, each rule once. Blank lines and lines whose
# first non-blank character is `#` are skipped.
#   0  a plan, possibly empty
#   2  the conf or the paths file cannot be read, or any conf line is not
#      three non-empty `|`-separated fields. Prints no rule at all in that
#      case, and says which line on stderr: a conf that cannot be read in
#      full must not run half its checks and call that a pass.
verify_plan () {
  local conf="$1" changed="$2"
  if [ ! -r "$conf" ]; then echo "verify_plan: cannot read conf '$conf'" >&2; return 2; fi
  if [ ! -r "$changed" ]; then echo "verify_plan: cannot read changed-paths file '$changed'" >&2; return 2; fi
  local line n=0 prefix label rest command plan=""
  while IFS= read -r line || [ -n "$line" ]; do
    n=$(( n + 1 ))
    case "$(printf '%s' "$line" | _verify_trim)" in ''|'#'*) continue ;; esac
    case "$line" in
      *'|'*'|'*) ;;
      *) echo "verify_plan: $conf line $n is not 'prefix | label | command'" >&2; return 2 ;;
    esac
    prefix="$(printf '%s' "${line%%|*}" | _verify_trim)"
    rest="${line#*|}"
    label="$(printf '%s' "${rest%%|*}" | _verify_trim)"
    command="$(printf '%s' "${rest#*|}" | _verify_trim)"
    if [ -z "$prefix" ] || [ -z "$label" ] || [ -z "$command" ]; then
      echo "verify_plan: $conf line $n has an empty field" >&2; return 2
    fi
    # Fixed-string prefix match at the start of a line: awk index() == 1.
    if awk -v p="$prefix" 'index($0, p) == 1 { found = 1; exit } END { exit !found }' "$changed"; then
      plan="$plan$label	$command
"
    fi
  done < "$conf"
  printf '%s' "$plan"
  return 0
}

# verify_run <label> <command> <report> <timeout-s>
#
# Runs <command> under bash in the current directory, in its own process
# group. Prints exactly one line:
#   ✓ <label>             and returns 0; the command's output is discarded
#   ✗ <label> (rc N)      and returns N; appends the label, the command, the
#                         rc and the last VERIFY_REPORT_TAIL_LINES lines of
#                         the output (stdout and stderr together) to <report>
# A command still running after <timeout-s> seconds is sent TERM, then KILL,
# along with every process in its group, and counts as a failure with
# N = VERIFY_TIMEOUT_RC. If <report> cannot be written the failure still
# returns N; the write error goes to stderr.
verify_run () {
  local label="$1" command="$2" report="$3" timeout="$4"
  local out rc=0
  out="$(mktemp)" || return 1
  # fork + setpgrp so the alarm can kill the whole group: a plain `alarm; exec`
  # would kill bash and leave the test runner it started still running.
  perl -e '
    my ($t, $rc_timeout, @cmd) = @ARGV;
    my $pid = fork();
    die "fork: $!" unless defined $pid;
    if (!$pid) { setpgrp(0, 0); exec @cmd; exit 127; }
    $SIG{ALRM} = sub {
      kill "TERM", -$pid; sleep 2; kill "KILL", -$pid;
      waitpid($pid, 0); exit $rc_timeout;
    };
    alarm $t;
    waitpid($pid, 0);
    my $s = $?;
    exit(($s & 127) ? 128 + ($s & 127) : $s >> 8);
  ' "$timeout" "$VERIFY_TIMEOUT_RC" bash -c "$command" >"$out" 2>&1 </dev/null || rc=$?
  if [ "$rc" -eq 0 ]; then
    echo "✓ $label"
    rm -f "$out"
    return 0
  fi
  echo "✗ $label (rc $rc)"
  local total
  total="$(wc -l < "$out" | tr -d ' ')"
  {
    echo "## ✗ $label"
    echo ""
    echo "command: $command"
    echo "exit code: $rc"
    [ "$rc" -eq "$VERIFY_TIMEOUT_RC" ] && echo "timed out after ${timeout}s and was killed"
    echo ""
    if [ "$total" -gt "$VERIFY_REPORT_TAIL_LINES" ]; then
      echo "output (last $VERIFY_REPORT_TAIL_LINES of $total lines; re-run the command for the rest):"
    else
      echo "output:"
    fi
    tail -n "$VERIFY_REPORT_TAIL_LINES" "$out"
    echo ""
  } >> "$report"
  rm -f "$out"
  return "$rc"
}

# verify_increment <log>
#
# Verifies the working tree in the current directory, which must be a git
# repo root: lists what changed (tracked edits and untracked files alike, the
# builder commits nothing), plans from VERIFY_CONF, and runs every planned
# command even after one fails, so the fix phase sees all the failures at
# once. Each command's one-line result is appended to <log>. Removes
# VERIFY_REPORT_FILE first, so the report only ever describes this run.
#
# Prints exactly one word; the return code mirrors it:
#   0  PASS   every planned command succeeded
#   0  NONE   no rule matched what changed; nothing was run
#   1  FAIL   at least one command failed; VERIFY_REPORT_FILE has the output
#   2  ERROR  the changed paths could not be listed, or verify_plan failed.
#             Never treated as a pass by the caller: an unreadable conf is
#             not evidence the tests are green.
verify_increment () {
  local log="$1"
  rm -f "$VERIFY_REPORT_FILE"
  local status changed plan
  # Captured before it is parsed, so git's own exit status is what is tested
  # whether or not the caller has pipefail on.
  if ! status="$(git status --porcelain --untracked-files=all 2>>"$log")"; then
    echo "ERROR"; return 2
  fi
  changed="$(mktemp)" || { echo "ERROR"; return 2; }
  # Columns 1-3 are the status; a rename prints `old -> new`, and the new
  # path is the one that exists.
  printf '%s\n' "$status" | sed -e 's/^...//' -e 's/^.* -> //' > "$changed"
  if ! plan="$(verify_plan "$VERIFY_CONF" "$changed" 2>>"$log")"; then
    rm -f "$changed"; echo "ERROR"; return 2
  fi
  rm -f "$changed"
  if [ -z "$plan" ]; then echo "NONE"; return 0; fi

  local label command failed=0
  while IFS='	' read -r label command; do
    verify_run "$label" "$command" "$VERIFY_REPORT_FILE" "$VERIFY_TIMEOUT_S" >>"$log" 2>&1 || failed=1
  done <<< "$plan"
  if [ "$failed" -eq 1 ]; then echo "FAIL"; return 1; fi
  echo "PASS"; return 0
}
