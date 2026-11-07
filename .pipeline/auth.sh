#!/bin/bash
# auth.sh — decides how the night logs in to Claude, and recognises a phase
# that died because it could not.
#
# Sourced by run.sh. Pure: reads one file it is handed, writes nothing, calls
# no model, touches no Keychain. run.sh does the Keychain read and the probe
# call; this file only judges their results. Covered offline by
# test_auth_alert.sh.
#
# Why it exists: on 2026-10-30, 10-31 and 11-01 every phase died with "Failed
# to authenticate: OAuth session expired and could not be refreshed". The
# headless run had been borrowing the interactive login, nothing checked it
# before the first phase, and the nights logged as ordinary partial runs
# (shipped 0/1) that nobody saw until a human read the logs. A long-lived
# token from `claude setup-token`, kept in the login Keychain, removes the
# dependency on the interactive session; the probe makes a dead login one
# clear abort instead of a night of failed phases.

# The login Keychain item run.sh reads the token from. Created once, by hand:
#   claude setup-token
#   security add-generic-password -a "$USER" -s agentlab-claude-oauth -w
AUTH_KEYCHAIN_SERVICE="agentlab-claude-oauth"

# `security find-generic-password` exit code for "no such item"
# (errSecItemNotFound). Any other non-zero code means the item may exist but
# could not be read — a locked Keychain, a denied ACL.
AUTH_ITEM_NOT_FOUND_RC=44

# Extended regex, matched case-insensitively against the probe's own output.
# The first alternative is the CLI's prefix on both auth failures seen in run
# logs ("Failed to authenticate: OAuth session expired and could not be
# refreshed", 2026-10-30..11-01; "Failed to authenticate. API Error: 403",
# 2026-08-01/04). The others cover the CLI's wording for a bad or expired
# token handed in through the environment.
AUTH_FAILURE_RE="failed to authenticate|invalid api key|oauth token has expired"

# auth_token_disposition <security_rc> <token_length>
# What to do with the result of the Keychain read. Prints exactly one of:
#   USE       rc 0 and a non-empty token — export it for every claude call
#   FALLBACK  the item does not exist — no long-lived token has been set up
#             yet, so keep using the interactive login (the probe still runs)
#   ABORT     the read failed for any other reason, or returned an empty
#             token. A token that exists but cannot be read is not a licence
#             to fall back silently: that is how a locked Keychain would turn
#             back into the 10-30 outage.
# Returns 0 for USE and FALLBACK, 1 for ABORT, 2 (printing nothing) when
# either argument is not a non-negative integer.
auth_token_disposition () {
  local rc="${1:-}" len="${2:-}"
  case "$rc" in ''|*[!0-9]*) return 2 ;; esac
  case "$len" in ''|*[!0-9]*) return 2 ;; esac
  if [ "$rc" -eq 0 ] && [ "$len" -gt 0 ]; then
    echo "USE"; return 0
  fi
  if [ "$rc" -eq "$AUTH_ITEM_NOT_FOUND_RC" ]; then
    echo "FALLBACK"; return 0
  fi
  echo "ABORT"; return 1
}

# auth_failure_reason <probe_output_file>
# Returns 0 and prints the last matching line (trimmed) if the file shows an
# authentication failure. Returns 1, printing nothing, if there is no such
# line or the file is missing or unreadable.
auth_failure_reason () {
  local file="$1" line
  [ -r "$file" ] || return 1
  line="$(grep -aiE -- "$AUTH_FAILURE_RE" "$file" 2>/dev/null | tail -1)"
  [ -n "$line" ] || return 1
  printf '%s\n' "$line" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//'
  return 0
}
