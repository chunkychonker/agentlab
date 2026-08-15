#!/usr/bin/env bash
# Test-only side effect for examples/skill-permission-suppression.
#
# Writes one line to the path given as $1 and nothing else. The point is a
# real, on-disk fact that does not depend on anything the model *says* it
# did: either this script ran (file exists) or it did not (file absent).
#
# Failure modes: exits non-zero with a message on stderr if $1 is missing,
# or if the path is not writable (set -euo pipefail; no fallback path, no
# silent success).
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "usage: mark.sh <sentinel-path>" >&2
  exit 2
fi

printf 'ran at %s\n' "$(date -u +%FT%TZ)" > "$1"
