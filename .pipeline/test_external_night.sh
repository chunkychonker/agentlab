#!/bin/bash
# Offline tests for the night-level control flow in .pipeline/external_night.sh:
# the baseline gate (B2, B3, B6) and the usage-limit stop and resume (L2-L5).
#
# Real: git (a throwaway local bare repo), the verify script, the decision and
# resume functions. Stubbed: every model phase (run_phase), gh, and the
# agentlab-side commits. No network, no model.
# Run: bash .pipeline/test_external_night.sh
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
LAB_REPO="$PWD"

PASS=0; FAIL=0
ok ()  { PASS=$((PASS+1)); echo "ok   $1"; }
bad () { FAIL=$((FAIL+1)); echo "FAIL $1"; }
check () { local name="$1"; shift; if "$@"; then ok "$name"; else bad "$name"; fi; }

T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT

# --- fixtures --------------------------------------------------------------
git init --quiet --bare -b main "$T/remote.git"
git clone --quiet "$T/remote.git" "$T/seed" 2>/dev/null
( cd "$T/seed" && git config user.email t@t && git config user.name t \
  && echo app > app.txt && git add -A && git commit --quiet -m one && git push --quiet origin main ) \
  || { echo "fixture setup failed"; exit 1; }

# Verify passes iff the checkout has a file named `fixed`, or the base is
# declared healthy. Prints a VERIFY line last, like the real gate.
cat > "$T/verify.sh" <<'EOF'
#!/bin/bash
if [ -e "$1/fixed" ] || [ "${BASE_HEALTHY:-0}" -eq 1 ]; then echo "VERIFY: PASS"; exit 0; fi
echo "VERIFY: FAIL (1): routes stay in corridors"; exit 1
EOF

# --- run one night in a fresh lab dir ---------------------------------------
# night <case> : runs run_external_night in a subshell with the scenario's env:
#   CYCLES, BASE_HEALTHY, FAKE_PRS, FAKE_PRS_RC, RC_research, RC_build,
#   RC_review, RC_maintain, BUILD_FIXES (build creates `fixed`).
# Leaves $T/<case>/phases (one "<name>|<prompt>" line per phase) and
# $T/<case>/rc. KEEP_LAB=1 reuses the previous case's lab dir (for resume).
LAB="$T/lab"
night () {
  local id="$1"
  mkdir -p "$T/$id"
  if [ "${KEEP_LAB:-0}" -ne 1 ]; then
    rm -rf "$LAB"; mkdir -p "$LAB/logs" "$LAB/research" "$LAB/projects/demo"
    printf 'repo=owner/demo\nbase=main\nverify=%s\n' "$T/verify.sh" > "$LAB/projects/demo/target"
  fi
  (
    cd "$LAB" || exit 99
    export EXT_WORK_ROOT="$T/work"
    REPO="$LAB_REPO" LOG="$T/$id/log" TS="t$id"
    . "$LAB_REPO/.pipeline/usage_limit.sh"
    . "$LAB_REPO/.pipeline/verdict.sh"
    . "$LAB_REPO/.pipeline/postcondition.sh"
    . "$LAB_REPO/.pipeline/external_night.sh"

    eval "orig_$(declare -f ext_sync)"
    ext_sync () { orig_ext_sync "$1" "$T/remote.git" "$3"; }
    # Open PRs = the scenario's FAKE_PRS plus any PR a maintain phase opened tonight.
    ext_open_agentlab_prs () { printf '%s\n' "${FAKE_PRS:-}"; cat "$T/$id/opened" 2>/dev/null; return "${FAKE_PRS_RC:-0}"; }
    gh () { echo OPEN; }
    ext_scrub_pr_body () { :; }
    ext_commit_bookkeeping () { :; }
    snapshot_dirty_main () { :; }

    run_phase () {
      local name="${2##*: }" prompt="$4" var rc
      echo "$name|$prompt" >> "$T/$id/phases"
      var="RC_$name"; rc="${!var:-0}"
      [ "$rc" -eq 0 ] || return "$rc"
      case "$name" in
        research) echo note > "research/2026-10-22-demo-$id.md" ;;
        build)    echo change > "$EXT_WORK/feature.txt"
                  [ "${BUILD_FIXES:-0}" -eq 1 ] && echo y > "$EXT_WORK/fixed" ;;
        review)   echo "VERDICT: PASS" > logs/last-review.md ;;
        maintain) echo 42 > logs/last-pr.txt; echo 42 >> "$T/$id/opened" ;;
      esac
      return 0
    }
    run_external_night demo > "$T/$id/out" 2>&1
  )
  echo $? > "$T/$id/rc"
  touch "$T/$id/phases"
}
phases () { cut -d'|' -f1 "$T/$1/phases" | tr '\n' ,; }
rc () { cat "$T/$1/rc"; }

# B2: the 10-21 night. Base fails, our PR is open: no phase runs at all.
CYCLES=1 FAKE_PRS=6 night B2
check "B2 base fails with our PR open -> no research, no build" [ -z "$(phases B2)" ]
check "B2b ...the night reports no PR" [ "$(rc B2)" -eq 1 ]
check "B2c ...and says why, naming the PR" grep -q "PR(s) #6 are open" "$T/B2/log"

# B3: the 10-20 night. Base fails, nothing open: the cycle runs and must fix it.
CYCLES=1 BUILD_FIXES=1 night B3
check "B3 base fails with no PR open -> the full cycle runs" [ "$(phases B3)" == "research,build,review,maintain," ]
check "B3b ...and ships" [ "$(rc B3)" -eq 0 ]
check "B3c ...with the failure named in ext-context for the phases" \
  grep -q "fails verify on its own" "$LAB/logs/ext-context.md"

# B6: base passes with our PR open -> proceed; the PR list does not matter.
CYCLES=1 BASE_HEALTHY=1 FAKE_PRS=6 night B6
check "B6 a passing base proceeds even with our PR open" [ "$(phases B6)" == "research,build,review,maintain," ]

# B7: the PR list cannot be read while the base fails -> stop.
CYCLES=1 FAKE_PRS_RC=1 night B7
check "B7 base fails and gh fails -> no phase runs" [ -z "$(phases B7)" ]

# B8: the check runs every cycle. Cycle 1 fixes the base and opens PR 42; the
# base itself still fails, so cycle 2 sees its own open PR and stops.
CYCLES=2 BUILD_FIXES=1 night B8
check "B8 cycle 2 stops on the PR cycle 1 just opened" [ "$(phases B8)" == "research,build,review,maintain," ]
check "B8b ...and the night still reports cycle 1's PR" [ "$(rc B8)" -eq 0 ]

# L2 + L3a: a usage limit in build, CYCLES=2 -> one research, one build, stop.
CYCLES=2 BASE_HEALTHY=1 RC_build=75 night L2
check "L2 a usage limit in build stops the night: cycle 2 never starts" [ "$(phases L2)" == "research,build," ]
check "L2b ...and the night returns USAGE_LIMIT_RC" [ "$(rc L2)" -eq 75 ]
check "L3a ...and leaves a resume marker naming the research note" \
  grep -qx "note=research/2026-10-22-demo-L2.md" "$LAB/logs/ext-resume-demo"

# L4: the next night on an unchanged base skips research and names the note.
KEEP_LAB=1 CYCLES=1 BASE_HEALTHY=1 night L4
check "L4 resume skips research" [ "$(phases L4)" == "build,review,maintain," ]
check "L4b ...and the build prompt names the reused note" \
  grep -q "^build|.*research/2026-10-22-demo-L2.md" "$T/L4/phases"
check "L4c ...and the marker is gone after use" [ ! -e "$LAB/logs/ext-resume-demo" ]

# L4d: the base moves between the limit and the next night -> research again.
CYCLES=1 BASE_HEALTHY=1 RC_build=75 night L4d1
( cd "$T/seed" && echo two >> app.txt && git commit --quiet -am two && git push --quiet origin main )
KEEP_LAB=1 CYCLES=1 BASE_HEALTHY=1 night L4d2
check "L4d a moved base discards the marker and researches afresh" \
  [ "$(phases L4d2)" == "research,build,review,maintain," ]

# L3b: a limit during research leaves no marker (there is no note to reuse).
CYCLES=2 BASE_HEALTHY=1 RC_research=75 night L3b
check "L3b a limit in research writes no marker" [ ! -e "$LAB/logs/ext-resume-demo" ]
check "L3c ...and still stops the night" [ "$(phases L3b)" == "research," -a "$(rc L3b)" -eq 75 ]

# L5: an ordinary build failure keeps today's behaviour: next cycle runs, no marker.
CYCLES=2 BASE_HEALTHY=1 RC_build=1 night L5
check "L5 an ordinary failure continues to the next cycle" \
  [ "$(phases L5)" == "research,build,research,build," ]
check "L5b ...writes no marker" [ ! -e "$LAB/logs/ext-resume-demo" ]
check "L5c ...and returns 1, not USAGE_LIMIT_RC" [ "$(rc L5)" -eq 1 ]

# L7: a limit in maintain before any PR was recorded stops the night.
CYCLES=1 BASE_HEALTHY=1 RC_maintain=75 night L7
check "L7 a limit in maintain with no PR recorded stops with 75" [ "$(rc L7)" -eq 75 ]

echo ""
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
