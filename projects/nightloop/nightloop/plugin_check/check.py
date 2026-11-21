"""The checker's entry point: read a plugin directory, then apply the rules."""

from __future__ import annotations

from pathlib import Path

from .problem import Problem
from .rules import check_tree
from .tree import read_tree


def check_plugin(root: Path) -> list[Problem]:
    """Every problem in the plugin at ``root``; ``[]`` means it passes.

    Reads the directory once (``tree.read_tree``), then decides purely
    (``rules.check_tree``). Raises ``NotADirectoryError`` if ``root`` is not
    a directory and propagates any other ``OSError`` from reading it.
    Defects in the plugin are returned, never raised.
    """
    return check_tree(read_tree(root))
