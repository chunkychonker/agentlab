Placeholder subdirectory. It exists only so `test_agent.py` has a real,
on-disk directory (not a file) inside `reference/` to exercise
`read_reference()`'s "resolved path is a directory, not a file" failure
mode without reaching outside the reference directory to do so. It is not
part of the skill's actual content and SKILL.md never links to it.
