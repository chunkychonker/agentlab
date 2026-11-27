#!/bin/bash
# models.sh — which Claude model the build and review phases run on.
#
# Sourced by run.sh. Pure except for reading the one conf file it is handed:
# no env-var reads (run.sh reads AGENTLAB_BUILD_MODEL / AGENTLAB_REVIEW_MODEL
# once and passes the value in), no network, no model. Bash 3.2 compatible.
# Covered offline by test_models.sh.
#
# Why it exists: until 2026-11-27 build and review were both the literal `opus`
# at their run_phase call sites, so the reviewer was an isolated session but
# the same model as the author (research/2026-11-27-cross-model-review.md).
# This makes the reviewer's model a setting, so it can be changed and measured
# (`nightloop bench --model`) without editing run.sh. The default is still
# opus for both; changing it is a human decision made from bench numbers.
#
# Conf format (.pipeline/models.conf), one setting per line:
#   build=<model>
#   review=<model>
# Blank lines and lines starting with `#` are ignored. Spaces around the key
# and value are ignored. A missing file means both phases use $MODELS_DEFAULT.
# Every other irregularity is an error naming the file and line: an unknown
# key, a key set twice, a line with no `=`, an empty or invalid model. There is
# deliberately no fallback to the default on a bad value — a typo that quietly
# restored opus would hide itself, and nothing downstream would surface it.
#
# The fix round reuses the build model; it is the builder finishing its work.

# The model a phase runs on when the conf does not name one.
MODELS_DEFAULT="opus"
# Full model names: `claude-` then lowercase alphanumeric words joined by
# single `.` or `-`, e.g. claude-opus-5-5. Never ends in a separator.
MODELS_FULL_NAME_RE='^claude-[a-z0-9]+([.-][a-z0-9]+)*$'

# is_valid_model <name>
#
# 0 when <name> is an alias run.sh is willing to pass to `claude --model`
# (opus, sonnet, fable) or a full model name matching MODELS_FULL_NAME_RE;
# 1 for anything else, including the empty string. Prints nothing. Does not
# check that the installed CLI knows the model: an unknown-but-well-formed
# name fails in the CLI, which run_phase reports as a failed phase.
is_valid_model () {
  case "$1" in
    opus|sonnet|fable) return 0 ;;
  esac
  local re="$MODELS_FULL_NAME_RE"
  [[ "$1" =~ $re ]]
}

# resolve_phase_model <phase> <conf-path> [override]
#
# Prints the model <phase> (build or review) runs on, and returns 0.
# Precedence: [override] when the argument is present (even if empty), then
# the conf's line for <phase>, then $MODELS_DEFAULT.
#
# The whole conf is validated on every call, override or not, so a broken
# file is reported the night it breaks rather than the night the override is
# dropped.
#
# Returns 1, printing one line to stderr and nothing to stdout, when: <phase>
# is not build or review; <conf-path> exists but cannot be read; a conf line
# has no `=`, an unknown key, a repeated key, or an empty/invalid model
# (message: `<conf-path>:<line>: ...`); or [override] is present but not a
# valid model. Never partially succeeds: on any error, nothing is printed to
# stdout.
resolve_phase_model () {
  local phase="$1" conf="$2"
  case "$phase" in
    build|review) ;;
    *) echo "models: unknown phase '$phase' for $conf (expected build or review)" >&2
       return 1 ;;
  esac

  local build_val="" build_ln="" review_val="" review_ln=""
  if [ -e "$conf" ]; then
    if [ ! -r "$conf" ] || [ -d "$conf" ]; then
      echo "models: $conf exists but cannot be read as a file" >&2
      return 1
    fi
    local line n=0 key val trimmed
    while IFS= read -r line || [ -n "$line" ]; do
      n=$(( n + 1 ))
      # Trim surrounding whitespace (including a CR from a CRLF file).
      trimmed="$(printf '%s' "$line" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
      case "$trimmed" in
        ''|'#'*) continue ;;
        *=*) ;;
        *) echo "$conf:$n: expected 'build=<model>' or 'review=<model>', got '$trimmed'" >&2
           return 1 ;;
      esac
      key="$(printf '%s' "${trimmed%%=*}" | sed 's/[[:space:]]*$//')"
      val="$(printf '%s' "${trimmed#*=}" | sed 's/^[[:space:]]*//')"
      case "$key" in
        build)
          if [ -n "$build_ln" ]; then
            echo "$conf:$n: duplicate key 'build' (already set on line $build_ln)" >&2
            return 1
          fi
          build_ln="$n" ;;
        review)
          if [ -n "$review_ln" ]; then
            echo "$conf:$n: duplicate key 'review' (already set on line $review_ln)" >&2
            return 1
          fi
          review_ln="$n" ;;
        *) echo "$conf:$n: unknown key '$key' (expected build or review)" >&2
           return 1 ;;
      esac
      if [ -z "$val" ]; then
        echo "$conf:$n: empty model for '$key'" >&2
        return 1
      fi
      if ! is_valid_model "$val"; then
        echo "$conf:$n: invalid model '$val' for '$key' (expected opus, sonnet, fable or a full name like claude-opus-5-5)" >&2
        return 1
      fi
      if [ "$key" = build ]; then build_val="$val"; else review_val="$val"; fi
    done < "$conf"
  fi

  if [ "$#" -ge 3 ]; then
    if ! is_valid_model "$3"; then
      echo "models: override '$3' for phase '$phase' is not a valid model (expected opus, sonnet, fable or a full name like claude-opus-5-5)" >&2
      return 1
    fi
    printf '%s\n' "$3"
    return 0
  fi

  local chosen
  if [ "$phase" = build ]; then chosen="$build_val"; else chosen="$review_val"; fi
  printf '%s\n' "${chosen:-$MODELS_DEFAULT}"
}
