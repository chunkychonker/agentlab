#!/bin/bash
# Offline tests for the private findings file (spec #7, step 1):
# findings_store.py through its command line (findings.sh's record_finding),
# and its run.sh wiring. Writes only under a temp dir.
# Run: bash .pipeline/test_findings_store.sh
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
REPO="$(pwd)"
RUN_SH="$REPO/.pipeline/run.sh"
# shellcheck source=findings.sh
. .pipeline/findings.sh

PASS=0; FAIL=0
ok ()  { PASS=$((PASS+1)); echo "ok   $1"; }
bad () { FAIL=$((FAIL+1)); echo "FAIL $1"; }
check () { local name="$1"; shift; if "$@"; then ok "$name"; else bad "$name"; fi; }

T="$(mktemp -d)"
trap 'chmod -R u+w "$T"; rm -rf "$T"' EXIT
F="$T/findings.md"
FINDINGS_FILE="$F"
# rec <label> <date> <subject> <detail> — record_finding, rc in $rc, stderr dropped.
rec () { record_finding "$@" 2>/dev/null; rc=$?; }
# The pipeline's lines only.
managed () { grep -E '^- \[[ xX]\] \[(health|pipeline)\] ' "$1"; }

# --- A1: a new finding adds exactly one line ---
rec pipeline 2026-11-09 "NETWORK UNREACHABLE" "after 3 attempts"
check "A1 a new finding: rc $FINDING_ADDED" [ "$rc" -eq "$FINDING_ADDED" ]
check "A1 ...the file starts with the header" grep -qx '# Findings' "$F"
check "A1 ...and has exactly the one line, in the spec's format" \
  [ "$(managed "$F")" == "- [ ] [pipeline] NETWORK UNREACHABLE — seen 1× (first 2026-11-09, last 2026-11-09) — after 3 attempts" ]

# --- A2: seen on a later night updates the line, never adds one ---
rec pipeline 2026-11-10 "NETWORK UNREACHABLE" "after 5 attempts"
check "A2 seen again: rc $FINDING_SEEN_AGAIN" [ "$rc" -eq "$FINDING_SEEN_AGAIN" ]
check "A2 ...still one line, count, last date and detail updated" \
  [ "$(managed "$F")" == "- [ ] [pipeline] NETWORK UNREACHABLE — seen 2× (first 2026-11-09, last 2026-11-10) — after 5 attempts" ]

# --- A3: the same night twice gives the same file as once ---
cp "$F" "$T/once"
rec pipeline 2026-11-10 "NETWORK UNREACHABLE" "after 5 attempts"
check "A3 the same night again: rc $FINDING_SEEN_AGAIN" [ "$rc" -eq "$FINDING_SEEN_AGAIN" ]
check "A3 ...and the file is byte for byte unchanged" cmp -s "$F" "$T/once"
rec pipeline 2026-11-10 "NETWORK UNREACHABLE" "a newer detail"
check "A3 a re-run with a new detail refreshes the detail, never the count" \
  [ "$(managed "$F")" == "- [ ] [pipeline] NETWORK UNREACHABLE — seen 2× (first 2026-11-09, last 2026-11-10) — a newer detail" ]
cp "$F" "$T/before-old"
rec pipeline 2026-11-01 "NETWORK UNREACHABLE" "an older night"
check "A3 an older night than the line's last: rc $FINDING_SEEN_AGAIN, file unchanged" \
  [ "$rc|$(cmp -s "$F" "$T/before-old" && echo same)" == "$FINDING_SEEN_AGAIN|same" ]

# --- A4: a finding marked fixed that comes back is reopened, count kept ---
sed -i '' 's/^- \[ \] \[pipeline\] NETWORK/- [x] [pipeline] NETWORK/' "$F"
cp "$F" "$T/fixed"
rec pipeline 2026-11-10 "NETWORK UNREACHABLE" "same night it was fixed"
check "A4 fixed, then the same night again: stays fixed, file unchanged" cmp -s "$F" "$T/fixed"
rec pipeline 2026-11-11 "NETWORK UNREACHABLE" "after 5 attempts"
check "A4 fixed, then a later night: rc $FINDING_REOPENED" [ "$rc" -eq "$FINDING_REOPENED" ]
check "A4 ...open again, seen 3×, and the detail says it came back" \
  [ "$(managed "$F")" == "- [ ] [pipeline] NETWORK UNREACHABLE — seen 3× (first 2026-11-09, last 2026-11-11) — came back 2026-11-11: after 5 attempts" ]
sed -i '' 's/^- \[ \] \[pipeline\] NETWORK/- [X] [pipeline] NETWORK/' "$F"
rec pipeline 2026-11-12 "NETWORK UNREACHABLE" "x"
check "A4 a capital [X] counts as fixed too" [ "$rc" -eq "$FINDING_REOPENED" ]

# --- A5: the owner's own lines are kept byte for byte, wherever they sit ---
G="$T/own.md"
printf '%s\n' "# my notes" "" "looked at this one, it's the VPN" \
  "- [ ] [pipeline] examples/a/ — seen 2× (first 2026-11-01, last 2026-11-02) — old" > "$G"
printf 'a CRLF line\r\n- [ ] not mine either: [pipeline] but no count\n  indented note\nno newline at the end' >> "$G"
cp "$G" "$T/own.orig"
FINDINGS_FILE="$G"
rec pipeline 2026-11-09 "examples/a/" "new detail"
check "A5 an update in the middle of the owner's lines: rc $FINDING_SEEN_AGAIN" [ "$rc" -eq "$FINDING_SEEN_AGAIN" ]
check "A5 ...only that one line changed" \
  [ "$(diff "$T/own.orig" "$G" | grep -c '^[<>]')" == 2 ]
cp "$G" "$T/own.mid"
rec health 2026-11-09 "examples/b/" "fails"
check "A5 a new finding after a last line with no newline: the old text is an exact prefix" \
  [ "$(head -c "$(wc -c < "$T/own.mid")" "$G" | cmp -s - "$T/own.mid" && echo same)" == same ]
check "A5 ...the owner's last line got a newline and the new line follows it" \
  [ "$(tail -n 2 "$G")" == "no newline at the end
- [ ] [health] examples/b/ — seen 1× (first 2026-11-09, last 2026-11-09) — fails" ]
check "A5 ...the CRLF line still ends in CRLF" grep -q $'^a CRLF line\r$' "$G"
check "A5 ...and no header was added to a file that had content" [ "$(grep -c '^# Findings' "$G")" == 0 ]

# The label is part of the identity.
rec pipeline 2026-11-09 "examples/b/" "fails"
check "A5 the same subject under the other label is a separate line" \
  [ "$rc|$(managed "$G" | grep -c 'examples/b/')" == "$FINDING_ADDED|2" ]
FINDINGS_FILE="$F"

# --- A6: a failed write leaves the file as it was ---
D="$T/ro"; mkdir -p "$D"; FINDINGS_FILE="$D/findings.md"
rec health 2026-11-09 "examples/x/" "one"
cp "$FINDINGS_FILE" "$T/ro.orig"; chmod 555 "$D"
rec health 2026-11-10 "examples/x/" "two"
check "A6 an unwritable directory: rc 1" [ "$rc" -eq 1 ]
check "A6 ...the file is unchanged and no temp file was left" \
  [ "$(cmp -s "$FINDINGS_FILE" "$T/ro.orig" && echo same)|$(ls -A "$D" | wc -l | tr -d ' ')" == "same|1" ]
chmod 755 "$D"
printf '\xff\xfe not utf-8\n' > "$T/bin.md"; cp "$T/bin.md" "$T/bin.orig"; FINDINGS_FILE="$T/bin.md"
rec health 2026-11-10 "examples/x/" "two"
check "A6 a file that is not UTF-8: rc 1, unchanged" [ "$rc|$(cmp -s "$T/bin.md" "$T/bin.orig" && echo same)" == "1|same" ]
FINDINGS_FILE="$T/sub/dir/findings.md"
rec health 2026-11-10 "examples/x/" "two"
check "A6 a missing directory: rc 1" [ "$rc" -eq 1 ]

# --- checked at the boundary ---
FINDINGS_FILE="$T/v.md"
for bad_date in 2026-02-30 2026-11-9 "" yesterday; do
  rec health "$bad_date" "examples/x/" "d"
  check "V1 bad date '$bad_date': rc 1, no file" [ "$rc" -eq 1 -a ! -e "$FINDINGS_FILE" ]
done
rec health 2026-11-09 "   " "d";                 check "V2 blank subject: rc 1" [ "$rc" -eq 1 ]
rec health 2026-11-09 "a — b" "d";               check "V3 a subject with ' — ': rc 1" [ "$rc" -eq 1 ]
rec issues 2026-11-09 "examples/x/" "d";         check "V4 an unknown label: rc 2 (usage)" [ "$rc" -eq 2 ]
check "V5 ...none of them created the file" [ ! -e "$FINDINGS_FILE" ]
rec health 2026-11-09 "-starts with a dash" "--so does this"
check "V6 a subject and detail starting with '-' are taken as text" \
  [ "$rc|$(managed "$FINDINGS_FILE")" == "0|- [ ] [health] -starts with a dash — seen 1× (first 2026-11-09, last 2026-11-09) — --so does this" ]
long="$(printf 'y%.0s' $(seq 1 250))"
rec health 2026-11-09 "examples/long/" "$long"
check "V7 a detail over 200 characters is cut and ends in '...'" \
  [ "$(managed "$FINDINGS_FILE" | grep -c " — $(printf 'y%.0s' $(seq 1 200))\.\.\.$")" == 1 ]
rec health 2026-11-09 $'examples/multi\n  line/' $'a\tdetail\non two lines'
rec health 2026-11-10 "examples/multi line/" "again"
check "V8 whitespace runs collapse, so a reworded-by-whitespace subject is the same finding" \
  [ "$rc|$(managed "$FINDINGS_FILE" | grep -c 'examples/multi line/ — seen 2×')" == "$FINDING_SEEN_AGAIN|1" ]
rec health 2026-11-09 "examples/nodetail/" ""
check "V9 no detail: no trailing dash" \
  grep -qxF -- "- [ ] [health] examples/nodetail/ — seen 1× (first 2026-11-09, last 2026-11-09)" "$FINDINGS_FILE"

# --- run.sh wiring ---
eval "$(sed -n '/^file_health_findings () {/,/^}/p' "$RUN_SH")"
eval "$(sed -n '/^file_pipeline_findings () {/,/^}/p' "$RUN_SH")"
if ! declare -f file_health_findings >/dev/null || ! declare -f file_pipeline_findings >/dev/null; then
  bad "W0 could not extract the filing functions from run.sh — the W cases are vacuous"
else
  LOG="$T/run.log"; TS="2026-11-09_020001"
  HEALTH_SNAPSHOT_FILE="$T/health.md"; PIPELINE_SNAPSHOT_FILE="$T/pipeline.md"
  : > "$HEALTH_SNAPSHOT_FILE"; : > "$PIPELINE_SNAPSHOT_FILE"
  BACKLOG_FILE="$T/BACKLOG.md"; PIPELINE_PAUSES_FILE="$T/pauses"
  health_findings () { printf '%s\n' "examples/x/ — test fails" "examples/y/ — import error"; }
  pipeline_findings () { printf '%s\n' "schedule gap 2026-10-05 — no run" "NETWORK UNREACHABLE — 3 attempts"; }
  health_item_subject () { printf '%s' "${1%% — *}"; }
  pipeline_gap_date () { case "$1" in "schedule gap "*) echo 2026-10-05 ;; *) return 1 ;; esac; }
  pipeline_date_paused () { return 0; }
  # The backlog already holds every finding (rc 3): the file is written anyway.
  backlog_file_health_finding () { return 3; }
  findings_to_issues () { return 1; }
  git () { return 0; }
  send_alert () { echo "$1" >> "$T/alerts"; }

  FINDINGS_FILE="$T/w.md"; : > "$LOG"; : > "$T/alerts"
  file_health_findings >/dev/null; rc=$?
  file_pipeline_findings >/dev/null; rc2=$?
  check "W1 both filing functions return 0" [ "$rc|$rc2" == "0|0" ]
  check "W2 every finding is saved, even ones the backlog already had" \
    [ "$(managed "$FINDINGS_FILE" | cut -d' ' -f5- | sed 's/ — seen.*//' | LC_ALL=C sort | tr '\n' '|')" == "NETWORK UNREACHABLE|examples/x/|examples/y/|" ]
  check "W3 ...a gap inside a declared pause is not saved" [ "$(grep -c 'schedule gap' "$FINDINGS_FILE")" == 0 ]
  check "W4 ...under the right labels" \
    [ "$(managed "$FINDINGS_FILE" | grep -c '\[health\] examples/')|$(managed "$FINDINGS_FILE" | grep -c '\[pipeline\] NETWORK')" == "2|1" ]
  check "W5 ...the log has each observer's summary line" \
    [ "$(grep -c '^findings file: 2 new, 0 seen again, 0 reopened, 0 not saved\.$' "$LOG")|$(grep -c '^findings file: 1 new, 0 seen again' "$LOG")" == "1|1" ]
  check "W6 ...and no alert" [ ! -s "$T/alerts" ]
  TS="2026-11-10_020001"; : > "$LOG"
  file_health_findings >/dev/null
  check "W7 the next night: counted as seen again" grep -q '^findings file: 0 new, 2 seen again, 0 reopened, 0 not saved\.$' "$LOG"

  # A6 for run.sh: nothing saved, the night continues, one alert per observer.
  mkdir -p "$T/ro2"; chmod 555 "$T/ro2"; FINDINGS_FILE="$T/ro2/findings.md"; : > "$T/alerts"; : > "$LOG"
  file_health_findings >/dev/null; rc=$?
  file_pipeline_findings >/dev/null; rc2=$?
  chmod 755 "$T/ro2"
  check "W8 the file unwritable: both still return 0" [ "$rc|$rc2" == "0|0" ]
  check "W9 ...one alert each, counting the unsaved findings" \
    [ "$(wc -l < "$T/alerts" | tr -d ' ')|$(grep -c '^2 health finding(s) could not be saved' "$T/alerts")|$(grep -c '^1 pipeline finding(s) could not be saved' "$T/alerts")" == "2|1|1" ]
  check "W10 ...and the store's reason is in the run log" grep -q 'findings_store.py: ' "$LOG"
  unset -f git
fi

# Structure: the alert comes before the early return, as for the issues.
for fn in file_health_findings file_pipeline_findings; do
  body="$(awk -v f="^$fn \\\\(\\\\) \\\\{" '$0 ~ f { on = 1 } on { print } on && /^\}/ { exit }' "$RUN_SH")"
  alert_ln="$(printf '%s\n' "$body" | grep -n 'send_alert "\$unsaved' | head -1 | cut -d: -f1)"
  early_ln="$(printf '%s\n' "$body" | grep -n '\[ "\$filed" -eq 0 \] && return 0' | head -1 | cut -d: -f1)"
  check "W11 $fn alerts on unsaved findings before it can return early" \
    [ "${alert_ln:-0}" -gt 0 -a "${early_ln:-0}" -gt "${alert_ln:-0}" ]
done
check "W12 run.sh sources findings.sh" grep -qE '^for lib in .* findings( |;)' "$RUN_SH"
check "W13 logs/ is git-ignored, so the file is never pushed" grep -qx 'logs/' "$REPO/.gitignore"
check "W14 the startup scrub keeps logs/" grep -q "git clean -fdx -e logs/" "$RUN_SH"

echo ""
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
