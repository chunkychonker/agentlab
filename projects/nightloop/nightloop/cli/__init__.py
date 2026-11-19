"""The ``nightloop`` command line: ``run``, ``run --dry-run`` and ``report``.

Public API::

    from nightloop.cli import main, ExitCode

``python -m nightloop`` and the ``nightloop`` console script both call
``main``. The pure parts (``window``, ``plan``, ``summary``,
``night_text``) are importable on their own and do no I/O.
"""

from __future__ import annotations

from .main import ExitCode, main

__all__ = ["ExitCode", "main"]
