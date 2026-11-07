#!/bin/bash
# deps.sh — builds the runner clone's local dependencies when they are missing.
#
# Sourced by run.sh and setup_runner.sh. Unlike the libs in run.sh's lib loop
# this one is an edge, not pure: it creates a venv and runs pip. It is
# idempotent — an existing venv is left alone, so an ordinary night costs one
# file test.
#
# Why it exists: .mcp.json starts the hn-search MCP server from $HN_VENV, and
# run.sh's nightly `git clean -fdx` used to delete that ignored directory every
# night, so the researcher ran without hn-search (its own logs say so from
# 2026-11-02 on). The scrub now spares the venv; this rebuilds it when it is
# absent — a fresh runner clone, or a human removing it.

# The venv .mcp.json's hn-search server runs from, relative to the repo root.
HN_VENV="examples/mcp-hn-search/.venv"
HN_REQUIREMENTS="examples/mcp-hn-search/requirements.txt"

# bootstrap_runner_deps <log_file>
# Run from the repo root. When $HN_VENV/bin/python3 exists, does nothing and
# prints nothing. Otherwise creates the venv with the python3 on PATH, installs
# $HN_REQUIREMENTS into it, and prints one line saying so.
# Returns 0 when the venv was already there or was built. Returns 1 when the
# build failed (no python3, pip or network failure); the half-built venv is
# removed so the next run retries from scratch instead of finding a python3
# with no packages. pip's own output goes to <log_file>, never stdout.
bootstrap_runner_deps () {
  local log="$1"
  [ -x "$HN_VENV/bin/python3" ] && return 0
  rm -rf "$HN_VENV"
  if python3 -m venv "$HN_VENV" >>"$log" 2>&1 \
     && "$HN_VENV/bin/python3" -m pip install --quiet -r "$HN_REQUIREMENTS" >>"$log" 2>&1; then
    echo "deps: built $HN_VENV from $HN_REQUIREMENTS"
    return 0
  fi
  rm -rf "$HN_VENV"
  return 1
}
