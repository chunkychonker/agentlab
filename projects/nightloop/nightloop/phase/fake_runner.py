"""A scripted ``Runner`` for tests: never starts a process, never calls a model.

Pure apart from what a scripted step chooses to do (a step may write files
to a temp repo to stand in for the phase's artifact).
"""

from __future__ import annotations

import json
from typing import Callable, Sequence, Union

from .request import PhaseRequest
from .runner import RawRun

# A step is either the RawRun to return, or a function that does the phase's
# work (e.g. writes a note) and then returns one.
Step = Union[RawRun, Callable[[PhaseRequest], RawRun]]


class FakeRunnerExhausted(RuntimeError):
    """``FakeRunner.run`` was called more times than it had steps."""


class FakeRunner:
    """Returns its scripted steps in order and records every request it was given.

    ``requests`` lists the requests received, oldest first. Raises
    ``FakeRunnerExhausted`` from ``run`` once the steps are used up, and
    ``TypeError`` if a step returns something other than a ``RawRun``.
    """

    def __init__(self, steps: Sequence[Step]) -> None:
        self._steps = list(steps)
        self.requests: list[PhaseRequest] = []

    def run(self, request: PhaseRequest) -> RawRun:
        if len(self.requests) >= len(self._steps):
            raise FakeRunnerExhausted(
                f"no step left for call {len(self.requests) + 1} ({request.name!r}); "
                f"scripted {len(self._steps)}"
            )
        step = self._steps[len(self.requests)]
        self.requests.append(request)
        raw = step(request) if callable(step) else step
        if not isinstance(raw, RawRun):
            raise TypeError(f"a FakeRunner step must give a RawRun, got {type(raw).__name__}")
        return raw


def cli_stdout(result: str = "done", *, is_error: bool = False, **fields: object) -> str:
    """Return a CLI-shaped JSON stdout with ``result``, ``is_error`` and any extra ``fields``.

    Raises ``TypeError`` if a field value is not JSON-serialisable.
    """
    return json.dumps({"type": "result", "result": result, "is_error": is_error, **fields})


def cli_success(result: str = "done", **fields: object) -> RawRun:
    """A ``RawRun`` for a phase that exited 0 and reported success."""
    return RawRun(returncode=0, stdout=cli_stdout(result, **fields), stderr="", timed_out=False)
