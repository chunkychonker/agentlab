#!/bin/bash
# Offline tests for .pipeline/external.sh. No network: git cases run against a
# throwaway local bare repo. Run: bash .pipeline/test_external.sh
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
# shellcheck source=external.sh
. .pipeline/external.sh

PASS=0; FAIL=0
ok ()  { PASS=$((PASS+1)); echo "ok   $1"; }
bad () { FAIL=$((FAIL+1)); echo "FAIL $1"; }
check () { local name="$1"; shift; if "$@"; then ok "$name"; else bad "$name"; fi; }

T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT

# --- ext_target_field ------------------------------------------------------
cat > "$T/target" <<'EOF'
# a comment
repo = owner/name
base=main   # trailing comment
protect=a/SUMMARY.md
protect=b/c.json

empty=
EOF
check "E1 single value is trimmed" [ "$(ext_target_field "$T/target" repo)" == "owner/name" ]
check "E2 trailing comment stripped" [ "$(ext_target_field "$T/target" base)" == "main" ]
check "E3 repeated key yields every value in order" \
  [ "$(ext_target_field "$T/target" protect | tr '\n' ,)" == "a/SUMMARY.md,b/c.json," ]
ext_target_field "$T/target" missing >/dev/null; check "E4 missing key returns 1" [ $? -eq 1 ]
ext_target_field "$T/target" empty >/dev/null;   check "E5 empty value counts as missing" [ $? -eq 1 ]
ext_target_field "$T/nope" repo >/dev/null;      check "E6 unreadable file returns 2" [ $? -eq 2 ]

# --- ext_protected_hits ----------------------------------------------------
changed=$'src/a.ts\nsrc/WORKSPACE_SUMMARY.md\ndata/hot.json'
out="$(ext_protected_hits "$changed" "src/WORKSPACE_SUMMARY.md" "data/hot.json")"; rc=$?
check "E7 protected and hot paths both reported" [ "$rc" -eq 1 -a "$(echo "$out" | wc -l | tr -d ' ')" -eq 2 ]
ext_protected_hits $'src/a.ts\nsrc/b.ts' "src/WORKSPACE_SUMMARY.md" "data/hot.json" >/dev/null
check "E8 untouched protected/hot paths allow the change" [ $? -eq 0 ]
ext_protected_hits "src/WORKSPACE_SUMMARY.md.bak" "src/WORKSPACE_SUMMARY.md" "" >/dev/null
check "E9 match is exact, not a prefix" [ $? -eq 0 ]
ext_protected_hits "" "x" "y" >/dev/null
check "E10 empty change list is allowed" [ $? -eq 0 ]

# --- git edge: sync / changed / hot / snapshot -----------------------------
git init --quiet --bare -b main "$T/remote.git"
git clone --quiet "$T/remote.git" "$T/seed" 2>/dev/null
( cd "$T/seed" && git config user.email t@t && git config user.name t \
  && echo one > a.txt && echo node_modules/ > .gitignore && git add -A && git commit --quiet -m one \
  && git push --quiet origin main ) || { echo "fixture setup failed"; exit 1; }

W="$T/work/target"
check "E11 sync clones a missing checkout" ext_sync "$W" "$T/remote.git" main
check "E12 sync of an unknown base fails" bash -c ". .pipeline/external.sh; ! ext_sync '$W' '$T/remote.git' nope"

echo edit >> "$W/a.txt"; echo new > "$W/b.txt"; mkdir -p "$W/node_modules"; echo x > "$W/node_modules/i"
check "E13 changed files = tracked edit + untracked, ignored excluded" \
  [ "$(ext_changed_files "$W" main | tr '\n' ,)" == "a.txt,b.txt," ]

check "E14 snapshot commits a dirty tree to a local branch" ext_snapshot "$W" "cycle/t-unshipped"
check "E15 snapshot branch exists locally" git -C "$W" rev-parse --verify --quiet -q cycle/t-unshipped
check "E16 snapshot is not pushed" bash -c "! git -C '$T/remote.git' rev-parse --verify --quiet cycle/t-unshipped"

check "E17 re-sync restores pristine base" ext_sync "$W" "$T/remote.git" main
check "E18 re-sync removes ignored build output" [ ! -e "$W/node_modules/i" ]
check "E19 re-sync leaves no changes" [ -z "$(ext_changed_files "$W" main)" ]
check "E20 re-sync keeps the unshipped snapshot" git -C "$W" rev-parse --verify --quiet -q cycle/t-unshipped
check "E21 snapshot of a clean tree is a no-op" ext_snapshot "$W" "cycle/never"
check "E22 ...and creates no branch" bash -c "! git -C '$W' rev-parse --verify --quiet cycle/never"

( cd "$T/seed" && echo two > added.txt && echo two >> a.txt && git add -A && git commit --quiet -m two && git push --quiet origin main )
ext_sync "$W" "$T/remote.git" main
check "E23 a recently modified file is hot" bash -c ". .pipeline/external.sh; ext_hot_files '$W' main 2 | grep -qx a.txt"
check "E24 a recently added (unmodified) file is not hot" bash -c ". .pipeline/external.sh; ! ext_hot_files '$W' main 2 | grep -qx added.txt"
( cd "$T/seed" && echo mine >> added.txt && git add -A && git -c user.email=me@lab -c user.name=lab commit --quiet -m mine && git push --quiet origin main )
ext_sync "$W" "$T/remote.git" main
check "E30 a file modified only by self_email is not hot" bash -c ". .pipeline/external.sh; ! ext_hot_files '$W' main 2 me@lab | grep -qx added.txt"
check "E31 ...but is hot when no self_email is given" bash -c ". .pipeline/external.sh; ext_hot_files '$W' main 2 | grep -qx added.txt"
check "E32 another author's edit stays hot despite self_email" bash -c ". .pipeline/external.sh; ext_hot_files '$W' main 2 me@lab | grep -qx a.txt"

# --- AI attribution --------------------------------------------------------
msg=$'fix: x\n\nbody line\n\nCo-Authored-By: Claude Opus <noreply@anthropic.com>\nCo-Authored-By: Kush <k@x.com>\n\U0001F916 Generated with [Claude Code](https://claude.com/claude-code)'
out="$(printf '%s\n' "$msg" | ext_strip_ai_attribution)"
check "E27 strip drops the Claude trailer and footer" bash -c "! printf '%s' \"\$1\" | grep -qiE 'anthropic|claude code'" _ "$out"
check "E28 strip keeps human co-authors and body" bash -c "printf '%s' \"\$1\" | grep -q 'Kush' && printf '%s' \"\$1\" | grep -q 'body line'" _ "$out"
ext_sync "$W" "$T/remote.git" main
( cd "$W" && git config user.email t@t && git config user.name t && echo h > h.txt && git add h.txt \
  && git commit --quiet -m "$msg" )
check "E29 synced checkout's hook strips the trailer from a real commit" \
  bash -c "! git -C '$W' log -1 --format=%B | grep -qi anthropic && git -C '$W' log -1 --format=%B | grep -q Kush"

# --- ext_commit_bookkeeping ------------------------------------------------
git init --quiet --bare -b main "$T/lab.git"
git clone --quiet "$T/lab.git" "$T/lab" 2>/dev/null
( cd "$T/lab" && git config user.email t@t && git config user.name t \
  && mkdir -p research knowledge projects/p .pipeline && cp "$OLDPWD"/.pipeline/external{,_night}.sh .pipeline/ \
  && echo plan > projects/p/PLAN.md && echo v1 > projects/p/verify.sh && echo repo=a > projects/p/target \
  && touch research/.k knowledge/.k && git add -A && git commit --quiet -m init && git push --quiet origin main )
( cd "$T/lab" && echo v2 > projects/p/verify.sh && echo repo=evil > projects/p/target && echo n > research/note.md \
  && REPO="$T/lab" LOG="$T/lab.log" bash -c '. .pipeline/external_night.sh; ext_commit_bookkeeping p "chore: t"' ) >/dev/null 2>&1
check "E25 bookkeeping pushes the reviewed verify script" \
  bash -c "git -C '$T/lab.git' show main:projects/p/verify.sh | grep -qx v2"
check "E26 bookkeeping never commits target" \
  bash -c "git -C '$T/lab.git' show main:projects/p/target | grep -qx repo=a && git -C '$T/lab' status --short | grep -q target"

echo ""
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
