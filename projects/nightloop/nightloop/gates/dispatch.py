"""Maps a ``Postcondition`` onto the gate check that implements it.

The single entry point the phase runner calls after a phase exits. It never
reads the clock: the caller passes the phase's start time.
"""

from __future__ import annotations

from pathlib import Path

from ..model import Postcondition
from .constants import INCREMENT_GLOB, INCREMENT_ROOTS, RESEARCH_DIR, RESEARCH_GLOB
from .freshness import validate_floor
from .fs import check_freshness, check_increment, check_verdict
from .outcome import GateResult, Ungated


def evaluate(
    post: Postcondition, repo: Path, phase_started: int, verdict_path: Path
) -> GateResult:
    """Run the gate for ``post`` against the repo at ``repo``.

    ``phase_started`` is the epoch second the phase began (the freshness
    floor). ``verdict_path`` is the review verdict file; a relative path is
    resolved against ``repo``. Each gate reads only what it needs:
    ``REVIEW_VERDICT`` ignores ``phase_started`` beyond validating it, the
    freshness gates ignore ``verdict_path``, and ``NONE`` touches nothing.

    Never raises for filesystem or content problems; those are fail-closed
    outcomes. Raises ``ValueError`` if ``phase_started`` is not a
    non-negative int, for every ``post`` including ``NONE``, so a caller bug
    surfaces on the first call rather than only on nights a freshness gate
    runs. Raises ``TypeError`` if ``post`` is not a ``Postcondition``.
    """
    if not isinstance(post, Postcondition):
        raise TypeError(f"post must be a Postcondition, got {type(post).__name__}")
    validate_floor(phase_started)

    if post is Postcondition.NONE:
        return GateResult(post, Ungated.NONE, "gate NONE: no postcondition for this phase")

    if post is Postcondition.REVIEW_VERDICT:
        path = repo / verdict_path
        verdict = check_verdict(path)
        return GateResult(post, verdict, f"review verdict {verdict.name} in {str(path)!r}")

    if post is Postcondition.RESEARCH_NOTE_FRESH:
        root = repo / RESEARCH_DIR
        state = check_freshness(root, RESEARCH_GLOB, phase_started)
        return GateResult(
            post,
            state,
            f"research note {state.name}: {RESEARCH_GLOB!r} under {str(root)!r}, "
            f"floor {phase_started}",
        )

    if post is Postcondition.INCREMENT_BUILT:
        roots = [repo / r for r in INCREMENT_ROOTS]
        state = check_increment(roots, INCREMENT_GLOB, phase_started)
        return GateResult(
            post,
            state,
            f"increment {state.name}: {INCREMENT_GLOB!r} under "
            f"{', '.join(repr(str(r)) for r in roots)}, floor {phase_started}",
        )

    # Every Postcondition member is handled above; a new member must be too.
    raise AssertionError(f"no gate implements {post!r}")
