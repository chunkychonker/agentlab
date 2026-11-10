#!/bin/bash
# Offline tests for .pipeline/issues.sh and its run.sh wiring (spec #6, step
# 1). gh is .pipeline/fake_gh.py on PATH: no network, no real issues.
# Run: bash .pipeline/test_issues.sh
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
REPO="$(pwd)"
RUN_SH="$REPO/.pipeline/run.sh"
# shellcheck source=review_record.sh
. .pipeline/review_record.sh
# shellcheck source=issues.sh
. .pipeline/issues.sh
# shellcheck source=findings.sh
. .pipeline/findings.sh

PASS=0; FAIL=0
ok ()  { PASS=$((PASS+1)); echo "ok   $1"; }
bad () { FAIL=$((FAIL+1)); echo "FAIL $1"; }
check () { local name="$1"; shift; if "$@"; then ok "$name"; else bad "$name"; fi; }

T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
mkdir -p "$T/bin"
printf '#!/bin/bash\nexec /usr/bin/python3 "%s/.pipeline/fake_gh.py" "$@"\n' "$REPO" > "$T/bin/gh"
chmod +x "$T/bin/gh"
PATH="$T/bin:$PATH"
export FAKE_GH_DIR="$T/gh"

# A fresh, empty GitHub for each case.
reset_gh () { rm -rf "$FAKE_GH_DIR"; mkdir -p "$FAKE_GH_DIR"; : > "$FAKE_GH_DIR/calls"; }
# Seeds an issue: seed <number> <state> <label> <title>
seed () {
  /usr/bin/python3 - "$FAKE_GH_DIR/issues.json" "$@" <<'PY'
import json, os, sys
path, num, state, label, title = sys.argv[1:]
issues = json.load(open(path)) if os.path.exists(path) else []
issues.append({"number": int(num), "title": title, "state": state, "labels": [label], "body": "", "comments": []})
json.dump(issues, open(path, "w"))
PY
}
# q <python expression over `issues`> — prints its value.
q () {
  /usr/bin/python3 -c 'import json,os,sys
p=os.path.join(os.environ["FAKE_GH_DIR"],"issues.json")
issues=json.load(open(p)) if os.path.exists(p) else []
print(eval(sys.argv[1]))' "$1"
}
calls () { grep -c "\"$1\"" "$FAKE_GH_DIR/calls" 2>/dev/null || true; }
ncalls () { wc -l < "$FAKE_GH_DIR/calls" | tr -d ' '; }

# --- issues_enabled ---

printf 'on\n' > "$T/sw";   issues_enabled "$T/sw"; check "N1 'on' enables" [ $? -eq 0 ]
printf ' on \n\n' > "$T/sw"; issues_enabled "$T/sw"; check "N2 'on' with whitespace enables" [ $? -eq 0 ]
printf 'off\n' > "$T/sw";  issues_enabled "$T/sw"; check "N3 'off' disables (rc 1)" [ $? -eq 1 ]
issues_enabled "$T/missing"; check "N4 a missing switch disables (rc 1)" [ $? -eq 1 ]
printf 'yes\n' > "$T/sw";  issues_enabled "$T/sw"; check "N5 anything else is rc 2" [ $? -eq 2 ]
check "N6 the switch ships off" [ "$(tr -d '[:space:]' < .pipeline/file-issues)" == "off" ]

# --- issue_title_for ---

check "T1 squeezes whitespace and trims" [ "$(issue_title_for "$(printf '  run-1 \t—  shipped\n0/2  ')")" == "run-1 — shipped 0/2" ]
check "T2 a plain subject is unchanged" [ "$(issue_title_for 'examples/tool-x/')" == "examples/tool-x/" ]
out="$(issue_title_for "   ")"; rc=$?
check "T3 a blank subject: rc 1, prints nothing" [ "$rc|$out" == "1|" ]
long="$(head -c 400 /dev/zero | tr '\0' 'x')"
t="$(issue_title_for "$long")"
check "T4 a long subject is cut to $ISSUE_TITLE_MAX bytes, ending '...'" \
  [ "$(printf '%s' "$t" | LC_ALL=C wc -c | tr -d ' ')" == "$ISSUE_TITLE_MAX" -a "${t%...}" != "$t" ]
ulong="$(for i in $(seq 1 200); do printf 'é'; done)"
t="$(issue_title_for "$ulong")"
check "T5 a long UTF-8 subject stays valid UTF-8 and within the limit" \
  [ "$(printf '%s' "$t" | LC_ALL=C wc -c | tr -d ' ')" -le "$ISSUE_TITLE_MAX" ]
check "T5b ...(valid UTF-8)" sh -c "printf '%s' \"\$1\" | iconv -f UTF-8 -t UTF-8 >/dev/null 2>&1" _ "$t"
check "T6 the same subject always gives the same title" [ "$(issue_title_for "$long")" == "$(issue_title_for "$long")" ]

# --- issue_match ---

j='[{"number":3,"title":"a","state":"CLOSED"},{"number":5,"title":"a","state":"OPEN"},{"number":9,"title":"a","state":"CLOSED"},{"number":7,"title":"ab","state":"OPEN"}]'
check "M1 an open match wins over a newer closed one" [ "$(printf '%s' "$j" | issue_match a)" == "OPEN 5" ]
check "M2 only closed matches: the newest closed" \
  [ "$(printf '%s' '[{"number":3,"title":"a","state":"CLOSED"},{"number":9,"title":"a","state":"CLOSED"}]' | issue_match a)" == "CLOSED 9" ]
check "M3 no exact match: NONE (a longer title does not count)" [ "$(printf '%s' "$j" | issue_match b)" == "NONE" ]
check "M4 a prefix of a title does not match it" [ "$(printf '%s' '[{"number":7,"title":"ab","state":"OPEN"}]' | issue_match a)" == "NONE" ]
check "M5 an empty list: NONE" [ "$(printf '[]' | issue_match a)" == "NONE" ]
out="$(printf 'not json' | issue_match a)"; rc=$?
check "M6 bad JSON: rc != 0, prints nothing" [ "$rc" -ne 0 -a -z "$out" ]
out="$(printf '{"a":1}' | issue_match a)"; rc=$?
check "M7 a JSON object instead of a list: rc != 0" [ "$rc" -ne 0 -a -z "$out" ]

# --- issue_file_finding, against the fake gh ---

reset_gh
issue_file_finding health 2026-11-08 "examples/tool-x/" "README transcript drift on line 4" 2026-11-08_020001 2>/dev/null; rc=$?
check "I1 a new finding opens an issue (rc 0)" [ "$rc" -eq 0 ]
check "I2 ...one issue, titled with the subject, labeled health" \
  [ "$(q 'len(issues), issues[0]["title"], issues[0]["labels"]')" == "(1, 'examples/tool-x/', ['health'])" ]
body="$(q 'issues[0]["body"]')"
check "I3 ...the body has the detail, the date and the run" \
  [ -n "$(printf '%s' "$body" | grep -F 'README transcript drift on line 4')" -a \
    -n "$(printf '%s' "$body" | grep -F 'Observed: 2026-11-08 (run 2026-11-08_020001)')" ]

issue_file_finding health 2026-11-09 "examples/tool-x/" "still drifting" 2026-11-09_020001 2>/dev/null; rc=$?
check "I4 (F3) re-filing the subject adds a comment (rc 3)" [ "$rc" -eq 3 ]
check "I5 (F3) ...and no new issue" [ "$(q 'len(issues)')" == 1 ]
check "I6 (F3) ...the comment says re-observed, with the date" \
  [ "$(q 'issues[0]["comments"][0].splitlines()[0]')" == "Re-observed 2026-11-09 (run 2026-11-09_020001)." ]
issue_file_finding health 2026-11-10 "  examples/tool-x/  " "again" 2>/dev/null; rc=$?
check "I7 extra whitespace in the subject still dedupes" [ "$rc|$(q 'len(issues)')" == "3|1" ]

reset_gh; seed 4 CLOSED health "run-2026-09-03 — session limit"
issue_file_finding health 2026-11-08 "run-2026-09-03 — session limit" "back again" 2>/dev/null; rc=$?
check "I8 a closed match opens a new issue (rc 0)" [ "$rc|$(q 'len(issues)')" == "0|2" ]
check "I9 ...that links the closed one" [ -n "$(q 'issues[1]["body"]' | grep -F 'after #4 was closed')" ]

reset_gh; seed 2 OPEN pipeline "same subject"
issue_file_finding health 2026-11-08 "same subject" "x" 2>/dev/null; rc=$?
check "I10 an open issue under the other label does not count as a match" [ "$rc|$(q 'len(issues)')" == "0|2" ]

reset_gh
issue_file_finding bogus 2026-11-08 "s" "d" 2>/dev/null; rc=$?
check "I11 an unknown label: rc 1, gh never called" [ "$rc|$(ncalls)" == "1|0" ]
issue_file_finding health 2026-11-08 "   " "d" 2>/dev/null; rc=$?
check "I12 a blank subject: rc 1, gh never called" [ "$rc|$(ncalls)" == "1|0" ]

reset_gh
issue_file_finding health 2026-11-08 "config leak" "found sk-ant-api03-abcdefghijklmnop in .env" 2>"$T/err"; rc=$?
check "I13 a detail with an sk-ant- key: rc 1" [ "$rc" -eq 1 ]
check "I14 ...nothing was created" [ "$(calls create)|$(q 'len(issues)')" == "0|0" ]
check "I15 ...and stderr says why" grep -q 'credential pattern' "$T/err"
reset_gh; seed 1 OPEN health "config leak"
issue_file_finding health 2026-11-08 "config leak" "ghp_abcdefghijklmnopqrstuvwxyz0123 again" 2>/dev/null; rc=$?
check "I16 a secret on the comment path is not posted either" [ "$rc|$(calls comment)" == "1|0" ]
reset_gh
issue_file_finding health 2026-11-08 "token sk-ant-api03-abcdefghijklmnop in a subject" "plain detail" 2>"$T/err"; rc=$?
check "I16b a secret in the subject (the title): rc 1, gh never called" [ "$rc|$(ncalls)" == "1|0" ]
check "I16c ...and stderr says it was the subject" grep -q 'subject matches a credential pattern' "$T/err"

reset_gh
FAKE_GH_FAIL=list issue_file_finding health 2026-11-08 "s" "d" 2>/dev/null; rc=$?
check "I17 'gh issue list' failing: rc 1, nothing created" [ "$rc|$(calls create)" == "1|0" ]
FAKE_GH_FAIL=create issue_file_finding health 2026-11-08 "s" "d" 2>/dev/null; rc=$?
check "I18 'gh issue create' failing: rc 1" [ "$rc|$(q 'len(issues)')" == "1|0" ]
seed 1 OPEN health "s"
FAKE_GH_FAIL=comment issue_file_finding health 2026-11-08 "s" "d" 2>/dev/null; rc=$?
check "I19 'gh issue comment' failing: rc 1 (not 3)" [ "$rc|$(q 'len(issues[0]["comments"])')" == "1|0" ]

reset_gh
issue_file_finding pipeline 2026-11-08 '--title-like "quoted" `date` $(id)' "d" 2>/dev/null; rc=$?
check "I20 a subject with dashes, quotes, backticks and \$( ) is filed literally" \
  [ "$rc|$(q 'issues[0]["title"]')" == '0|--title-like "quoted" `date` $(id)' ]

# --- issue_record_unfiled ---

U="$T/unfiled.md"
issue_record_unfiled "$U" health 2026-11-08 "subj one" "detail one"; rc1=$?
issue_record_unfiled "$U" pipeline 2026-11-08 "subj two" ""; rc2=$?
check "U1 both appends succeed" [ "$rc1$rc2" == "00" ]
check "U2 one heading, then one line per finding" \
  [ "$(grep -c '^# ' "$U")|$(grep -c '^- ' "$U")" == "1|2" ]
check "U3 the line carries label, date, subject and detail" grep -qxF -- "- [health] 2026-11-08: subj one — detail one" "$U"
check "U4 no detail: no trailing dash" grep -qxF -- "- [pipeline] 2026-11-08: subj two" "$U"
issue_record_unfiled "$T/no/such/dir/u.md" health d s x 2>/dev/null
check "U5 an unwritable file: rc 1" [ $? -eq 1 ]

# --- run.sh wiring ---

eval "$(sed -n '/^file_finding_issue () {/,/^}/p' "$RUN_SH")"
eval "$(sed -n '/^findings_to_issues () {/,/^}/p' "$RUN_SH")"
if ! declare -f file_finding_issue >/dev/null || ! declare -f findings_to_issues >/dev/null; then
  bad "W0 could not extract the run.sh helpers — the W cases are vacuous"
else
  LOG="$T/run.log"; : > "$LOG"; TS="2026-11-08_020001"
  UNFILED_FINDINGS_FILE="$T/unfiled-run.md"
  reset_gh
  file_finding_issue health 2026-11-08 "examples/a/" "broken" >/dev/null; rc=$?
  logged="$(grep -c 'issue opened: examples/a/' "$LOG")"
  check "W1 a filed finding: rc 0, logged as opened" [ "$rc|$logged" == "0|1" ]
  FAKE_GH_FAIL=create file_finding_issue health 2026-11-08 "examples/b/" "broken" >/dev/null; rc=$?
  check "W2 gh failing: rc 1" [ "$rc" -eq 1 ]
  check "W3 ...the finding lands in the unfiled file" grep -qxF -- "- [health] 2026-11-08: examples/b/ — broken" "$UNFILED_FINDINGS_FILE"
  check "W4 ...and gh's reason is in the run log" grep -q "'gh issue create' failed" "$LOG"

  ISSUES_SWITCH_FILE="$T/sw2"
  printf 'on\n' > "$ISSUES_SWITCH_FILE"; findings_to_issues; check "W5 switch on: findings_to_issues rc 0" [ $? -eq 0 ]
  printf 'off\n' > "$ISSUES_SWITCH_FILE"; findings_to_issues; check "W6 switch off: rc 1" [ $? -eq 1 ]
  printf 'onn\n' > "$ISSUES_SWITCH_FILE"; findings_to_issues >/dev/null; rc=$?
  logged="$(grep -c "neither 'on' nor 'off'" "$LOG")"
  check "W7 a typo in the switch: rc 1 and a log line" [ "$rc|$logged" == "1|1" ]
fi

# Structure: both filing functions file issues behind the switch, and alert
# on anything unfiled before their early return.
for fn in file_health_findings file_pipeline_findings; do
  body="$(awk -v f="^$fn \\\\(\\\\) \\\\{" '$0 ~ f { on = 1 } on { print } on && /^\}/ { exit }' "$RUN_SH")"
  check "W8 $fn reads the switch" [ -n "$(printf '%s\n' "$body" | grep -F 'findings_to_issues && issues_on=1')" ]
  check "W9 $fn files each finding as an issue only when on" \
    [ -n "$(printf '%s\n' "$body" | grep -A1 -F 'if [ "$issues_on" -eq 1 ]; then' | grep -F 'file_finding_issue')" ]
  alert_ln="$(printf '%s\n' "$body" | grep -n 'send_alert "\$unfiled' | head -1 | cut -d: -f1)"
  early_ln="$(printf '%s\n' "$body" | grep -n '\[ "\$filed" -eq 0 \] && return 0' | head -1 | cut -d: -f1)"
  check "W10 $fn alerts on unfiled findings before it can return early" \
    [ "${alert_ln:-0}" -gt 0 -a "${early_ln:-0}" -gt "${alert_ln:-0}" ]
done

# End to end through file_health_findings, with the backlog and git stubbed:
# the night continues (rc 0) even when every issue fails.
eval "$(sed -n '/^file_health_findings () {/,/^}/p' "$RUN_SH")"
if declare -f file_health_findings >/dev/null; then
  HEALTH_SNAPSHOT_FILE="$T/health.md"; : > "$HEALTH_SNAPSHOT_FILE"
  BACKLOG_FILE="$T/BACKLOG.md"
  health_findings () { printf '%s\n' "examples/x/ — test fails" "examples/y/ — import error"; }
  health_item_subject () { printf '%s' "${1%% — *}"; }
  backlog_file_health_finding () { return 3; }
  # The findings file is test_findings_store.sh's to cover; here it always saves.
  record_finding () { return "$FINDING_ADDED"; }
  git () { return 0; }
  send_alert () { echo "$1" >> "$T/alerts"; }
  printf 'on\n' > "$ISSUES_SWITCH_FILE"; : > "$T/alerts"; rm -f "$UNFILED_FINDINGS_FILE"
  reset_gh
  FAKE_GH_FAIL=list file_health_findings >/dev/null; rc=$?
  check "E1 every issue failing: file_health_findings still returns 0" [ "$rc" -eq 0 ]
  check "E2 ...both findings are in the unfiled file" [ "$(grep -c '^- \[health\]' "$UNFILED_FINDINGS_FILE")" == 2 ]
  check "E3 ...and one alert was raised, counting them" [ "$(wc -l < "$T/alerts" | tr -d ' ')|$(grep -c '^2 health finding' "$T/alerts")" == "1|1" ]
  reset_gh; : > "$T/alerts"
  file_health_findings >/dev/null
  check "E4 gh working: two issues opened even though the backlog had both (rc 3)" [ "$(q 'len(issues)')" == 2 ]
  check "E5 ...and no alert" [ ! -s "$T/alerts" ]
  printf 'off\n' > "$ISSUES_SWITCH_FILE"; reset_gh
  file_health_findings >/dev/null
  check "E6 switch off: gh is never called" [ "$(ncalls)" == 0 ]
  unset -f git
else
  bad "E0 could not extract file_health_findings"
fi

echo ""
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
