#!/bin/bash
# setup_runner.sh — makes the dedicated runner clone the place the nightly
# pipeline runs from.
#
# Usage: bash .pipeline/setup_runner.sh [--dry-run] [--no-launchd]
#   --dry-run     print what each step would do; change nothing
#   --no-launchd  build or refresh the clone but leave the launchd job alone
#                 (stage and inspect the clone first, switch the job later)
#
# Run it by hand from the human's clone (~/agentlab), after merging. The runner
# defaults to ~/agentlab-runner; set AGENTLAB_RUNNER_DIR to put it elsewhere.
#
# Declarative and idempotent: each step checks whether its desired state
# already holds and acts only when it does not, so a second run changes
# nothing and says so. Steps, in order:
#   1. clone       the runner is a clone of this clone's `origin`
#   2. identity    it commits as this clone's local user.name / user.email
#   3. logs        it starts with this clone's logs/, copied once, so the weekly
#                  health/observer cadence and the observer keep their history
#   4. deps        the hn-search venv exists (deps.sh)
#   5. launchd     the job runs <runner>/.pipeline/run.sh (runner.sh renders it)
# The job is switched last, so a failure in 1-4 leaves the old job untouched.
#
# Fails fast: the first step that fails stops the script with exit 1 and says
# what state it left. Exit 2 on a usage error. Refuses to touch the job while a
# night is running. Not covered: if the runner clone is later deleted, launchd
# fails before run.sh starts and no morning alert fires (PIPELINE.md).

set -uo pipefail

DRY_RUN=0
LAUNCHD=1
for arg in "$@"; do
  case "$arg" in
    --dry-run)    DRY_RUN=1 ;;
    --no-launchd) LAUNCHD=0 ;;
    *) echo "usage: bash .pipeline/setup_runner.sh [--dry-run] [--no-launchd]" >&2; exit 2 ;;
  esac
done

SOURCE="$(cd "$(dirname "$0")/.." && pwd)"
RUNNER="${AGENTLAB_RUNNER_DIR:-$HOME/agentlab-runner}"
PLIST="$HOME/Library/LaunchAgents/com.steeb.agentlab.daily.plist"
PLIST_BACKUP="$SOURCE/logs/launchd-plist-before-runner.plist"
DOMAIN="gui/$(id -u)"

. "$SOURCE/.pipeline/runner.sh" || exit 1
. "$SOURCE/.pipeline/deps.sh" || exit 1

case "$RUNNER" in
  /*) ;;
  *) echo "AGENTLAB_RUNNER_DIR must be an absolute path, got '$RUNNER'." >&2; exit 2 ;;
esac
RUNNER="${RUNNER%/}"
if [ "$RUNNER" = "$SOURCE" ]; then
  echo "The runner cannot be this clone ($SOURCE): the point is that they differ." >&2
  exit 2
fi

CHANGED=0
ok ()    { echo "ok      $1"; }
would () { echo "would   $1"; CHANGED=1; }
did ()   { echo "did     $1"; CHANGED=1; }
die ()   { echo "FAILED  $1" >&2; exit 1; }

# --- 1. clone ---
ORIGIN="$(git -C "$SOURCE" remote get-url origin 2>/dev/null)" \
  || die "clone: $SOURCE has no 'origin' remote to clone from."
if [ -d "$RUNNER/.git" ]; then
  have="$(git -C "$RUNNER" remote get-url origin 2>/dev/null)"
  [ "$have" = "$ORIGIN" ] \
    || die "clone: $RUNNER is a clone of '$have', not '$ORIGIN'. Move it aside by hand."
  ok "clone: $RUNNER is a clone of $ORIGIN"
elif [ -e "$RUNNER" ] && [ -n "$(ls -A "$RUNNER" 2>/dev/null)" ]; then
  die "clone: $RUNNER exists, is not empty, and is not a git clone. Move it aside by hand."
elif [ "$DRY_RUN" -eq 1 ]; then
  would "clone: git clone $ORIGIN $RUNNER"
else
  git clone --quiet "$ORIGIN" "$RUNNER" || die "clone: git clone $ORIGIN $RUNNER failed (nothing else was changed)."
  did "clone: git clone $ORIGIN $RUNNER"
fi
CLONED=0; [ -d "$RUNNER/.git" ] && CLONED=1

# --- 2. identity ---
for key in user.name user.email; do
  want="$(git -C "$SOURCE" config --local "$key" 2>/dev/null)"
  if [ -z "$want" ]; then
    ok "identity: $SOURCE sets no local $key; the runner uses the global one"
    continue
  fi
  have=""; [ "$CLONED" -eq 1 ] && have="$(git -C "$RUNNER" config --local "$key" 2>/dev/null)"
  if [ "$have" = "$want" ]; then
    ok "identity: $key = $want"
  elif [ "$DRY_RUN" -eq 1 ]; then
    would "identity: set $key = $want"
  else
    git -C "$RUNNER" config --local "$key" "$want" || die "identity: could not set $key in $RUNNER."
    did "identity: set $key = $want"
  fi
done

# --- 3. logs ---
# "Once" means: only while the runner has no run log of its own. After its
# first night (or a seeding) it owns its history and this step leaves it alone.
if ls "$RUNNER"/logs/run-*.log >/dev/null 2>&1; then
  ok "logs: $RUNNER/logs has run history (left as it is)"
elif ! ls "$SOURCE"/logs/run-*.log >/dev/null 2>&1; then
  ok "logs: $SOURCE/logs has no run history to carry over"
elif [ "$DRY_RUN" -eq 1 ]; then
  would "logs: copy $SOURCE/logs/ (mtimes kept, so the cadence checks see the real dates)"
else
  mkdir -p "$RUNNER/logs" && cp -Rp "$SOURCE/logs/." "$RUNNER/logs/" \
    || die "logs: copy into $RUNNER/logs failed part-way; remove $RUNNER/logs and rerun."
  did "logs: copy $SOURCE/logs/ (mtimes kept)"
fi

# --- 4. deps ---
if [ -x "$RUNNER/$HN_VENV/bin/python3" ]; then
  ok "deps: $HN_VENV exists"
elif [ "$DRY_RUN" -eq 1 ]; then
  would "deps: build $HN_VENV"
else
  mkdir -p "$RUNNER/logs"
  ( cd "$RUNNER" && bootstrap_runner_deps "$RUNNER/logs/setup-runner-deps.log" ) >/dev/null \
    || die "deps: building $HN_VENV failed (see $RUNNER/logs/setup-runner-deps.log). The launchd job was not touched."
  did "deps: build $HN_VENV"
fi

# --- 5. launchd ---
if [ "$LAUNCHD" -eq 0 ]; then
  ok "launchd: skipped (--no-launchd); the job still runs $(plutil -extract ProgramArguments.1 raw "$PLIST" 2>/dev/null || echo '(no job installed)')"
else
  want_plist="$(render_runner_plist "$RUNNER")" || die "launchd: cannot render a plist for '$RUNNER'."
  loaded=0; launchctl print "$DOMAIN/$RUNNER_LABEL" >/dev/null 2>&1 && loaded=1
  same=0; [ -f "$PLIST" ] && [ "$(cat "$PLIST")" = "$want_plist" ] && same=1
  if [ "$same" -eq 1 ] && [ "$loaded" -eq 1 ]; then
    ok "launchd: $RUNNER_LABEL runs $RUNNER/.pipeline/run.sh"
  elif [ "$DRY_RUN" -eq 1 ]; then
    [ "$same" -eq 0 ] && would "launchd: rewrite $PLIST to run $RUNNER/.pipeline/run.sh (old one saved to $PLIST_BACKUP)"
    would "launchd: reload $RUNNER_LABEL"
  else
    if [ "$loaded" -eq 1 ] && launchctl print "$DOMAIN/$RUNNER_LABEL" 2>/dev/null | grep -q 'state = running'; then
      die "launchd: a night is running right now. Rerun after it ends; nothing was changed."
    fi
    if [ "$same" -eq 0 ]; then
      if [ -f "$PLIST" ] && [ ! -f "$PLIST_BACKUP" ]; then
        mkdir -p "$SOURCE/logs" && cp -p "$PLIST" "$PLIST_BACKUP" \
          || die "launchd: could not save the old plist to $PLIST_BACKUP; nothing was changed."
      fi
      if [ "$loaded" -eq 1 ]; then
        launchctl bootout "$DOMAIN/$RUNNER_LABEL" 2>/dev/null \
          || die "launchd: bootout of $RUNNER_LABEL failed; the old job is still loaded."
        loaded=0
      fi
      printf '%s\n' "$want_plist" > "$PLIST" \
        || die "launchd: could not write $PLIST. The job is UNLOADED; restore with: launchctl bootstrap $DOMAIN $PLIST_BACKUP"
      plutil -lint "$PLIST" >/dev/null \
        || die "launchd: the written $PLIST does not lint. The job is UNLOADED; restore $PLIST_BACKUP and bootstrap it."
      did "launchd: rewrote $PLIST (old one saved to $PLIST_BACKUP)"
    fi
    launchctl enable "$DOMAIN/$RUNNER_LABEL" 2>/dev/null
    launchctl bootstrap "$DOMAIN" "$PLIST" \
      || die "launchd: bootstrap of $PLIST failed. The job is UNLOADED; retry: launchctl bootstrap $DOMAIN $PLIST"
    did "launchd: loaded $RUNNER_LABEL from $PLIST"
  fi
fi

echo ""
if [ "$CHANGED" -eq 0 ]; then
  echo "Nothing to do: the runner at $RUNNER is already in its desired state."
elif [ "$DRY_RUN" -eq 1 ]; then
  echo "Dry run: nothing was changed. Rerun without --dry-run to apply."
else
  echo "Done. The runner is $RUNNER. Rerunning this script should now say 'Nothing to do'."
fi
