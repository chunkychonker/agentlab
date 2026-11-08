#!/bin/bash
# Offline tests for .pipeline/migrate_backlog_findings.sh (spec #6, step 2).
# Each case runs the script in a throwaway copy of the repo with gh replaced by
# .pipeline/fake_gh.py: no network, no real issues, the real BACKLOG.md is
# never touched.
# Run: bash .pipeline/test_migrate_backlog_findings.sh
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
REPO="$(pwd)"

PASS=0; FAIL=0
ok ()  { PASS=$((PASS+1)); echo "ok   $1"; }
bad () { FAIL=$((FAIL+1)); echo "FAIL $1"; }
check () { local name="$1"; shift; if "$@"; then ok "$name"; else bad "$name"; fi; }

T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
mkdir -p "$T/bin"
printf '#!/bin/bash\nexec /usr/bin/python3 "%s/.pipeline/fake_gh.py" "$@"\n' "$REPO" > "$T/bin/gh"
chmod +x "$T/bin/gh"
export PATH="$T/bin:$PATH" FAKE_GH_DIR="$T/gh" MIGRATE_PAUSE_SECONDS=0
W="$T/repo"
M="$W/.pipeline/migrate_backlog_findings.sh"

# Six unclaimed findings, two of which share a subject (one duplicate), one
# with a backslash; plus lines that must survive: a claimed finding, a done
# finding, a portfolio topic, and a stranded-work line that looks like a
# finding.
cat > "$T/BACKLOG.fixture" <<'EOF'
# Backlog

## Coding agents
- [ ] Build a tool-use loop with retries
- [building] Streaming accumulator for thinking blocks

## Health-check findings
- [done #42] fix (health 2026-09-02): BACKLOG.md:24 marked [building] — never advanced
- [building] fix (health 2026-09-15): run-2026-09-03_134704 — claimed by a cycle
- [ ] fix (health 2026-09-15): run-2026-09-05_130001 — shipped 0/2, session limit
- [ ] fix (health 2026-09-15): no run log for 2026-09-04
- [ ] fix (health 2026-09-21): examples/prompt-caching-tool-loop/ — README transcript drift: line 24
- [ ] fix (health 2026-09-28): run-2026-09-05_130001 — seen again, same subject
- [ ] fix (health 2026-09-28): examples/regex-tool/ — pattern '\d+' in README does not match output
- [ ] fix (health 2026-10-01): [[missing-note]] in knowledge/INDEX.md — dangling wikilink

## Stranded work (unshipped branches)
- [ ] fix (health 2026-10-02): cycle/2026-10-02-unshipped-1 — keep me, I am stranded work
EOF
CANDIDATES=6
DUPLICATES=1

# A fresh copy of the repo and an empty GitHub, with the switch set to $1.
fresh () {
  rm -rf "$W" "$FAKE_GH_DIR"; mkdir -p "$W/.pipeline" "$FAKE_GH_DIR"; : > "$FAKE_GH_DIR/calls"
  cp "$REPO"/.pipeline/{migrate_backlog_findings,review_record,issues,backlog,health}.sh "$W/.pipeline/"
  cp "$T/BACKLOG.fixture" "$W/BACKLOG.md"
  printf '%s\n' "$1" > "$W/.pipeline/file-issues"
}
q () {
  /usr/bin/python3 -c 'import json,os,sys
p=os.path.join(os.environ["FAKE_GH_DIR"],"issues.json")
issues=json.load(open(p)) if os.path.exists(p) else []
print(eval(sys.argv[1]))' "$1"
}
ncalls () { wc -l < "$FAKE_GH_DIR/calls" | tr -d ' '; }
creates () { grep -c '"create"' "$FAKE_GH_DIR/calls" || true; }
unclaimed_findings () { grep -c '^- \[ \] fix (health' "$W/BACKLOG.md" | tr -d ' '; }
# Every line that is not an unclaimed finding outside the stranded section.
kept_lines () {
  awk '/^## / { s = index($0, "## Stranded work") == 1 } s || index($0, "- [ ] fix (health ") != 1' "$1"
}

# --- usage and the switch ---

fresh on
bash "$M" --bogus >/dev/null 2>&1; rc=$?
check "G1 an unknown flag is a usage error (rc 2), nothing touched" [ "$rc|$(ncalls)" == "2|0" ]
cmp -s "$W/BACKLOG.md" "$T/BACKLOG.fixture"; check "G1b ...BACKLOG.md unchanged" [ $? -eq 0 ]

fresh off
out="$(bash "$M" 2>&1)"; rc=$?
check "G2 switch off: a live run refuses (rc 1)" [ "$rc" -eq 1 ]
same=no; cmp -s "$W/BACKLOG.md" "$T/BACKLOG.fixture" && same=yes
check "G3 ...calls no gh and leaves BACKLOG.md alone" [ "$(ncalls)|$same" == "0|yes" ]
check "G3b ...and says why" [ -n "$(printf '%s' "$out" | grep -F "does not say 'on'")" ]

# --- dry run ---

fresh off
out="$(bash "$M" --dry-run 2>&1)"; rc=$?
check "D1 the dry run works with the switch off (rc 0)" [ "$rc" -eq 0 ]
check "D2 ...calls no gh at all" [ "$(ncalls)" == 0 ]
cmp -s "$W/BACKLOG.md" "$T/BACKLOG.fixture"; check "D3 ...and leaves BACKLOG.md byte for byte" [ $? -eq 0 ]
check "D4 ...lists every candidate" [ "$(printf '%s\n' "$out" | grep -c '^  would file ')" == "$CANDIDATES" ]
check "D5 ...an example's finding is labeled health" [ -n "$(printf '%s\n' "$out" | grep -xF '  would file [health] examples/prompt-caching-tool-loop/')" ]
check "D6 ...a wikilink finding is labeled health" [ -n "$(printf '%s\n' "$out" | grep -F '[health] [[missing-note]]')" ]
check "D7 ...a run log's finding is labeled pipeline" [ -n "$(printf '%s\n' "$out" | grep -xF '  would file [pipeline] run-2026-09-05_130001')" ]
check "D8 ...nothing from the stranded section" [ -z "$(printf '%s\n' "$out" | grep -F 'unshipped-1')" ]

# --- the migration (F1, F4) ---

fresh on
out="$(bash "$M" 2>&1)"; rc=$?
check "F1a a clean migration returns 0" [ "$rc" -eq 0 ]
check "F1b (F1) no unclaimed finding is left outside the stranded section" \
  [ "$(kept_lines "$W/BACKLOG.md" | grep -c '^- \[ \] fix (health')" == 1 ]
check "F1c (F1) open issues = migrated minus duplicates ($CANDIDATES - $DUPLICATES)" \
  [ "$(q 'sum(1 for i in issues if i["state"] == "OPEN")')" == "$(( CANDIDATES - DUPLICATES ))" ]
check "F1d the duplicate became a comment on the first issue" \
  [ "$(q '[len(i["comments"]) for i in issues if i["title"] == "run-2026-09-05_130001"]')" == "[1]" ]
check "F1e every other line is untouched, in order" \
  [ "$(kept_lines "$W/BACKLOG.md")" == "$(kept_lines "$T/BACKLOG.fixture")" ]
check "F1f labels follow the subject" \
  [ "$(q 'sorted((i["labels"][0], i["title"]) for i in issues)[0]')" == "('health', '[[missing-note]] in knowledge/INDEX.md')" ]
check "F1g the backslash line was filed and removed" \
  [ "$(q 'sum(1 for i in issues if "\\d+" in i["body"])')|$(grep -c 'regex-tool' "$W/BACKLOG.md")" == "1|0" ]
check "F1h the body keeps the original date and says it was migrated" \
  [ "$(q '[i["body"] for i in issues if i["title"] == "no run log for 2026-09-04"][0].count("Observed: 2026-09-15 (run migrated from BACKLOG.md)")')" == 1 ]
check "F1i the summary counts match" \
  [ -n "$(printf '%s\n' "$out" | grep -xF "5 issue(s) opened, 1 already open (commented), 0 line(s) left in place.")" ]

# F4: the researcher picks `- [ ] ` lines; none of them is a finding now
# (the stranded-section line is excluded from claims by its own rules).
. "$REPO/.pipeline/backlog.sh"
before="$(backlog_count_unclaimed "$T/BACKLOG.fixture")"; after="$(backlog_count_unclaimed "$W/BACKLOG.md")"
check "F4 (F4) the unclaimed count dropped by exactly the candidates ($before -> $after)" [ $(( before - after )) -eq "$CANDIDATES" ]

# --- re-running (F2) ---

snap="$(cat "$FAKE_GH_DIR/issues.json")"; : > "$FAKE_GH_DIR/calls"
out="$(bash "$M" 2>&1)"; rc=$?
check "F2a (F2) a second run returns 0" [ "$rc" -eq 0 ]
check "F2b (F2) ...makes no gh call at all" [ "$(ncalls)" == 0 ]
check "F2c (F2) ...and GitHub is unchanged" [ "$(cat "$FAKE_GH_DIR/issues.json")" == "$snap" ]
check "F2d ...it reports nothing to do" [ -n "$(printf '%s\n' "$out" | grep -xF '0 unclaimed finding line(s) in BACKLOG.md.')" ]

# A line whose issue already exists (a run that filed it but died before the
# delete): commented, deleted, never a second issue.
fresh on
/usr/bin/python3 - "$FAKE_GH_DIR/issues.json" <<'PY'
import json, sys
json.dump([{"number": 1, "title": "no run log for 2026-09-04", "state": "OPEN",
            "labels": ["pipeline"], "body": "", "comments": []}], open(sys.argv[1], "w"))
PY
bash "$M" >/dev/null 2>&1
check "F2e (F2) an already-filed line gets a comment, not a second issue" \
  [ "$(q 'sum(1 for i in issues if i["title"] == "no run log for 2026-09-04")')|$(q '[len(i["comments"]) for i in issues if i["number"] == 1]')" == "1|[1]" ]

# --- partial failure ---

fresh on
out="$(FAKE_GH_FAIL=create bash "$M" 2>&1)"; rc=$?
check "P1 gh create failing: rc 1" [ "$rc" -eq 1 ]
check "P2 ...nothing was deleted from BACKLOG.md" [ "$(unclaimed_findings)" == 7 ]
check "P3 ...and the summary says they were left in place" [ -n "$(printf '%s\n' "$out" | grep -F "line(s) left in place" | grep -v ' 0 line')" ]
out="$(bash "$M" 2>&1)"; rc=$?
check "P4 the next run, with gh working, finishes the job" [ "$rc|$(unclaimed_findings)|$(q 'len(issues)')" == "0|1|5" ]

fresh on
sed -i '' 's|no run log for 2026-09-04|token sk-ant-api03-abcdefghijklmnop leaked|' "$W/BACKLOG.md"
bash "$M" >/dev/null 2>&1; rc=$?
check "P5 a line with a credential is not posted and stays (rc 1)" \
  [ "$rc|$(grep -c 'sk-ant-api03' "$W/BACKLOG.md")|$(q 'sum(1 for i in issues if "sk-ant" in i["title"] + i["body"])')" == "1|1|0" ]

# --- the helpers, sourced ---

. "$REPO/.pipeline/migrate_backlog_findings.sh"
check "H1 migrate_parse_line splits date, subject and detail" \
  [ "$(migrate_parse_line '- [ ] fix (health 2026-09-15): run-1 — shipped 0/2 — twice' | tr '\n' '|')" == "2026-09-15|run-1|shipped 0/2 — twice|" ]
check "H2 ...a line with no detail" \
  [ "$(migrate_parse_line '- [ ] fix (health 2026-09-15): no run log for 2026-09-04' | tr '\n' '|')" == "2026-09-15|no run log for 2026-09-04||" ]
migrate_parse_line '- [ ] fix (health no-close-paren' >/dev/null; check "H3 ...rc 1 on a malformed line" [ $? -eq 1 ]
migrate_parse_line '- [ ] Build a thing' >/dev/null; check "H4 ...rc 1 on a topic line" [ $? -eq 1 ]
check "H5 migrate_label_for: backticked example path is health" [ "$(migrate_label_for '`examples/x/README.md` claims')" == health ]
check "H6 migrate_label_for: a claim line is pipeline" [ "$(migrate_label_for 'BACKLOG.md:216 marked [building]')" == pipeline ]
printf 'a\nb\\c\nb\\c\nd\n' > "$T/rm"
migrate_remove_line "$T/rm" 'b\c'; rc=$?
check "H7 migrate_remove_line drops only the first exact match" [ "$rc|$(tr '\n' ' ' < "$T/rm")" == '0|a b\c d ' ]
migrate_remove_line "$T/rm" 'zzz'; rc=$?
check "H8 ...rc 1 and the file unchanged when nothing matches" [ "$rc|$(tr '\n' ' ' < "$T/rm")" == '1|a b\c d ' ]

echo ""
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
