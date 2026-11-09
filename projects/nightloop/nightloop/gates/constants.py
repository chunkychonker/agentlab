"""Where agentlab's phases leave their artifacts, relative to the repo root.

Named once, mirroring ``.pipeline/postcondition.sh``, so a path change is a
diff here rather than a gate that silently passes somewhere else. These are
defaults for ``dispatch.evaluate``, not config, in this milestone.
"""

from __future__ import annotations

# The research phase writes one dated note here per cycle.
RESEARCH_DIR = "research"
RESEARCH_GLOB = "*.md"

# The build phase writes under examples/ (demo mode) or projects/ (project
# mode); only one is live per night, so both are swept.
INCREMENT_ROOTS: tuple[str, ...] = ("examples", "projects")

# Any file counts: the builder must produce something runnable, not a named file.
INCREMENT_GLOB = "*"
