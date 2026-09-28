"""Defines the canary experiment: its fixed inputs plus the precondition that keeps it honest.

Pure: constants plus one checker. No filesystem, no network.

The experiment only proves something if the canary can reach Claude's reply
through exactly one route: Claude reading the Skill body out of the
container. So the canary must be in the SKILL.md body, and must NOT be in:

- the frontmatter `name` / `description` (the API puts those in the system
  prompt up front, "Level 1" metadata, so no file read would be needed), or
- the user prompt (Claude could just echo it).
"""

from __future__ import annotations

from frontmatter import SkillMd

# Arbitrary codeword that is not derivable from general knowledge. Must match
# the one written into skills/hello-skill/SKILL.md; check_experiment_design
# enforces that before any upload.
CANARY = "VERMILION-OTTER-4471"

SKILL_DIR_NAME = "hello-skill"
SKILL_MD_FILENAME = "SKILL.md"

# Should trigger the Skill by matching its description, without naming the
# canary or the file.
PROMPT = "Please write a house-style greeting for Ada."


class ExperimentDesignError(ValueError):
    """The canary could reach the reply by a route other than reading the Skill body."""


def check_experiment_design(skill: SkillMd, prompt: str, canary: str) -> None:
    """Assert the canary appears only in the Skill body.

    Raises:
        ExperimentDesignError: the canary is empty; missing from the body; or
            present in the name, description, or prompt. Any of these would
            make a "loaded" verdict meaningless.
    """
    if not canary:
        raise ExperimentDesignError("canary must be non-empty")
    if canary not in skill.body:
        raise ExperimentDesignError("canary is not in the SKILL.md body")
    for label, text in (("name", skill.name), ("description", skill.description), ("prompt", prompt)):
        if canary in text:
            raise ExperimentDesignError(f"canary leaks into the {label}; the read would be unprovable")
