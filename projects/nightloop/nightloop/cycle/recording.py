"""A ``Runner`` decorator that remembers the last ``RawRun`` it passed through.

Pure: no I/O. ``PhaseOutcome`` drops stdout and stderr, but the usage-limit
check needs them (a non-zero exit with non-JSON stdout leaves no
``CliResult``). Wrapping the runner keeps ``nightloop.phase`` unchanged.
"""

from __future__ import annotations

from ..phase.request import PhaseRequest
from ..phase.runner import RawRun, Runner


class RecordingRunner:
    """Delegates to ``inner`` and keeps the result of the most recent call in ``last``.

    ``last`` is ``None`` until a call returns. Whatever ``inner.run`` raises
    propagates, and ``last`` keeps its previous value.
    """

    def __init__(self, inner: Runner) -> None:
        self._inner = inner
        self.last: RawRun | None = None

    def run(self, request: PhaseRequest) -> RawRun:
        raw = self._inner.run(request)
        self.last = raw
        return raw
