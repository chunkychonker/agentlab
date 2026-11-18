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
