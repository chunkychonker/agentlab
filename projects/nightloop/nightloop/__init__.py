"""nightloop: a gated overnight research -> build -> review loop for a git repo.

Public config API: ``from nightloop.config import load_config, parse_config, ...``.
Public gates API: ``from nightloop.gates import evaluate, GateResult, ...``.
Public phase API: ``from nightloop.phase import run_phase, ...``.
Public cycle API: ``from nightloop.cycle import run_night, GitWorkspace, ...``.
Public ship API: ``from nightloop.ship import GhShipper``.
Public claims API: ``from nightloop.claims import reconcile_stranded, mark_done, ...``.
Public schedule API: ``from nightloop.schedule import nightly_job, plan_install, ...``.
Public CLI: ``python -m nightloop run|report|schedule`` or ``from nightloop.cli import main``.
"""
