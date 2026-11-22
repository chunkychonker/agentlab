"""Shared builders for the milestone-9 scheduler tests (not a test module).

``FakeLaunchctl`` keeps launchd's state in a dict and behaves like the real
commands where it matters to the plan: ``bootout`` of an unloaded job and
``bootstrap`` of a loaded one fail (launchctl's "failed: 5"), ``bootstrap``
reads the plist at the given path, and ``lint`` parses the file it is
handed. It records every call so tests can assert that no mutating call
was made.
"""

from __future__ import annotations

import plistlib
from pathlib import Path, PurePosixPath

from nightloop.schedule import JobState, LaunchdJob, ScheduleError
from nightloop.schedule.ports import MUTATING_METHODS

# Real `launchctl print gui/502/<label>` output captured on Darwin 25.2,
# trimmed to the lines around the state keys; nested blocks keep their own
# "state = active" and the extra "job state = exited" key.
PRINT_RUNNING = """\
gui/502/com.steeb.agentlab.daily = {
\tactive count = 1
\tpath = /Users/steeb/Library/LaunchAgents/com.steeb.agentlab.daily.plist
\ttype = LaunchAgent
\tstate = running

\tprogram = /bin/bash
\targuments = {
\t\t/bin/bash
\t\t/Users/steeb/agentlab-runner/.pipeline/run.sh
\t}

\tresource coalition = {
\t\tID = 1234
\t\ttype = resource
\t\tstate = active
\t}

\tjetsam coalition = {
\t\tID = 1235
\t\ttype = jetsam
\t\tstate = active
\t}
}
"""
PRINT_IDLE = """\
gui/502/com.apple.quicklook = {
\tactive count = 0
\tpath = /System/Library/LaunchAgents/com.apple.quicklook.plist
\ttype = LaunchAgent
\tstate = not running

\tresource coalition = {
\t\tstate = active
\t}
\tjob state = exited
}
"""
PRINT_NOT_FOUND_STDERR = (
    'Bad request.\nCould not find service "no.such.label" in domain for user gui: 502\n'
)


def make_job(**overrides: object) -> LaunchdJob:
    """A valid job; ``overrides`` replace single fields."""
    fields: dict[str, object] = {
        "label": "dev.nightloop.test.0123abcd",
        "argv": ("/usr/bin/python3", "-m", "nightloop", "run", "--repo", "/r", "--config", "/r/n.toml"),
        "workdir": PurePosixPath("/r"),
        "path_env": "/usr/bin:/bin",
        "hour": 2,
        "minute": 0,
        "out_log": PurePosixPath("/r/logs/out.log"),
        "err_log": PurePosixPath("/r/logs/err.log"),
    }
    fields.update(overrides)
    return LaunchdJob(**fields)  # type: ignore[arg-type]


class FakeLaunchctl:
    """An in-memory ``Launchctl``. ``fail={"bootstrap": "msg"}`` makes that method raise ``ScheduleError``."""

    def __init__(self, states: dict[str, JobState] | None = None, *, fail: dict[str, str] | None = None) -> None:
        self.states: dict[str, JobState] = dict(states or {})
        self.fail = dict(fail or {})
        self.calls: list[tuple[str, ...]] = []
        self.loaded_plists: dict[str, bytes] = {}

    def mutating_calls(self) -> list[tuple[str, ...]]:
        return [call for call in self.calls if call[0] in MUTATING_METHODS]

    def _enter(self, *call: str) -> None:
        self.calls.append(call)
        if call[0] in self.fail:
            raise ScheduleError(f"`launchctl {' '.join(call)}` exited 5: {self.fail[call[0]]}")

    def state(self, domain: str, label: str) -> JobState:
        self._enter("state", domain, label)
        return self.states.get(label, JobState.NOT_LOADED)

    def enable(self, domain: str, label: str) -> None:
        self._enter("enable", domain, label)

    def bootstrap(self, domain: str, plist: PurePosixPath) -> None:
        self._enter("bootstrap", domain, str(plist))
        data = Path(plist).read_bytes()
        label = plistlib.loads(data)["Label"]
        if self.states.get(label, JobState.NOT_LOADED) is not JobState.NOT_LOADED:
            raise ScheduleError("Bootstrap failed: 5: Input/output error (already loaded)")
        self.states[label] = JobState.LOADED_IDLE
        self.loaded_plists[label] = data

    def bootout(self, domain: str, label: str) -> None:
        self._enter("bootout", domain, label)
        if self.states.get(label, JobState.NOT_LOADED) is JobState.NOT_LOADED:
            raise ScheduleError("Boot-out failed: 5: Input/output error (not loaded)")
        self.states[label] = JobState.NOT_LOADED
        self.loaded_plists.pop(label, None)

    def lint(self, path: PurePosixPath) -> None:
        self._enter("lint", str(path))
        plistlib.loads(Path(path).read_bytes())
