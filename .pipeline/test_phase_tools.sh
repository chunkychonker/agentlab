#!/bin/bash
# Offline tests for .pipeline/phase_tools.sh (spec #3, D1) and
# .claude/hooks/publish_guard.py (D3). No model is called.
# Run: bash .pipeline/test_phase_tools.sh
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
# shellcheck source=phase_tools.sh
. .pipeline/phase_tools.sh

PASS=0; FAIL=0
ok ()  { PASS=$((PASS+1)); echo "ok   $1"; }
bad () { FAIL=$((FAIL+1)); echo "FAIL $1"; }
check () { local name="$1"; shift; if "$@"; then ok "$name"; else bad "$name"; fi; }

KEYS="research build review maintain replenish health observe"

# The values that follow one flag, up to the next flag. Reads phase_tool_args
# output on stdin.
values_of () {
  awk -v f="$1" '$0 ~ /^--/ { on = ($0 == f); next } on { print }'
}
args_of () { phase_tool_args "$1"; }
# rc 0 if <rule> is among the values of <flag> for <key>.
has () { args_of "$1" | values_of "$2" | grep -qxF -- "$3"; }

# --- D1: each phase's argument list ---

for k in $KEYS; do
  out="$(args_of "$k")"; rc=$?
  check "P1 $k: known key returns 0 and prints arguments" [ "$rc" -eq 0 -a -n "$out" ]
  check "P2 $k: runs under dontAsk" [ "$(printf '%s\n' "$out" | values_of --permission-mode)" == "dontAsk" ]
  check "P3 $k: never bypassPermissions" [ -z "$(printf '%s\n' "$out" | grep -F bypassPermissions)" ]
done

out="$(phase_tool_args bogus)"; rc=$?
check "P4 an unknown key fails closed: rc 1, no output" [ "$rc|$out" == "1|" ]
out="$(phase_tool_args "")"; rc=$?
check "P4b an empty key fails closed" [ "$rc|$out" == "1|" ]

tools="$(args_of research | values_of --tools)"
check "P5 research: --tools leaves Bash out entirely" [ -z "$(printf '%s\n' "$tools" | tr ',' '\n' | grep -x Bash)" ]
check "P5b research: Bash is also denied" has research --disallowedTools Bash
check "P5c research: no Bash(...) rule is allowed" [ -z "$(args_of research | values_of --allowedTools | grep '^Bash')" ]
check "P5d research: may search the web" has research --allowedTools WebSearch
check "P5e research: may use hn-search" has research --allowedTools mcp__hn-search__search_stories
check "P5f research: may write research notes" has research --allowedTools "Write(research/**)"
check "P5g research: no unrestricted Write" [ -z "$(args_of research | values_of --allowedTools | grep -xE 'Write|Edit')" ]

# Only maintain may publish.
for k in $KEYS; do
  allowed="$(args_of "$k" | values_of --allowedTools)"
  pushes=0; prs=0
  printf '%s\n' "$allowed" | grep -qxF "Bash(git push *)" && pushes=1
  printf '%s\n' "$allowed" | grep -qxF "Bash(gh pr create *)" && prs=1
  if [ "$k" = maintain ]; then
    check "P6 maintain: git push and gh pr create are allowed" [ "$pushes$prs" == "11" ]
  else
    check "P6 $k: neither git push nor gh pr create is allowed" [ "$pushes$prs" == "00" ]
  fi
done

# Phases with Bash must deny publishing in every spelling, including the rtk
# form the RTK hook rewrites to.
for k in build review; do
  for rule in "Bash(git push *)" "Bash(rtk git push *)" "Bash(git commit *)" \
              "Bash(rtk git commit *)" "Bash(gh *)" "Bash(rtk gh *)"; do
    check "P7 $k denies $rule" has "$k" --disallowedTools "$rule"
  done
done
for k in health observe; do
  for rule in "Bash(git push *)" "Bash(rtk git push *)" "Bash(gh pr create *)" \
              "Bash(rtk gh pr create *)" "Bash(gh pr merge *)" "Bash(gh api *)"; do
    check "P7 $k denies $rule" has "$k" --disallowedTools "$rule"
  done
  check "P7b $k may still read PRs (gh pr view is not denied)" \
    [ -z "$(args_of "$k" | values_of --disallowedTools | grep -xF 'Bash(gh *)')" ]
done
check "P8 maintain: no web tools" has maintain --disallowedTools WebSearch
check "P8b maintain: never merges" has maintain --disallowedTools "Bash(gh pr merge *)"
check "P8c maintain: never force-pushes" has maintain --disallowedTools "Bash(git push --force *)"
check "P8d maintain: no unrestricted Bash" [ -z "$(args_of maintain | values_of --allowedTools | grep -x Bash)" ]
check "P9 replenish: no Bash" has replenish --disallowedTools Bash
check "P9b replenish: edits only BACKLOG.md" \
  [ "$(args_of replenish | values_of --allowedTools | grep -E '^(Edit|Write)' | sort | tr '\n' ' ')" == "Edit(BACKLOG.md) Write(BACKLOG.md) " ]
check "P10 review: may write its verdict" has review --allowedTools "Write(logs/last-review.md)"

# Each phase with an agent names the agent file that exists.
for k in $KEYS; do
  agent="$(args_of "$k" | values_of --agent)"
  [ -z "$agent" ] && continue
  check "P11 $k: agent $agent exists" [ -f ".claude/agents/$agent.md" ]
done
check "P11b the researcher's agent file lists no Bash tool" \
  [ -z "$(sed -n '/^tools:/p' .claude/agents/agentlab-researcher.md | tr ', ' '\n\n' | grep -x Bash)" ]
check "P11c the maintainer's agent file lists Write" \
  [ -n "$(sed -n '/^tools:/p' .claude/agents/agentlab-maintainer.md | tr ', ' '\n\n' | grep -x Write)" ]

# File rules are passed literally: a glob must never expand against real files.
mkdir -p logs
touch logs/lab-health-PHASE-TOOLS-TEST.log
check "P12 globs in file rules are not expanded" has health --allowedTools "Write(logs/lab-health-*)"
rm -f logs/lab-health-PHASE-TOOLS-TEST.log

# --- D3: the publish guard hook ---

HOOK=.claude/hooks/publish_guard.py
PY=/usr/bin/python3
# guard <phase|-> <payload-json> -> exit code. "-" means AGENTLAB_PHASE unset.
guard () {
  if [ "$1" = "-" ]; then
    printf '%s' "$2" | env -u AGENTLAB_PHASE "$PY" "$HOOK" 2>/dev/null
  else
    printf '%s' "$2" | AGENTLAB_PHASE="$1" "$PY" "$HOOK" 2>/dev/null
  fi
  echo $?
}
bash_payload () { "$PY" -c 'import json,sys; print(json.dumps({"tool_name":"Bash","tool_input":{"command":sys.argv[1]}}))' "$1"; }

check "H1 build: git push blocks (exit 2)" [ "$(guard build "$(bash_payload 'git push origin main')")" == 2 ]
check "H2 maintain: git push is allowed" [ "$(guard maintain "$(bash_payload 'git push -u origin HEAD')")" == 0 ]
check "H3 build: rtk git push blocks" [ "$(guard build "$(bash_payload 'rtk git push')")" == 2 ]
check "H4 build: git -C <dir> push blocks" [ "$(guard build "$(bash_payload 'git -C /tmp/x push origin HEAD')")" == 2 ]
check "H5 build: git -c k=v push blocks" [ "$(guard build "$(bash_payload 'git -c user.name=x push')")" == 2 ]
check "H6 build: bash -c \"git push\" blocks" [ "$(guard build "$(bash_payload 'bash -c "git push"')")" == 2 ]
check "H7 build: push after && blocks" [ "$(guard build "$(bash_payload 'git add -A && git push')")" == 2 ]
check "H8 build: push in \$( ) blocks" [ "$(guard build "$(bash_payload 'echo $(git push)')")" == 2 ]
check "H9 build: env-prefixed push blocks" [ "$(guard build "$(bash_payload 'GIT_TRACE=1 git push')")" == 2 ]
check "H10 build: push on a second line blocks" [ "$(guard build "$(bash_payload "$(printf 'git status\ngit push')")")" == 2 ]
check "H11 build: gh pr create blocks" [ "$(guard build "$(bash_payload 'gh pr create --fill')")" == 2 ]
check "H12 research: gh api blocks" [ "$(guard research "$(bash_payload 'gh api repos/x/y')")" == 2 ]
check "H13 health: gh pr view is allowed" [ "$(guard health "$(bash_payload 'gh pr view 12 --json state -q .state')")" == 0 ]
check "H14 observe: gh pr list is allowed" [ "$(guard observe "$(bash_payload 'gh pr list --state merged')")" == 0 ]
check "H15 health: gh pr merge blocks" [ "$(guard health "$(bash_payload 'gh pr merge 12')")" == 2 ]
check "H16 build: git status is allowed" [ "$(guard build "$(bash_payload 'git status --porcelain')")" == 0 ]
check "H17 build: a commit message mentioning push is allowed" [ "$(guard build "$(bash_payload 'git log --grep "git push"')")" == 0 ]
check "H18 build: a heredoc with an apostrophe is allowed" \
  [ "$(guard build "$(bash_payload "$(printf "cat > notes.md <<'EOF'\nit's fine\nEOF")")")" == 0 ]
check "H19 unset phase blocks (fails closed)" [ "$(guard - "$(bash_payload 'ls')")" == 2 ]
check "H20 empty phase blocks" [ "$(guard "" "$(bash_payload 'ls')")" == 2 ]
check "H21 malformed payload blocks" [ "$(guard build 'not json')" == 2 ]
check "H22 non-object payload blocks" [ "$(guard build '[1,2]')" == 2 ]
check "H23 Bash call with no command blocks" [ "$(guard build '{"tool_name":"Bash","tool_input":{}}')" == 2 ]
check "H24 a non-Bash tool is allowed" [ "$(guard build '{"tool_name":"Read","tool_input":{"file_path":"x"}}')" == 0 ]
check "H25 unparseable text mentioning git push blocks" [ "$(guard build "$(bash_payload "echo 'oops; git push")")" == 2 ]
check "H26 too-deep shell nesting blocks" \
  [ "$(guard build "$(bash_payload "bash -c \"bash -c 'bash -c \\\"bash -c ls\\\"'\"")")" == 2 ]
reason="$(printf '%s' "$(bash_payload 'git push')" | AGENTLAB_PHASE=build "$PY" "$HOOK" 2>&1 >/dev/null)"
check "H27 a block names the command and the phase on stderr" \
  [ -n "$(printf '%s' "$reason" | grep -F "git push" | grep -F build)" ]

# The settings file run.sh passes with --settings registers exactly this hook.
check "H28 phase_settings.json is valid JSON and registers publish_guard.py" \
  [ "$(/usr/bin/jq -r '.hooks.PreToolUse[0].hooks[0].command' .pipeline/phase_settings.json | grep -c 'publish_guard.py')" == 1 ]

echo ""
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
