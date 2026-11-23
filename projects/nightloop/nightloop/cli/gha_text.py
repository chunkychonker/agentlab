"""The person-facing text ``schedule-gha`` prints after the workflow: the steps only a human can do.

Pure: builds strings from values; no I/O.
"""

from __future__ import annotations

from ..schedule_gha.spec import Credential

_SECRET_HOW = {
    Credential.OAUTH_TOKEN: "make it with `claude setup-token` (Pro/Max/Team/Enterprise); "
                            "it ties every night to that one person's subscription",
    Credential.API_KEY: "an Anthropic API key; every night is billed per token to that API account",
}


def manual_steps(*, credential: Credential, workflow_name: str, agents: tuple[str, ...]) -> str:
    """The numbered manual steps and the known hazards, one paragraph each, no trailing newline."""
    names = ", ".join(agents)
    return "\n".join([
        "Manual steps (nightloop cannot do these for you):",
        f"  1. Add the repository secret {credential.value}: {_SECRET_HOW[credential]}.",
        "  2. Settings > Actions > General > Workflow permissions: enable",
        "     \"Allow GitHub Actions to create and approve pull requests\", or the PR step fails.",
        f"  3. Make the phases' agents ({names}) resolve on the runner, e.g. commit them",
        "     under .claude/agents/; the workflow does not install the nightloop plugin.",
        "  4. Commit the workflow to the default branch: schedules run only from there.",
        f"  5. Check it once by hand: Actions > {workflow_name} > Run workflow, read the log,",
        "     and download the nightloop-metrics artifact.",
        "Known hazards:",
        "  - This workflow has never been run on GitHub by nightloop's tests.",
        "  - PRs opened with GITHUB_TOKEN do not trigger other workflows; use a PAT or App token",
        "    if CI must run on nightloop's PRs.",
        "  - On a public repo GitHub disables the schedule after 60 days without repo activity.",
        "  - A late or dropped scheduled run is normal; a late one is refused by nightloop run",
        "    (exit 3, the job shows failed) and nothing is changed.",
    ])
