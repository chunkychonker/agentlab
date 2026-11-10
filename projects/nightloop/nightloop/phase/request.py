"""The validated description of one phase to run: what to ask, where, for how long.

Pure: no I/O. A ``PhaseRequest`` that exists is runnable as built, so the
argv builder and the runners never re-check it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..model import PHASE_POSTCONDITIONS, Phase

# A prompt that starts with this would be parsed by the CLI as an option, not
# as the prompt, so it is not representable.
_OPTION_PREFIX = "-"


@dataclass(frozen=True)
class PhaseRequest:
    """One phase run: the phase's ``name`` and config, the prompt, its cwd and wall-clock limit.

    Raises ``ValueError`` if ``name`` is not one of the config's phases
    (``research``, ``build``, ``review``), if ``phase.postcondition`` is not
    one that phase may use, if ``prompt`` is empty, starts with ``-`` or holds
    a NUL byte (argv cannot carry one), if ``cwd`` is relative, or if
    ``timeout_s`` is not positive. Raises ``TypeError`` if a field has the
    wrong type (``timeout_s`` must be an ``int``, not a ``bool``).
    """

    name: str
    phase: Phase
    prompt: str
    cwd: Path
    timeout_s: int

    def __post_init__(self) -> None:
        if not isinstance(self.name, str):
            raise TypeError(f"name must be a str, got {type(self.name).__name__}")
        if self.name not in PHASE_POSTCONDITIONS:
            raise ValueError(
                f"name must be one of {', '.join(PHASE_POSTCONDITIONS)}, got {self.name!r}"
            )
        if not isinstance(self.phase, Phase):
            raise TypeError(f"phase must be a Phase, got {type(self.phase).__name__}")
        if self.phase.postcondition not in PHASE_POSTCONDITIONS[self.name]:
            raise ValueError(
                f"postcondition {self.phase.postcondition.value!r} is not allowed "
                f"for the {self.name} phase"
            )
        self._check_prompt()
        if not isinstance(self.cwd, Path):
            raise TypeError(f"cwd must be a Path, got {type(self.cwd).__name__}")
        if not self.cwd.is_absolute():
            raise ValueError(f"cwd must be an absolute path, got {str(self.cwd)!r}")
        if isinstance(self.timeout_s, bool) or not isinstance(self.timeout_s, int):
            raise TypeError(f"timeout_s must be an int, got {type(self.timeout_s).__name__}")
        if self.timeout_s <= 0:
            raise ValueError(f"timeout_s must be > 0, got {self.timeout_s}")

    def _check_prompt(self) -> None:
        if not isinstance(self.prompt, str):
            raise TypeError(f"prompt must be a str, got {type(self.prompt).__name__}")
        if self.prompt.strip() == "":
            raise ValueError("prompt must be non-empty")
        if self.prompt.startswith(_OPTION_PREFIX):
            raise ValueError("prompt must not start with '-': the CLI would read it as an option")
        if "\x00" in self.prompt:
            raise ValueError("prompt must not contain a NUL byte")
