#!/bin/bash
# Offline tests for .pipeline/models.sh and its wiring into run.sh: which model
# the build and review phases run on (research/2026-11-27-cross-model-review.md,
# acceptance criteria 1-6 and 9). No network, no model: the CLI is a stand-in script.
# Run: bash .pipeline/test_models.sh
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
REPO="$(pwd)"
RUN_SH="$REPO/.pipeline/run.sh"
# shellcheck source=models.sh
. .pipeline/models.sh

PASS=0; FAIL=0
ok ()  { PASS=$((PASS+1)); echo "ok   $1"; }
bad () { FAIL=$((FAIL+1)); echo "FAIL $1"; }
check () { local name="$1"; shift; if "$@"; then ok "$name"; else bad "$name"; fi; }

T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
NO_CONF="$T/absent.conf"

# conf <name> <line>... : writes a conf file under $T and prints its path.
conf () {
  local name="$1"; shift
  printf '%s\n' "$@" > "$T/$name"
  printf '%s\n' "$T/$name"
}
# resolves <expected> <args...> : resolve_phase_model prints exactly <expected>, rc 0.
resolves () {
  local want="$1" got; shift
  got="$(resolve_phase_model "$@" 2>/dev/null)" && [ "$got" = "$want" ]
}
# refuses <needle> <args...> : rc non-zero, nothing on stdout, <needle> in stderr.
refuses () {
  local needle="$1" out err; shift
  out="$(resolve_phase_model "$@" 2>"$T/err")" && return 1
  err="$(cat "$T/err")"
  [ -z "$out" ] && [[ "$err" == *"$needle"* ]]
}

# --- 1. no conf file, no override: opus for both ---------------------------
check "C1a no conf: build is opus"  resolves opus build "$NO_CONF"
check "C1b no conf: review is opus" resolves opus review "$NO_CONF"

# --- 2. review=sonnet moves only review -------------------------------------
c2="$(conf c2.conf 'review=sonnet')"
check "C2a review=sonnet: review is sonnet" resolves sonnet review "$c2"
check "C2b ...and build stays opus"         resolves opus build "$c2"
c2b="$(conf c2b.conf '# a comment' '' '  build = claude-opus-5-5  ' 'review=fable')"
check "C2c comments, blank lines and spaces around = are ignored" resolves claude-opus-5-5 build "$c2b"
check "C2d ...and the other key is read from the same file"     resolves fable review "$c2b"
printf 'review=sonnet\r\n' > "$T/crlf.conf"
check "C2e a CRLF line ending is not part of the value" resolves sonnet review "$T/crlf.conf"
printf 'review=sonnet' > "$T/nonl.conf"
check "C2f a last line with no newline is still read" resolves sonnet review "$T/nonl.conf"

# --- 3. the override beats the conf ------------------------------------------
check "C3a an override beats the conf" resolves claude-opus-5-5 review "$c2" claude-opus-5-5
check "C3b an override with no conf file" resolves sonnet build "$NO_CONF" sonnet
check "C3c an empty override is refused, not treated as unset" refuses "override ''" review "$c2" ""
check "C3d an invalid override is refused" refuses "override 'opsu'" review "$c2" opsu
bad_with_override="$(conf c3e.conf 'review=opsu')"
check "C3e a broken conf is refused even when an override is given" \
  refuses "$bad_with_override:1:" review "$bad_with_override" sonnet

# --- 4. every bad conf names its file and line ------------------------------
c4a="$(conf c4a.conf '# models' 'review=opsu')"
check "C4a review=opsu: refused, file and line 2 named" refuses "$c4a:2: invalid model 'opsu'" review "$c4a"
c4b="$(conf c4b.conf 'review=')"
check "C4b review= : refused as empty" refuses "$c4b:1: empty model for 'review'" review "$c4b"
c4c="$(conf c4c.conf 'review=opus' 'build=opus' 'review=opus')"
check "C4c review set twice: refused on the repeat" \
  refuses "$c4c:3: duplicate key 'review' (already set on line 1)" review "$c4c"
check "C4c' ...even when asking for the other phase" refuses "$c4c:3:" build "$c4c"
c4d="$(conf c4d.conf 'lint=opus')"
check "C4d unknown key lint: refused" refuses "$c4d:1: unknown key 'lint'" review "$c4d"
check "C4e phase research: refused" refuses "unknown phase 'research'" research "$NO_CONF"
check "C4e' ...and an empty phase" refuses "unknown phase ''" "" "$NO_CONF"
c4f="$(conf c4f.conf 'review opus')"
check "C4f a line with no = : refused" refuses "$c4f:1: expected" review "$c4f"
mkdir "$T/dir.conf"
check "C4g a conf path that is a directory: refused" refuses "cannot be read" review "$T/dir.conf"
c4h="$(conf c4h.conf 'review=Opus')"
check "C4h a capitalised alias in the conf: refused" refuses "$c4h:1: invalid model 'Opus'" review "$c4h"

# --- 5. is_valid_model -------------------------------------------------------
for m in opus sonnet fable claude-opus-5-5 claude-fable-5-1 claude-sonnet-4-5-20250929 claude-x.y; do
  check "C5 accepts '$m'" is_valid_model "$m"
done
for m in Opus "claude opus" claude- claude-- claude-opus- "opus;rm -rf /" 'claude-$(id)' \
         'claude-a;b' "" " opus" "opus " "haiku" "opus[1m]" "CLAUDE-OPUS-5" "claude-opus-5
x"; do
  check "C5 rejects '$m'" bash -c '. "$1"; ! is_valid_model "$2"' _ "$REPO/.pipeline/models.sh" "$m"
done

# --- 6. run.sh's own wiring -------------------------------------------------
# The model block, extracted verbatim from run.sh and run in a scratch dir, so
# what is tested is what runs at night (models.conf path, env names, abort).
MODELS_BLOCK="$(sed -n '/^MODELS_CONF=/,/^echo "models: /p' "$RUN_SH")"
check "C6a the model block was extracted from run.sh" [ "$(printf '%s\n' "$MODELS_BLOCK" | wc -l | tr -d ' ')" -ge 9 ]

# models_at <dir> [VAR=value...] : runs the block in <dir>; prints
# "<rc>|<BUILD_MODEL>|<REVIEW_MODEL>" and leaves <dir>/log.
models_at () {
  local dir="$1"; shift
  ( cd "$dir" && env -u AGENTLAB_BUILD_MODEL -u AGENTLAB_REVIEW_MODEL "$@" bash -c '
      set -uo pipefail
      . "$1"; LOG=log; BUILD_MODEL=; REVIEW_MODEL=
      eval "$2" >/dev/null
      echo "0|$BUILD_MODEL|$REVIEW_MODEL"' _ "$REPO/.pipeline/models.sh" "$MODELS_BLOCK" ) || echo "$?||"
}
mkdir -p "$T/w-none/.pipeline" "$T/w-sonnet/.pipeline" "$T/w-bad/.pipeline"
printf 'review=sonnet\n' > "$T/w-sonnet/.pipeline/models.conf"
printf 'build=opus\nreview=opsu\n' > "$T/w-bad/.pipeline/models.conf"
check "C6b run.sh, no conf, no env: opus for both" [ "$(models_at "$T/w-none")" = "0|opus|opus" ]
check "C6c run.sh, review=sonnet: review sonnet, build opus" [ "$(models_at "$T/w-sonnet")" = "0|opus|sonnet" ]
check "C6d run.sh, AGENTLAB_REVIEW_MODEL beats the conf" \
  [ "$(models_at "$T/w-sonnet" AGENTLAB_REVIEW_MODEL=claude-opus-5-5)" = "0|opus|claude-opus-5-5" ]
check "C6e run.sh, AGENTLAB_BUILD_MODEL set but empty aborts" \
  [ "$(models_at "$T/w-none" AGENTLAB_BUILD_MODEL=)" = "1||" ]
check "C6f run.sh, a bad conf aborts the night (exit 1)" [ "$(models_at "$T/w-bad")" = "1||" ]
check "C6g ...and the log names the file, the line and the way out" \
  grep -qF ".pipeline/models.conf:2: invalid model 'opsu' for 'review'" "$T/w-bad/log"
check "C6h ...naming the env vars too" grep -qF "or AGENTLAB_BUILD_MODEL / AGENTLAB_REVIEW_MODEL if set. Aborting." "$T/w-bad/log"
check "C6h' an invalid override names its phase" \
  [ "$(models_at "$T/w-none" AGENTLAB_REVIEW_MODEL='claude opus')" = "1||" ]
check "C6h'' ...in the log" grep -qF "override 'claude opus' for phase 'review'" "$T/w-none/log"
check "C6i the resolved models are logged once" grep -qx "models: build=opus review=sonnet (fix rounds use the build model)" "$T/w-sonnet/log"

# The resolution must happen before the first phase can run.
block_ln="$(grep -n '^MODELS_CONF=' "$RUN_SH" | head -1 | cut -d: -f1)"
first_call_ln="$(grep -n '^[[:space:]]*\(if \)\{0,1\}run_phase ' "$RUN_SH" | grep -v 'run_phase ()' | head -1 | cut -d: -f1)"
before () { [ -n "$1" ] && [ -n "$2" ] && [ "$1" -lt "$2" ]; }
check "C6j the models are resolved (line $block_ln) above every run_phase call (first: $first_call_ln)" \
  before "$block_ln" "$first_call_ln"
# models.sh is sourced through run.sh's library loop, which aborts if it is missing.
check "C6k run.sh sources models.sh in its library loop" \
  grep -qE '^for lib in .* models( |;)' "$RUN_SH"

# Which variable each call site passes. The fix round is the builder again.
site () { grep -E "^[[:space:]]*run_phase [^ ]+ $1 \"cycle \\\$k/\\\$CYCLES: $2\"" "$RUN_SH" | awk '{print $2}'; }
check "C6l run.sh's build call site passes \$BUILD_MODEL"   [ "$(site build build)" = '"$BUILD_MODEL"' ]
check "C6m run.sh's review call site passes \$REVIEW_MODEL" [ "$(site review review)" = '"$REVIEW_MODEL"' ]
check "C6n run.sh's fix round passes \$BUILD_MODEL"         [ "$(site build fix)" = '"$BUILD_MODEL"' ]
ext_site () { grep -E "^[[:space:]]*run_phase [^ ]+ $1 " "$REPO/.pipeline/external_night.sh" | awk '{print $2}'; }
check "C6o external_night.sh's build and review pass the same variables" \
  [ "$(ext_site build)|$(ext_site review)" = '"$BUILD_MODEL"|"$REVIEW_MODEL"' ]

# run_phase itself, extracted verbatim, against a stand-in CLI that records its
# argv. Shows the phase header the log (and run_log.sh) reads, and that the
# default leaves the argv byte-identical to the literal `opus` it replaced.
. .pipeline/phase_tools.sh
. .pipeline/metrics.sh
. .pipeline/usage_limit.sh
. .pipeline/postcondition.sh
eval "$(sed -n '/^run_phase () {/,/^}/p' "$RUN_SH")"
PHASE_SETTINGS="$REPO/.pipeline/phase_settings.json"
METRICS_FILE="$T/metrics.jsonl"
TS="2026-11-27_020000"
cat > "$T/cli" <<'EOF'
#!/bin/bash
printf '%s\n' "$@" > "$CLI_ARGS_OUT"
echo '{"type":"result","is_error":false,"result":"done"}'
exit 0
EOF
chmod +x "$T/cli"
# phase <log> <args-out> <model> <key> <name> : one run_phase through the stand-in.
phase () {
  CLI_ARGS_OUT="$2" CLAUDE="$T/cli" LOG="$1" \
    run_phase "$3" "$4" "$5" phase_no_postcondition "prompt" >/dev/null 2>&1
}
check "C6p run_phase defined" declare -f run_phase >/dev/null

REVIEW_MODEL="$(cd "$T/w-sonnet" && resolve_phase_model review .pipeline/models.conf)"
BUILD_MODEL="$(cd "$T/w-sonnet" && resolve_phase_model build .pipeline/models.conf)"
: > "$T/rp.log"
phase "$T/rp.log" "$T/a-review" "$REVIEW_MODEL" review "cycle 1/1: review"
phase "$T/rp.log" "$T/a-build" "$BUILD_MODEL" build "cycle 1/1: build"
check "C6q review=sonnet: the review header says (model: sonnet)" \
  grep -qx -- "--- phase: cycle 1/1: review (model: sonnet) ---" "$T/rp.log"
check "C6r ...and build's says (model: opus)" \
  grep -qx -- "--- phase: cycle 1/1: build (model: opus) ---" "$T/rp.log"
check "C6s ...and the CLI got --model sonnet for review" \
  [ "$(grep -A1 -x -- '--model' "$T/a-review" | tail -1)" = "sonnet" ]
check "C6s' ...and --model opus for build" \
  [ "$(grep -A1 -x -- '--model' "$T/a-build" | tail -1)" = "opus" ]
. .pipeline/run_log.sh
check "C6t run_log.sh still parses the header (model: sonnet)" \
  [ "$(printf '%s\n' "--- phase: cycle 1/1: review (model: sonnet) ---" | sed -n "s|${RUN_LOG_PHASE_PATTERN}|\2|p")" = "sonnet" ]

default_model="$(resolve_phase_model review "$NO_CONF")"
phase "$T/d.log" "$T/a-default" "$default_model" review "cycle 1/1: review"
phase "$T/d.log" "$T/a-literal" opus review "cycle 1/1: review"
check "C6u with no conf, the review argv is byte-identical to the literal opus one" \
  cmp -s "$T/a-default" "$T/a-literal"
# cli_model <args-file> : the value the CLI received after --model.
cli_model () { grep -A1 -x -- '--model' "$1" 2>/dev/null | tail -1; }
phase "$T/f.log" "$T/a-fable" fable review "r"
phase "$T/f.log" "$T/a-full" claude-opus-5-5 review "r"
check "C6v run_phase now passes fable and a full name through to the CLI" \
  [ "$(cli_model "$T/a-fable")|$(cli_model "$T/a-full")" = "fable|claude-opus-5-5" ]
rm -f "$T/a-bad"
phase "$T/b.log" "$T/a-bad" "Opus" review "r"; bad_rc=$?
check "C6w ...and still refuses a bad model (rc 1) without calling the CLI" \
  [ "$bad_rc|$([ -e "$T/a-bad" ] && echo called || echo not-called)|$(grep -cF "run_phase: bad model 'Opus'" "$T/b.log")" = "1|not-called|1" ]

# --- criterion 9: PIPELINE.md says how to set the model, and that opus for
# both is still the default. The paragraph is read as one line (from its bold
# lead-in to the next blank line) so a re-wrap does not break the checks.
models_doc="$(sed -n '/^\*\*Setting the build and review models\.\*\*/,/^$/p' PIPELINE.md | tr '\n' ' ')"
check "C9a PIPELINE.md has the 'Setting the build and review models' paragraph" \
  [ -n "$models_doc" ]
check "C9b ...which says to edit .pipeline/models.conf with build= and review=" \
  grep -qF -- 'Edit `.pipeline/models.conf` (`build=<model>`, `review=<model>`)' <<<"$models_doc"
check "C9c ...names both one-run overrides" \
  [ "$(grep -oE 'AGENTLAB_(BUILD|REVIEW)_MODEL' <<<"$models_doc" | sort -u | tr '\n' ' ')" = "AGENTLAB_BUILD_MODEL AGENTLAB_REVIEW_MODEL " ]
check "C9d ...and says the default is still opus for both" \
  grep -qF -- 'The default is still **opus for both**.' <<<"$models_doc"
check "C9e the shipped models.conf matches that default (build and review are opus)" \
  [ "$(resolve_phase_model build .pipeline/models.conf)|$(resolve_phase_model review .pipeline/models.conf)" = "opus|opus" ]

echo ""
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
