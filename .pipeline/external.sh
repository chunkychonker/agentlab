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

# Lines that credit an AI tool rather than the account owner: a Co-Authored-By
# trailer naming Claude/Anthropic, or Claude Code's "Generated with" footer.
# Work in someone else's repo ships under the owner's GitHub identity only.
EXT_AI_ATTRIBUTION_RE='^(co-authored-by:.*(claude|anthropic)|.*generated with \[?claude code)'

# ext_strip_ai_attribution
# Filter: copies stdin to stdout minus lines matching EXT_AI_ATTRIBUTION_RE
# (case-insensitive). Never fails; empty input gives empty output.
ext_strip_ai_attribution () {
  grep -viE "$EXT_AI_ATTRIBUTION_RE" || true
}

# ext_install_commit_hook <workdir>
# Writes <workdir>/.git/hooks/commit-msg so every commit made in the checkout,
# by any agent, has AI attribution lines stripped before it is recorded.
# Idempotent (overwrites). Returns non-zero if the hook cannot be written.
ext_install_commit_hook () {
  local hook="$1/.git/hooks/commit-msg"
  mkdir -p "$(dirname "$hook")" || return 1
  cat > "$hook" <<HOOK || return 1
#!/bin/sh
# Installed by agentlab (.pipeline/external.sh): no AI attribution in commits.
grep -viE '$EXT_AI_ATTRIBUTION_RE' "\$1" > "\$1.agentlab" || true
mv "\$1.agentlab" "\$1"
HOOK
  chmod +x "$hook"
}

# ext_sync <workdir> <remote-url> <base>
# Leaves <workdir> as a pristine checkout of origin/<base>: cloned if absent,
# otherwise fetched, hard-reset and cleaned (ignored files included, so a stale
# build output can never satisfy the verify gate). Idempotent.
# Returns non-zero if the clone/fetch fails or <base> does not exist on the
# remote; the checkout is then in an unspecified state and must not be used.
# Local branches (including unshipped snapshots) are kept. Also (re)installs
# the commit-msg hook from ext_install_commit_hook.
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
  ext_install_commit_hook "$work"
}

# ext_hot_files <workdir> <base> <hours> [self_email]
# Paths MODIFIED or renamed on origin/<base> in the last <hours> hours, one per
# line, deduped. Another agent (the target's own IDE agent) may be mid-flight on
# these. Additions alone do not count: a repo created today adds every file in
# its first commit, and treating that as "in flight" would put the whole tree
# off limits. Iterating on a file is what shows up as M.
# Commits authored by [self_email] (agentlab's own identity) are ignored: our
# merged or stacked PRs are not another agent's in-flight work, and counting
# them would lock agentlab out of the files it just improved.
ext_hot_files () {
  local work="$1" base="$2" hours="$3" self="${4:-}"
  git -C "$work" log "origin/$base" --since="$hours hours ago" --diff-filter=MR --name-only --format='@%ae' 2>/dev/null \
    | awk -v self="$self" '/^@/ { skip = (self != "" && substr($0, 2) == self); next } NF && !skip' \
    | sort -u
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

# --- baseline gate ---------------------------------------------------------
#
# Why: on 2026-10-21 milestone 7 was researched and built on opus against a
# main that could not pass verify — the corridor fix it needed was still in
# open PR #6. Verify on the pristine checkout is a model-free way to know that
# before any tokens are spent.

# Branch prefix the maintainer gives every PR it opens on a target (see
# projects/EXTERNAL_MODE.md, maintainer step 2). The two must agree.
EXT_BRANCH_PREFIX="agentlab/"

# Words ext_baseline_decision prints. Defined once; callers compare to these.
EXT_BASELINE_PROCEED="PROCEED"                  # base passes verify
EXT_BASELINE_MUST_FIX="PROCEED_MUST_FIX"        # base fails, none of our PRs open: this cycle must fix it
EXT_BASELINE_STOP_OPEN_PR="STOP_OPEN_PR"        # base fails while one of our PRs is open: wait for the merge
EXT_BASELINE_STOP_UNKNOWN="STOP_PR_UNKNOWN"     # base fails and the PR list could not be read

# ext_baseline_decision <verify_rc> <open_prs_rc> <open_prs>
# Pure. <open_prs> is a newline-separated list of PR numbers; <open_prs_rc> is
# the exit status of the call that produced it (ignored when verify passed).
# Prints exactly one EXT_BASELINE_* word. Always returns 0.
ext_baseline_decision () {
  local verify_rc="$1" prs_rc="$2" prs="$3"
  if [ "$verify_rc" -eq 0 ]; then echo "$EXT_BASELINE_PROCEED"
  elif [ "$prs_rc" -ne 0 ]; then echo "$EXT_BASELINE_STOP_UNKNOWN"
  elif [ -n "$(printf '%s' "$prs" | tr -d '[:space:]')" ]; then echo "$EXT_BASELINE_STOP_OPEN_PR"
  else echo "$EXT_BASELINE_MUST_FIX"
  fi
  return 0
}

# ext_prs_with_prefix <prefix>
# Filter. Reads "<number> <head-branch>" lines on stdin and prints the number
# of each one whose branch starts with <prefix>. Never fails.
ext_prs_with_prefix () {
  awk -v p="$1" 'NF >= 2 && index($2, p) == 1 { print $1 }'
}

# ext_open_agentlab_prs <repo>
# Prints the open PRs on <repo> whose head branch starts with EXT_BRANCH_PREFIX,
# one number per line. Returns 1, printing nothing, if gh fails (network, auth).
ext_open_agentlab_prs () {
  local repo="$1" rows
  rows="$(gh pr list -R "$repo" --state open --limit 100 \
    --json number,headRefName --jq '.[] | "\(.number) \(.headRefName)"')" || return 1
  printf '%s\n' "$rows" | ext_prs_with_prefix "$EXT_BRANCH_PREFIX"
}

# ext_reset_pristine <workdir> <base>
# Puts <workdir> back to origin/<base> exactly — no fetch, so the commit the
# baseline verified is the commit the cycle builds on. Removes build output
# (ignored files included). Returns non-zero if either git step fails.
ext_reset_pristine () {
  local work="$1" base="$2"
  git -C "$work" reset --quiet --hard "origin/$base" || return 1
  git -C "$work" clean -fdxq
}

# --- resume after a usage limit --------------------------------------------
#
# A marker that exists only while the most recent cycle stopped on a usage
# limit AFTER its research note passed the gate. Two lines:
#   note=<path to the research note>
#   base=<full SHA of origin/<base> the note was written against>

# ext_resume_write <marker> <note_path> <base_sha>
# Overwrites <marker>. Returns non-zero if it cannot be written.
ext_resume_write () {
  printf 'note=%s\nbase=%s\n' "$2" "$3" > "$1"
}

# ext_resume_read <marker> <current_base_sha>
# Consumes the marker: it is deleted whatever the outcome, so a resume is
# offered at most once. Prints the note path and returns 0 only if the marker
# was readable, names an existing note, and its base SHA equals
# <current_base_sha>. Returns 1 if it was missing, malformed, named a missing
# note, or the base has moved since (the research may no longer hold).
ext_resume_read () {
  local marker="$1" current="$2" note base
  [ -r "$marker" ] || return 1
  note="$(sed -n 's/^note=//p' "$marker" | head -1)"
  base="$(sed -n 's/^base=//p' "$marker" | head -1)"
  rm -f "$marker"
  [ -n "$note" ] && [ -n "$base" ] && [ -f "$note" ] && [ "$base" == "$current" ] || return 1
  echo "$note"
}
