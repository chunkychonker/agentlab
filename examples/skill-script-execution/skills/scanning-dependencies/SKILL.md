---
name: scanning-dependencies
description: Scans a project's requirements.txt and package.json for unpinned dependency versions. Use when the user asks to check, audit, or scan dependency pinning, or asks whether dependencies are pinned/locked before a build.
allowed-tools: Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/scan_dependencies.py *)
---

## Instructions

1. Run the bundled scanner against the directory the user cares about
   (default to `.` if they don't name one):

   ```
   python3 ${CLAUDE_SKILL_DIR}/scripts/scan_dependencies.py <directory>
   ```

2. The script prints exactly one JSON object to stdout:
   `{"scanned": [...], "findings": [{"file", "package", "version_spec",
   "reason"}], "count": N}` — or, only if the given directory does not
   exist, `{"error": "..."}` with a non-zero exit code.

3. Do not re-implement the scanning logic yourself — parsing pin rules for
   `requirements.txt` and `package.json` is the script's job, not something
   to redo by reading the manifests directly. Your job is to run it and
   narrate the JSON it returns.

4. Summarize the findings to the user:
   - If `count` is 0, say plainly that no unpinned dependencies were found
     (and note if `scanned` was also empty — that means no manifest files
     exist under the directory, which is worth mentioning).
   - Otherwise, group findings by `file`, and for each one state the
     `package`, its `version_spec`, and the `reason` it was flagged.
   - If the directory does not exist, report the `error` message plainly —
     do not guess at what the user meant.
