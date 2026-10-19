#!/bin/bash
# TardyAI platform-parity gate: does this checkout build and hold together the
# way C3 AI Studio will build and serve it?
#
# Usage: bash projects/tardyai/verify.sh <tardyai-checkout>
#
# Every step runs even after one fails, so a single report lists every problem.
# Exit 0 only if all steps pass; exit 1 if any fails; exit 2 on bad usage.
#
# Mirrors the Studio path as closely as is possible offline:
#   - `npm ci`, not `npm install`: a clean deploy installs from the lock file and
#     refuses drift, while `npm install` silently repairs it and hides the bug.
#   - VITE_C3_PKG set and the bundle landing in ui/content/<pkg>/, the folder
#     Studio serves the preview from.
# What it cannot check: anything needing a live C3 tenant (upsertSeedData,
# c3Action calls, the Desktop/Mobile preview). Those stay on the PR checklist.

set -uo pipefail

PKG="pkgbc231600"
HERE="$(cd "$(dirname "$0")" && pwd)"

if [ "$#" -ne 1 ] || [ ! -d "$1/$PKG" ]; then
  echo "usage: verify.sh <tardyai-checkout>  (must contain $PKG/)" >&2
  exit 2
fi
ROOT="$(cd "$1" && pwd)"
UI="$ROOT/$PKG/ui/react"

FAILED=()
step () {
  local name="$1"; shift
  echo ""
  echo "=== verify: $name ==="
  if "$@"; then
    echo "--- PASS: $name"
  else
    echo "--- FAIL: $name"
    FAILED+=("$name")
  fi
}

package_name_unchanged () {
  python3 -I -c 'import json,sys; n=json.load(open(sys.argv[1]))["name"]; sys.exit(0 if n==sys.argv[2] else print(f"package name is {n!r}, must stay {sys.argv[2]!r}") or 1)' \
    "$ROOT/$PKG/$PKG.c3pkg.json" "$PKG"
}
npm_ci ()     { (cd "$UI" && npm ci --no-audit --no-fund); }
ui_build ()   { (cd "$UI" && VITE_C3_PKG="$PKG" npm run build); }
bundle_served () {
  [ -s "$ROOT/$PKG/ui/content/$PKG/index.html" ] \
    || { echo "no $PKG/ui/content/$PKG/index.html — Studio would serve an empty preview"; return 1; }
}
ui_tests ()   { (cd "$UI" && CI=1 npm test -- --watchAll=false); }
node_tests () {
  local dir="$ROOT/$PKG/test/node"
  if [ ! -d "$dir" ]; then echo "(no $PKG/test/node yet — skipped)"; return 0; fi
  node --test "$dir"
}

step "package name unchanged"        package_name_unchanged
step "c3typ methods implemented"     python3 -I "$HERE/checks/c3_parity.py" "$ROOT/$PKG/src"
step "seed data integrity"           python3 -I "$HERE/checks/seed_integrity.py" "$ROOT/$PKG"
step "npm ci (lock file in sync)"    npm_ci
step "ui build (lint + tsc + vite)"  ui_build
step "bundle at ui/content/$PKG"     bundle_served
step "ui tests (jest)"               ui_tests
step "router tests (node --test)"    node_tests

echo ""
if [ "${#FAILED[@]}" -eq 0 ]; then
  echo "VERIFY: PASS"
  exit 0
fi
echo "VERIFY: FAIL (${#FAILED[@]}): ${FAILED[*]}"
exit 1
