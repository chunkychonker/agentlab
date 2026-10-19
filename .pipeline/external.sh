#!/bin/bash
# external.sh — model-free plumbing for `external:<slug>` mode: working a repo
# agentlab does not own, by PR, never by merge.
#
# Sourced by run.sh. The pure functions (ext_target_field, ext_protected_hits)
# are covered offline by test_external.sh; the git functions are covered there
# against a local bare repo, so no test touches the network.
#
# The target is described by projects/<slug>/target, one `key=value` per line:
#   repo=<owner>/<name>        GitHub repo the PRs go to
#   base=<branch>              branch every cycle starts from and PRs target
#   verify=<path>              gate script, run as `bash <path> <checkout>`
#   protect=<path>             (repeatable) file in the target a cycle must not touch
#   hot_hours=<int>            files changed on base this recently are off limits
# Config is data: parsed once by run.sh via ext_target_field, then passed down.

# Where external checkouts live. Outside ~/agentlab on purpose: the target's code
# must never be committed into this public repo.
EXT_WORK_ROOT="${EXT_WORK_ROOT:-$HOME/agentlab-work}"

# Per-cycle handoff files (git-ignored under logs/), read by the subagents.
EXT_CONTEXT_FILE="logs/ext-context.md"
EXT_VERIFY_FILE="logs/last-verify.txt"

# ext_target_field <target-file> <key>
# Prints every value for <key>, one per line, in file order.
# Returns 0 if at least one value was found; 1 if none; 2 if the file is unreadable.
# Blank lines and `#` comments are skipped; whitespace around key and value is trimmed.
ext_target_field () {
  local file="$1" key="$2" line k v found=1
  [ -r "$file" ] || return 2
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%%#*}"
    case "$line" in *=*) ;; *) continue ;; esac
    k="$(printf '%s' "${line%%=*}" | tr -d '[:space:]')"
    v="$(printf '%s' "${line#*=}" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')"
    if [ "$k" == "$key" ] && [ -n "$v" ]; then
      printf '%s\n' "$v"
      found=0
    fi
  done < "$file"
  return $found
}

# ext_protected_hits <changed-files> <protected-files> <hot-files>
# Each argument is a newline-separated list of repo-relative paths.
# Prints every changed path that is protected or hot, tagged, one per line.
# Returns 0 if there are none (the change is allowed), 1 if there are any.
ext_protected_hits () {
  local changed="$1" protected="$2" hot="$3" path hits=1
  while IFS= read -r path; do
    [ -n "$path" ] || continue
    if printf '%s\n' "$protected" | grep -Fxq -- "$path"; then
      echo "protected: $path"; hits=0
    elif printf '%s\n' "$hot" | grep -Fxq -- "$path"; then
      echo "hot (changed recently on base): $path"; hits=0
    fi
  done <<< "$changed"
  [ "$hits" -eq 0 ] && return 1
  return 0
}

# ext_sync <workdir> <remote-url> <base>
# Leaves <workdir> as a pristine checkout of origin/<base>: cloned if absent,
# otherwise fetched, hard-reset and cleaned (ignored files included, so a stale
# build output can never satisfy the verify gate). Idempotent.
# Returns non-zero if the clone/fetch fails or <base> does not exist on the
# remote; the checkout is then in an unspecified state and must not be used.
# Local branches (including unshipped snapshots) are kept.
ext_sync () {
  local work="$1" url="$2" base="$3"
  if [ ! -d "$work/.git" ]; then
    mkdir -p "$(dirname "$work")" || return 1
    git clone --quiet "$url" "$work" || return 1
  fi
  git -C "$work" fetch --quiet --prune origin || return 1
  git -C "$work" rev-parse --verify --quiet "origin/$base" >/dev/null || return 1
  git -C "$work" checkout --quiet -B "$base" "origin/$base" || return 1
  git -C "$work" reset --quiet --hard "origin/$base" || return 1
  git -C "$work" clean -fdxq || return 1
}

# ext_hot_files <workdir> <base> <hours>
# Paths MODIFIED or renamed on origin/<base> in the last <hours> hours, one per
# line, deduped. Another agent (the target's own IDE agent) may be mid-flight on
# these. Additions alone do not count: a repo created today adds every file in
# its first commit, and treating that as "in flight" would put the whole tree
# off limits. Iterating on a file is what shows up as M.
ext_hot_files () {
  local work="$1" base="$2" hours="$3"
  git -C "$work" log "origin/$base" --since="$hours hours ago" --diff-filter=MR --name-only --format= 2>/dev/null \
    | sed '/^$/d' | sort -u
}

# ext_changed_files <workdir> <base>
# Every path this cycle changed relative to origin/<base>: tracked edits,
# deletions and new untracked files (ignored files excluded). One per line.
ext_changed_files () {
  local work="$1" base="$2"
  {
    git -C "$work" diff --name-only "origin/$base"
    git -C "$work" ls-files --others --exclude-standard
  } 2>/dev/null | sed '/^$/d' | sort -u
}

# ext_snapshot <workdir> <branch>
# If <workdir> has changes, commits them to a NEW LOCAL branch <branch> so a
# failed cycle's work survives the next ext_sync. Never pushes: an unreviewed
# branch does not belong in someone else's repo.
# Returns 0 if the tree was clean or the snapshot committed; 1 otherwise.
ext_snapshot () {
  local work="$1" branch="$2"
  [ -z "$(git -C "$work" status --porcelain)" ] && return 0
  git -C "$work" switch --quiet -c "$branch" || return 1
  git -C "$work" add -A || return 1
  git -C "$work" commit --quiet -m "wip: unshipped agentlab cycle (local only)" || return 1
}
