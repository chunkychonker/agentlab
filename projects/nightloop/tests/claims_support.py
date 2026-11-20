"""In-memory stand-ins for the two claims ports (not a test module).

``ScriptedSource`` returns the lookups a test hands it; ``MemoryBacklog``
holds the backlog text and counts writes, so a test can assert that a
no-op run wrote nothing. Neither decides anything: every decision under
test is made by the real reconcilers and pure edits.
"""

from __future__ import annotations

from typing import Sequence

from nightloop.claims import ClaimLookup, PrNumber, UnshippedBranch


class ScriptedSource:
    def __init__(self, branches: Sequence[UnshippedBranch] = (), merged: dict[int, ClaimLookup] | None = None) -> None:
        self._branches = list(branches)
        self._merged = dict(merged or {})

    def unshipped_claims(self) -> list[UnshippedBranch]:
        return list(self._branches)

    def merged_claim(self, pr: PrNumber) -> ClaimLookup:
        return self._merged[pr.value]


class MemoryBacklog:
    def __init__(self, text: str) -> None:
        self.text = text
        self.reads = 0
        self.writes = 0

    def read(self) -> str:
        self.reads += 1
        return self.text

    def write(self, text: str) -> None:
        self.writes += 1
        self.text = text


class RecordingPublisher:
    """A ``BacklogPublisher`` whose answers a test scripts, logging each call to ``events``.

    ``not_ready`` is what every ``ready()`` returns (``None`` means ready);
    ``failure`` is what every ``publish`` returns (``None`` means pushed).
    It decides nothing: the tests assert on what the real steps chose to
    call, in what order, and with which message.
    """

    def __init__(self, events: list[str] | None = None, *, not_ready=None, failure=None) -> None:
        self.events = [] if events is None else events
        self.messages: list[str] = []
        self._not_ready = not_ready
        self._failure = failure

    def ready(self):
        self.events.append("ready")
        return self._not_ready

    def publish(self, message: str):
        self.events.append("publish")
        self.messages.append(message)
        return self._failure
