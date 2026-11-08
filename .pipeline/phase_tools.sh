#!/bin/bash
# phase_tools.sh — the tool permissions each nightly phase runs with.
#
# Sourced by run.sh. Pure: prints arguments, reads and writes nothing else,
# calls no model. Covered offline by test_phase_tools.sh.
#
# Why it exists: until 2026-11-08 every phase ran under
# --permission-mode bypassPermissions, so the researcher — the one phase that
# reads the open web — could run `git push` or `gh pr create` if a fetched page
# told it to. Now every phase runs under dontAsk, where any tool call not on
# its allow list is denied without a prompt, and only the maintainer may
# publish.
#
# What this is NOT: a security boundary for phases that have Bash. A deny rule
# matches a command's text, and `python3 -c` or a script file gets around any
# text match. The real protection is that the researcher has no Bash at all.
# .claude/hooks/publish_guard.py is a second, independent check on publishing.
#
# Two facts shape the lists below, both verified live on 2026-11-08:
#   - The runner clone is an untrusted workspace, so the project's
#     .claude/settings.json allow list is ignored there. Each list here must
#     be complete on its own.
#   - The RTK hook in ~/.claude/settings.json rewrites `git push` to
#     `rtk git push` BEFORE permissions are checked, so a deny on the plain
#     form alone does nothing. deny_forms below denies both forms.

# Reads one command per line on stdin and prints the four spellings of a
# deny rule for each: plain and rtk-prefixed, in both the space and the legacy
# colon rule syntax.
_phase_deny_forms () {
  local cmd
  while IFS= read -r cmd; do
    [ -n "$cmd" ] || continue
    printf 'Bash(%s *)\nBash(%s:*)\nBash(rtk %s *)\nBash(rtk %s:*)\n' \
      "$cmd" "$cmd" "$cmd" "$cmd"
  done
}

# Commands only the maintainer may run, one per line. `gh` is denied whole:
# no phase but maintain needs to write to GitHub, and the read-only calls the
# agents used to make now come from logs/research-context.md.
_PHASE_PUBLISH_CMDS="git commit
git push
gh"

# gh subcommands that write to GitHub. The health and observer phases may read
# GitHub (`gh pr view`, `gh pr list`) but not these.
_PHASE_GH_WRITE_CMDS="gh pr create
gh pr merge
gh pr edit
gh pr comment
gh pr close
gh pr reopen
gh pr review
gh issue create
gh issue edit
gh issue comment
gh issue close
gh api
gh release
gh repo create
gh repo edit
gh repo delete
gh workflow run
gh secret
gh variable
gh label"

# Edit and Write rules for each path, so either file tool works on it.
_phase_file_rules () {
  local p
  for p in "$@"; do
    printf 'Edit(%s)\nWrite(%s)\n' "$p" "$p"
  done
}

# phase_tool_args <phase-key>
#   Prints the CLI arguments for one phase, one argument per line, for run.sh
#   to read into an array. Every phase gets --permission-mode dontAsk.
#   Keys: research build review maintain replenish health observe.
#   rc 1 and no output on any other key, so a typo fails the phase instead of
#   running it with no restrictions.
phase_tool_args () {
  local key="$1"
  case "$key" in
    research)
      printf '%s\n' --permission-mode dontAsk --agent agentlab-researcher \
        --tools "Read,Glob,Grep,Edit,Write,WebSearch,WebFetch,mcp__hn-search__search_stories,mcp__hn-search__get_story" \
        --allowedTools Read Glob Grep WebSearch WebFetch \
        mcp__hn-search__search_stories mcp__hn-search__get_story
      _phase_file_rules "research/**" "knowledge/**" "BACKLOG.md" "projects/*/PLAN.md"
      printf '%s\n' --disallowedTools Bash
      ;;
    build)
      printf '%s\n' --permission-mode dontAsk --agent agentlab-builder \
        --allowedTools Read Glob Grep Edit Write Bash \
        --disallowedTools
      printf '%s\n' "$_PHASE_PUBLISH_CMDS" | _phase_deny_forms
      ;;
    review)
      printf '%s\n' --permission-mode dontAsk --agent agentlab-reviewer \
        --allowedTools Read Glob Grep Bash
      _phase_file_rules "logs/last-review.md"
      printf '%s\n' --disallowedTools
      printf '%s\n' "$_PHASE_PUBLISH_CMDS" | _phase_deny_forms
      ;;
    maintain)
      printf '%s\n' --permission-mode dontAsk --agent agentlab-maintainer \
        --tools "Read,Glob,Grep,Bash,Edit,Write" \
        --allowedTools Read Glob Grep \
        "Bash(git status)" "Bash(git status *)" "Bash(git diff)" "Bash(git diff *)" \
        "Bash(git log)" "Bash(git log *)" "Bash(git add *)" "Bash(git commit *)" \
        "Bash(git push *)" "Bash(git switch *)" "Bash(git branch)" "Bash(git branch *)" \
        "Bash(git rev-parse *)" "Bash(git remote -v)" "Bash(git show *)" \
        "Bash(git config user.name)" "Bash(git config user.email)" "Bash(git config --get *)" \
        "Bash(git -C *)" \
        "Bash(rtk git *)" \
        "Bash(gh pr create *)" "Bash(gh pr view *)" "Bash(gh pr list *)" \
        "Bash(echo *)" "Bash(pwd)" "Bash(cd *)"
      _phase_file_rules "projects/*/PLAN.md" "logs/last-pr.txt" "logs/pr-body.md"
      printf '%s\n' --disallowedTools WebSearch WebFetch
      printf '%s\n' "gh pr merge" "git push --force" "git push -f" | _phase_deny_forms
      ;;
    replenish)
      printf '%s\n' --permission-mode dontAsk \
        --tools "Read,Glob,Grep,Edit,Write" \
        --allowedTools Read Glob Grep
      _phase_file_rules "BACKLOG.md"
      printf '%s\n' --disallowedTools Bash WebSearch WebFetch
      ;;
    health|observe)
      if [ "$key" = health ]; then
        printf '%s\n' --permission-mode dontAsk --agent agentlab-health \
          --allowedTools Read Glob Grep Bash
        _phase_file_rules "logs/lab-health-*" "logs/last-health.md"
      else
        printf '%s\n' --permission-mode dontAsk --agent agentlab-pipeline-observer \
          --allowedTools Read Glob Grep Bash
        _phase_file_rules "logs/lab-pipeline-*" "logs/last-pipeline-health.md"
      fi
      printf '%s\n' --disallowedTools
      printf '%s\n' "git commit" "git push" "$_PHASE_GH_WRITE_CMDS" | _phase_deny_forms
      ;;
    *)
      return 1 ;;
  esac
}
