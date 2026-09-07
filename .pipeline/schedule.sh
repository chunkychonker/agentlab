# Launch-window classification for the agentlab pipeline's preflight.
#
# This file decides whether the clock says the night's run should happen at
# all. It reads an hour it is handed; it never calls date, never aborts, and
# never prints outside its one stdout word — which is what makes it testable
# offline (see .pipeline/test_gates.sh). run.sh owns the acting-on-it half.
#
# Why it exists: the launchd slot is a REQUEST, not a guarantee. Two separate
# mechanisms can land this job in the middle of a workday:
#
#   - launchd runs a MISSED calendar job at the next wake. A Mac asleep at
#     02:00 therefore starts the night's run whenever the lid next opens.
#   - something less understood. Between 2026-08-29 and 2026-09-03 six
#     consecutive runs fired in the 11:47 and 13:47 hours with the minute
#     still pinned to the configured :47 — a pattern a lid-open wake does not
#     produce. That cause is UNPROVEN as of 2026-09-04; this guard
#     deliberately does not depend on knowing it.
#
# Either way the damage is the same and it is not subtle: a multi-cycle Opus
# job starting at 13:47 spends the rolling 5-hour usage window that
# interactive afternoon work needs. Refusing to start costs one night.
# Starting at the wrong hour costs the rest of the day.
#
# The tradeoff this accepts: a missed night STAYS missed. That is the point.
# `pmset repeat wakeorpoweron` is the complementary half that makes nights not
# be missed, and it needs root; this half needs nothing and also covers the
# drift, so the two are worth having together rather than either alone.
#
# Sourcing this file defines two functions and three constants. It runs
# nothing, prints nothing, and touches no files. run.sh sources it into a
# shell with `set -uo pipefail` on, so every expansion below must be safe
# under -u.
#
# bash 3.2 ONLY (macOS system bash is 3.2.57) — see
# knowledge/bash-3.2-testable-scripts.md.

# The overnight window, as hours on a 24h clock: START inclusive, END
# EXCLUSIVE. 1..5 means a run may START at 01:00 through 05:59. The end is
# exclusive because a cycle takes 20-25 minutes and a full night takes hours —
# a job that STARTS at 06:00 is still running well into the morning, which is
# the thing being prevented. These are the only two magic numbers here and
# they are named once; the launchd slot (02:00) must sit inside them.
WINDOW_START_HOUR=1
WINDOW_END_HOUR=6

# Set this in the environment to run deliberately off-schedule; see
# schedule_override_active. Named here so run.sh's message and the tests
# cannot drift from the variable they describe.
SCHEDULE_OVERRIDE_VAR="AGENTLAB_IGNORE_SCHEDULE"

# Whether a human is deliberately running this off-schedule, by return code
# only: 0 = override in force (run anyway), 1 = no override (guard applies).
#
# It exists because .pipeline/run.sh's own header tells you to "Run manually
# first to shake out PATH/auth" — and shaking out PATH/auth at 2am is not a
# thing anyone should have to do. Without an escape hatch this guard breaks
# the documented first-run workflow.
#
# Prints nothing and takes no argument. It is the ONE part of this file
# allowed to look at the environment, because "is a human driving this" is not
# a fact about the clock and cannot be passed in as one. Must be safe under
# `set -u`: the override variable is normally UNSET, so a bare "$FOO" here is
# a crash, not a false.
#
# ANY non-empty value overrides, so AGENTLAB_IGNORE_SCHEDULE=0 and =false both
# mean "run anyway". That is deliberate, not an oversight: the only way this
# variable gets a value at all is a human typing it in front of one command,
# so the typing IS the intent. Parsing it as a boolean would make the hatch
# silently do nothing for whoever assumed it was one, with no output saying
# why. To turn the override off, unset it or set it empty.
schedule_override_active () {
  # Read through the constant rather than spelling the name a second time, so
  # this, run.sh's refusal message, and the tests cannot drift apart. ${!x:-}
  # is indirect expansion with an empty default, and that default is the part
  # that survives `set -u` on the ~360 nights a year the variable is unset.
  # `[ -n ]` then supplies the return code directly: 0 for non-empty, 1 for
  # empty-or-unset, and nothing on stdout either way.
  [ -n "${!SCHEDULE_OVERRIDE_VAR:-}" ]
}

# What the hour <hour> means for the night, on stdout, as exactly one of
# RUN / REFUSE.
#
# The argument is an hour on a 24h clock as a STRING, normally `date +%H`,
# which zero-pads: "00".."09" arrive with a leading zero. Those are forced
# through base 10 before any arithmetic — in every bash ARITHMETIC context a
# leading zero means octal, and 08 and 09 are not octal, so `$(( $1 ))`,
# `(( $1 >= 1 ))` and `[[ $1 -ge 1 ]]` each abort with "value too great for
# base": every day, but only in the 8am and 9am hours.
#
# The POSIX `[` builtin is the exception — it parses base 10, so
# `[ "$1" -ge 1 ]` is genuinely safe. That exception is a trap of its own,
# because it keeps the bug invisible until someone switches to `[[` or adds
# one line of arithmetic. Verified on bash 3.2.57, 2026-09-04.
#
# Rejects anything that is not one or two digits BEFORE doing arithmetic on
# it: with a malformed operand the shell's own error is the only output, and a
# guard whose failure mode is "prints a shell error and returns something" is
# worse than no guard. An unreadable clock is not evidence that it is 2am, so
# a bad hour REFUSES rather than defaulting to RUN — the safe direction is
# always "do not spend the day's tokens".
#
# Failure modes (the return code mirrors the word on stdout, so a caller may
# branch on either):
#   0  RUN     hour is inside [WINDOW_START_HOUR, WINDOW_END_HOUR)
#   1  REFUSE  hour is outside that window, OR is empty, non-numeric,
#              or not a valid hour 0-23
schedule_disposition () {
  local hour="$1"

  case "$hour" in
    ''|*[!0-9]*) echo "REFUSE"; return 1 ;;
  esac
  if [ "${#hour}" -gt 2 ]; then
    echo "REFUSE"
    return 1
  fi

  # Base 10 forced: see the octal note above.
  local h
  h=$(( 10#$hour ))
  if [ "$h" -gt 23 ]; then
    echo "REFUSE"
    return 1
  fi

  if [ "$h" -ge "$WINDOW_START_HOUR" ] && [ "$h" -lt "$WINDOW_END_HOUR" ]; then
    echo "RUN"
    return 0
  fi
  echo "REFUSE"
  return 1
}
