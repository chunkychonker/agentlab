"""nightloop: a gated overnight research -> build -> review loop for a git repo.

Public config API: ``from nightloop.config import load_config, parse_config, ...``.
Public gates API: ``from nightloop.gates import evaluate, GateResult, ...``.
Public phase API: ``from nightloop.phase import run_phase, ...``.
Public cycle API: ``from nightloop.cycle import run_night, GitWorkspace, ...``.
Public ship API: ``from nightloop.ship import GitHubShipper, GitBranchPublisher, GhCli, ...``.
"""
