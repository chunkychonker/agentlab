"""``python -m nightloop``: the same command as the ``nightloop`` console script."""

from __future__ import annotations

from .cli.main import main

raise SystemExit(main())
