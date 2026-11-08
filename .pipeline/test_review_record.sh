#!/bin/bash
# Offline tests for .pipeline/review_record.sh and run.sh's
# attach_review_to_pr (spec #5). gh is a stub; no network, no model.
# Run: bash .pipeline/test_review_record.sh
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
REPO="$(pwd)"
RUN_SH="$REPO/.pipeline/run.sh"
# shellcheck source=review_record.sh
. .pipeline/review_record.sh

PASS=0; FAIL=0
ok ()  { PASS=$((PASS+1)); echo "ok   $1"; }
bad () { FAIL=$((FAIL+1)); echo "FAIL $1"; }
check () { local name="$1"; shift; if "$@"; then ok "$name"; else bad "$name"; fi; }

T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT

# The reviewer's output contract (agentlab-reviewer.md), multi-line Ran.
cat > "$T/verdict.md" <<'EOF'
VERDICT: PASS
Increment: examples/tool-x/
Ran: python3 -m pytest examples/tool-x -q -> 12 passed
ruff check examples/tool-x -> clean
Findings:
- warning: retry loop has no upper bound on total wait (client.py:41)
- nit: README example uses an old flag name
- nit: unicode is fine — café ✓
EOF

# --- review_record_section ---

review_record_section "$T/verdict.md" > "$T/section.md"; rc=$?
check "S1 a well-formed verdict gives rc 0" [ "$rc" -eq 0 ]
check "S2 the section starts with the hidden marker" [ "$(head -1 "$T/section.md")" == "$REVIEW_RECORD_MARKER" ]
check "S3 ...and has the heading" grep -qx '## Independent review' "$T/section.md"
check "S4 every Findings bullet is in the section" \
  [ "$(grep -c '^- ' "$T/verdict.md")" == "$(grep -cxFf <(grep '^- ' "$T/verdict.md") "$T/section.md")" ]
check "S5 both Ran lines are in the section" \
  [ "$(grep -c 'pytest examples/tool-x -q -> 12 passed\|ruff check examples/tool-x -> clean' "$T/section.md")" == 2 ]
check "S6 the verdict word is in the section" grep -qF '**Verdict:** PASS' "$T/section.md"
printf 'VERDICT: PASS\nIncrement: x\nRan: y\n' > "$T/nofindings.md"
out="$(review_record_section "$T/nofindings.md")"; rc=$?
check "S7 no Findings block: rc 1, prints nothing" [ "$rc|$out" == "1|" ]
out="$(review_record_section "$T/missing.md")"; rc=$?
check "S8 a missing file: rc 1, prints nothing" [ "$rc|$out" == "1|" ]

# --- review_record_has_marker ---

printf 'Body text\n' > "$T/body-plain"
cat "$T/body-plain" "$T/section.md" > "$T/body-marked"
review_record_has_marker "$T/body-marked"; check "K1 a body with the section has the marker" [ $? -eq 0 ]
review_record_has_marker "$T/body-plain"; check "K2 a body without it does not" [ $? -eq 1 ]
review_record_has_marker "$T/nope"; check "K3 a missing body does not" [ $? -eq 1 ]

# --- review_record_secret_hit (E5 at the unit level) ---

secret_case () {
  local id="$1" want="$2" text="$3" got label=passes
  printf '%s\n' "$text" > "$T/s-$id"
  review_record_secret_hit "$T/s-$id"; got=$?
  [ "$want" -eq 0 ] && label=flags
  check "X$id $label: $text" [ "$got" -eq "$want" ]
}
secret_case 1 0 "- blocker: hardcoded key sk-ant-api03-abcdefghijklmnop in config.py"
secret_case 2 0 "token ghp_abcdefghijklmnopqrstuvwxyz0123 leaked"
secret_case 3 0 "AWS AKIAIOSFODNN7EXAMPLE in .env"
secret_case 4 0 "-----BEGIN RSA PRIVATE KEY-----"
secret_case 5 0 "ANTHROPIC_API_KEY=abcd1234efgh5678"
secret_case 6 0 "github_pat_11ABCDEFG0123456789_abcdefghij"
secret_case 7 1 "- nit: commit 4f1c2e8b9a7d6c5b4a3f2e1d0c9b8a7f6e5d4c3b is fine"
secret_case 8 1 "- warning: task-runner retries forever"
secret_case 9 1 "Ran: export ANTHROPIC_API_KEY=\$KEY from the env (no value inline)"
review_record_secret_hit "$T/nope"; check "X10 an unreadable file is not 'clean' (rc 2)" [ $? -eq 2 ]

# --- review_record_compose (E3 at the unit level) ---

review_record_compose "$T/body-plain" "$T/section.md" > "$T/composed"; rc=$?
check "C1 a small body and section compose unchanged" \
  [ "$rc|$(cat "$T/composed")" == "0|$(cat "$T/body-plain"; printf '\n\n'; cat "$T/section.md")" ]
# A section far over the limit.
{ cat "$T/section.md"; for i in $(seq 1 3000); do echo "- nit: finding number $i, padded é with some text to make it long"; done; } > "$T/huge-section"
review_record_compose "$T/body-plain" "$T/huge-section" > "$T/composed-huge"; rc=$?
n="$(wc -c < "$T/composed-huge" | tr -d ' ')"
check "C2 an oversized section is cut to under $REVIEW_RECORD_BODY_LIMIT bytes (got $n)" [ "$rc" -eq 0 -a "$n" -lt "$REVIEW_RECORD_BODY_LIMIT" ]
check "C3 ...ends with a note saying it was truncated" grep -q 'Truncated to fit' "$T/composed-huge"
check "C4 ...keeps the maintainer's body intact at the top" [ "$(head -1 "$T/composed-huge")" == "Body text" ]
check "C5 ...keeps the marker" review_record_has_marker "$T/composed-huge"
check "C6 ...and is still valid UTF-8" iconv -f UTF-8 -t UTF-8 "$T/composed-huge" >/dev/null 2>&1
head -c 65500 /dev/zero | tr '\0' 'a' > "$T/huge-body"
out="$(review_record_compose "$T/huge-body" "$T/section.md")"; rc=$?
check "C7 a body that leaves no room: rc 1, prints nothing" [ "$rc" -eq 1 -a -z "$out" ]

# --- attach_review_to_pr, with gh stubbed ---

eval "$(sed -n '/^attach_review_to_pr () {/,/^}/p' "$RUN_SH")"
if ! declare -f attach_review_to_pr >/dev/null 2>&1; then
  bad "A0 could not extract attach_review_to_pr from run.sh — the A cases are vacuous"
else
  # The stub keeps the PR body in $GH_BODY and records each call in $GH_CALLS.
  gh () {
    echo "$*" >> "$GH_CALLS"
    case "$1 $2" in
      "pr view") [ "${GH_VIEW_FAIL:-0}" -eq 1 ] && return 1; cat "$GH_BODY" ;;
      "pr edit") [ "${GH_EDIT_FAIL:-0}" -eq 1 ] && return 1
                 local f; f="$(printf '%s\n' "$@" | awk 'p { print; exit } $0 == "--body-file" { p = 1 }')"
                 cp "$f" "$GH_BODY" ;;
      *) return 1 ;;
    esac
  }
  LOG="$T/attach.log"; : > "$LOG"
  REVIEW_VERDICT_FILE="$T/verdict.md"
  GH_BODY="$T/pr-body"; GH_CALLS="$T/gh-calls"
  edits () { grep -c '^pr edit' "$GH_CALLS" 2>/dev/null || true; }

  printf 'Adds tool-x.\n\nVerified: pytest passes.\n' > "$GH_BODY"; : > "$GH_CALLS"
  attach_review_to_pr 42; rc=$?
  check "A1 (E1) the section is added and attach returns 0" [ "$rc" -eq 0 ]
  check "A2 (E1) the PR body now holds every Findings bullet" \
    [ "$(grep -c '^- ' "$T/verdict.md")" == "$(grep -cxFf <(grep '^- ' "$T/verdict.md") "$GH_BODY")" ]
  check "A3 the maintainer's text is still first" [ "$(head -1 "$GH_BODY")" == "Adds tool-x." ]

  attach_review_to_pr 42; rc=$?
  check "A4 (E2) a second run returns 0" [ "$rc" -eq 0 ]
  check "A5 (E2) ...and the body still has exactly one section" [ "$(grep -cF "$REVIEW_RECORD_MARKER" "$GH_BODY")" == 1 ]
  check "A6 (E2) ...because it never edited again" [ "$(edits)" == 1 ]

  printf 'Body.\n' > "$GH_BODY"; : > "$GH_CALLS"
  GH_EDIT_FAIL=1 attach_review_to_pr 43; rc=$?
  check "A7 (E4) a failing gh pr edit returns 1 (do not merge)" [ "$rc" -eq 1 ]
  check "A8 (E4) ...and says so in the log" grep -q "'gh pr edit' failed for PR #43 — not merging" "$LOG"

  : > "$GH_CALLS"
  GH_VIEW_FAIL=1 attach_review_to_pr 44; rc=$?
  check "A9 an unreadable PR body returns 1 and edits nothing" [ "$rc|$(edits)" == "1|0" ]

  { cat "$T/verdict.md"; echo "- blocker: key sk-ant-api03-SECRETSECRETSECRET in app.py"; } > "$T/verdict-secret.md"
  printf 'Body.\n' > "$GH_BODY"; : > "$GH_CALLS"
  REVIEW_VERDICT_FILE="$T/verdict-secret.md" attach_review_to_pr 45; rc=$?
  check "A10 (E5) a review quoting an sk-ant- key returns 1" [ "$rc" -eq 1 ]
  check "A11 (E5) ...and nothing was posted" [ "$(edits)|$(grep -c 'sk-ant' "$GH_BODY")" == "0|0" ]
  check "A12 (E5) ...and the log says why" grep -q "matches a credential pattern — NOT posted, PR #45 left open" "$LOG"

  printf 'Body.\n' > "$GH_BODY"; : > "$GH_CALLS"
  REVIEW_VERDICT_FILE="$T/nofindings.md" attach_review_to_pr 46; rc=$?
  check "A13 a verdict with no Findings block returns 1 and edits nothing" [ "$rc|$(edits)" == "1|0" ]

  : > "$GH_CALLS"
  attach_review_to_pr "42; rm -rf /"; rc=$?
  check "A14 a PR number that is not a number returns 1 and calls no gh" [ "$rc|$(wc -l < "$GH_CALLS" | tr -d ' ')" == "1|0" ]

  # E3 end to end: a long body plus a huge verdict still posts under the limit.
  { head -c 60000 /dev/zero | tr '\0' 'b'; echo; } > "$GH_BODY"
  { cat "$T/verdict.md"; for i in $(seq 1 2000); do echo "- nit: long finding $i with padding text"; done; } > "$T/verdict-huge.md"
  : > "$GH_CALLS"
  REVIEW_VERDICT_FILE="$T/verdict-huge.md" attach_review_to_pr 47; rc=$?
  n="$(wc -c < "$GH_BODY" | tr -d ' ')"
  check "A15 (E3) an oversized review posts truncated, under the limit (got $n)" \
    [ "$rc" -eq 0 -a "$n" -lt "$REVIEW_RECORD_BODY_LIMIT" ]
fi

# --- run_cycle wiring (E4) ---

attach_ln="$(grep -n 'if ! attach_review_to_pr "\$pr_num"; then' "$RUN_SH" | head -1 | cut -d: -f1)"
merge_ln="$(grep -n 'gh pr merge "\$pr_num"' "$RUN_SH" | head -1 | cut -d: -f1)"
check "W1 (E4) run_cycle attaches the review before it merges (lines ${attach_ln:-none} < ${merge_ln:-none})" \
  [ -n "$attach_ln" -a -n "$merge_ln" ] && [ "${attach_ln:-0}" -lt "${merge_ln:-0}" ]
check "W2 (E4) ...and a failed attach returns before the merge" \
  [ "$(awk -v s="${attach_ln:-0}" 'NR > s && NR <= s + 3 && /return 1/ { print "yes"; exit }' "$RUN_SH")" == "yes" ]

echo ""
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
