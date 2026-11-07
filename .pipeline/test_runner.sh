#!/bin/bash
# Offline tests for .pipeline/runner.sh and .pipeline/setup_runner.sh.
# Run: bash .pipeline/test_runner.sh
#
# setup_runner.sh is driven end to end against a throwaway origin and runner
# with --no-launchd, so no case touches the network, the real launchd job, or
# ~/agentlab-runner.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
REPO="$(pwd)"
# shellcheck source=runner.sh
. .pipeline/runner.sh

PASS=0; FAIL=0
ok ()  { PASS=$((PASS+1)); echo "ok   $1"; }
bad () { FAIL=$((FAIL+1)); echo "FAIL $1"; }
check () { local name="$1"; shift; if "$@"; then ok "$name"; else bad "$name"; fi; }

T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT

# --- render_runner_plist ---

R="/Users/steeb/agentlab-runner"
render_runner_plist "$R" > "$T/job.plist"
check "P1 renders for an absolute path" [ $? -eq 0 ]
check "P2 the plist lints" plutil -lint -s "$T/job.plist"
check "P3 runs the runner's run.sh" \
  [ "$(plutil -extract ProgramArguments.1 raw "$T/job.plist")" == "$R/.pipeline/run.sh" ]
check "P4 works in the runner" \
  [ "$(plutil -extract WorkingDirectory raw "$T/job.plist")" == "$R" ]
check "P5 logs into the runner" \
  [ "$(plutil -extract StandardOutPath raw "$T/job.plist")" == "$R/logs/launchd.out.log" ]
check "P6 keeps the label, so it replaces the old job instead of adding one" \
  [ "$(plutil -extract Label raw "$T/job.plist")" == "com.steeb.agentlab.daily" ]
check "P7 keeps the 02:00 slot" \
  [ "$(plutil -extract StartCalendarInterval.Hour raw "$T/job.plist")$(plutil -extract StartCalendarInterval.Minute raw "$T/job.plist")" == "20" ]
check "P8 does not run at load (loading it must not start a night)" \
  [ "$(plutil -extract RunAtLoad raw "$T/job.plist")" == "false" ]
if grep -q '/agentlab/' "$T/job.plist"; then bad "P9 names no path in the human's clone"; else ok "P9 names no path in the human's clone"; fi
for d in "" "agentlab-runner" "/x/" "/x&y" "/x<y" '/x"y'; do
  out="$(render_runner_plist "$d")"; rc=$?
  check "P10 rejects '$d' with rc 2 and no output" [ "$rc" -eq 2 ] && [ -z "$out" ]
done

# --- setup_runner.sh: argument handling ---

bash .pipeline/setup_runner.sh --bogus >/dev/null 2>&1
check "U1 an unknown flag is a usage error" [ $? -eq 2 ]
AGENTLAB_RUNNER_DIR="$REPO" bash .pipeline/setup_runner.sh --dry-run --no-launchd >/dev/null 2>&1
check "U2 the runner cannot be the clone the script runs from" [ $? -eq 2 ]
AGENTLAB_RUNNER_DIR="relative/dir" bash .pipeline/setup_runner.sh --dry-run --no-launchd >/dev/null 2>&1
check "U3 a relative runner path is a usage error" [ $? -eq 2 ]

# --- setup_runner.sh end to end, offline ---
# A throwaway "human clone" (SRC) whose origin is a local bare repo. The origin
# tracks a stand-in venv python, so the deps step finds it and never runs pip.

g () { git -c user.name=t -c user.email=t@example.invalid "$@"; }
mkdir -p "$T/seed/.pipeline" "$T/seed/examples/mcp-hn-search/.venv/bin"
cp .pipeline/setup_runner.sh .pipeline/runner.sh .pipeline/deps.sh "$T/seed/.pipeline/"
printf '#!/bin/sh\n' > "$T/seed/examples/mcp-hn-search/.venv/bin/python3"
chmod +x "$T/seed/examples/mcp-hn-search/.venv/bin/python3"
( cd "$T/seed" && git init -q && g add -A && g commit -qm seed ) >/dev/null
git clone -q --bare "$T/seed" "$T/origin.git"
git clone -q "$T/origin.git" "$T/src"
git -C "$T/src" config --local user.name "Steve Ling"
git -C "$T/src" config --local user.email "steveylingy@gmail.com"
mkdir -p "$T/src/logs"
echo "=== done ===" > "$T/src/logs/run-2026-11-01_020001.log"
echo "health" > "$T/src/logs/lab-health-2026-11-01_020001.log"
touch -t 202611010200 "$T/src/logs/run-2026-11-01_020001.log" "$T/src/logs/lab-health-2026-11-01_020001.log"
setup () { AGENTLAB_RUNNER_DIR="$T/runner" bash "$T/src/.pipeline/setup_runner.sh" "$@"; }

setup --dry-run --no-launchd > "$T/dry.out" 2>&1
check "E1 a dry run succeeds" [ $? -eq 0 ]
check "E2 a dry run creates nothing" [ ! -e "$T/runner" ]
check "E3 a dry run says what it would do" grep -q '^would   clone' "$T/dry.out"

setup --no-launchd > "$T/first.out" 2>&1
check "E4 the first run succeeds" [ $? -eq 0 ]
check "E5 the runner is a clone of the same origin" \
  [ "$(git -C "$T/runner" remote get-url origin)" == "$T/origin.git" ]
check "E6 the runner commits as the human clone's local identity" \
  [ "$(git -C "$T/runner" config --local user.email)" == "steveylingy@gmail.com" ]
check "E7 run history is carried over" [ -f "$T/runner/logs/run-2026-11-01_020001.log" ]
check "E8 log mtimes are kept, so the cadence checks see the real dates" \
  [ "$(stat -f %m "$T/runner/logs/lab-health-2026-11-01_020001.log")" == \
    "$(stat -f %m "$T/src/logs/lab-health-2026-11-01_020001.log")" ]

setup --no-launchd > "$T/second.out" 2>&1
check "E9 a second run succeeds" [ $? -eq 0 ]
check "E10 a second run changes nothing and says so" grep -q '^Nothing to do' "$T/second.out"

echo "=== a night of its own ===" > "$T/runner/logs/run-2026-11-08_020001.log"
rm "$T/runner/logs/run-2026-11-01_020001.log"
setup --no-launchd >/dev/null 2>&1
check "E11 logs are copied once: a runner with its own history is left alone" \
  [ ! -e "$T/runner/logs/run-2026-11-01_020001.log" ]

mkdir -p "$T/notaclone" && touch "$T/notaclone/file"
AGENTLAB_RUNNER_DIR="$T/notaclone" bash "$T/src/.pipeline/setup_runner.sh" --no-launchd >/dev/null 2>&1
check "E12 a non-empty directory that is not a clone is refused" [ $? -eq 1 ]
check "E13 ...and left untouched" [ "$(ls -A "$T/notaclone")" == "file" ]

git clone -q "$T/origin.git" "$T/other" && git -C "$T/other" remote set-url origin https://example.invalid/other.git
AGENTLAB_RUNNER_DIR="$T/other" bash "$T/src/.pipeline/setup_runner.sh" --no-launchd >/dev/null 2>&1
check "E14 a clone of a different origin is refused" [ $? -eq 1 ]

echo ""
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
