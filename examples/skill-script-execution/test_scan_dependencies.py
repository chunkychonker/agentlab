"""Offline self-test for scan_dependencies.py.

Stdlib-only `unittest`, no network, no API key — same convention as
`skill-anatomy/test_validate_skill.py`. Each test asserts the *specific*
finding (or absence of one) for one case, not just "some finding appeared".

Run:
    cd examples/skill-script-execution
    python3 test_scan_dependencies.py
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SCRIPT_PATH = _HERE / "skills" / "scanning-dependencies" / "scripts" / "scan_dependencies.py"

_spec = importlib.util.spec_from_file_location("scan_dependencies", _SCRIPT_PATH)
scan_dependencies = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["scan_dependencies"] = scan_dependencies  # dataclasses needs this registered
_spec.loader.exec_module(scan_dependencies)


def _findings_for(package: str, result: dict) -> list[dict]:
    return [f for f in result["findings"] if f["package"] == package]


class ScanRequirementsTxtTests(unittest.TestCase):
    def test_bare_name_is_unpinned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "requirements.txt").write_text("numpy\n", encoding="utf-8")
            result = scan_dependencies.scan_directory(root)
            hits = _findings_for("numpy", result)
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0]["reason"], "no version pin (bare package name)")

    def test_exact_pin_is_not_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "requirements.txt").write_text("numpy==1.26.4\n", encoding="utf-8")
            result = scan_dependencies.scan_directory(root)
            self.assertEqual(_findings_for("numpy", result), [])
            self.assertEqual(result["count"], 0)

    def test_range_pin_is_unpinned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "requirements.txt").write_text("numpy>=1.26\n", encoding="utf-8")
            result = scan_dependencies.scan_directory(root)
            hits = _findings_for("numpy", result)
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0]["reason"], "range specifier, not an exact pin")
            self.assertEqual(hits[0]["version_spec"], ">=1.26")

    def test_comments_and_includes_are_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "requirements.txt").write_text(
                "# a comment\n-r other.txt\nrequests==2.32.3\n", encoding="utf-8"
            )
            result = scan_dependencies.scan_directory(root)
            self.assertEqual(result["count"], 0)
            self.assertEqual(len(result["findings"]), 0)


class ScanPackageJsonTests(unittest.TestCase):
    def test_caret_range_is_unpinned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text(
                json.dumps({"dependencies": {"lodash": "^4.17.21"}}), encoding="utf-8"
            )
            result = scan_dependencies.scan_directory(root)
            hits = _findings_for("lodash", result)
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0]["version_spec"], "^4.17.21")

    def test_exact_semver_is_not_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text(
                json.dumps({"dependencies": {"lodash": "4.17.21"}}), encoding="utf-8"
            )
            result = scan_dependencies.scan_directory(root)
            self.assertEqual(_findings_for("lodash", result), [])
            self.assertEqual(result["count"], 0)

    def test_malformed_json_reported_not_crashed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text("{ not valid json", encoding="utf-8")
            (root / "requirements.txt").write_text("flask\n", encoding="utf-8")
            result = scan_dependencies.scan_directory(root)
            # The malformed package.json still produces exactly one finding...
            parse_error_findings = [
                f for f in result["findings"] if "could not parse package.json" in f["reason"]
            ]
            self.assertEqual(len(parse_error_findings), 1)
            # ...and the valid requirements.txt in the same tree is still scanned.
            self.assertEqual(len(_findings_for("flask", result)), 1)
            self.assertEqual(result["count"], 2)


class ScanDirectoryTests(unittest.TestCase):
    def test_no_manifests_is_zero_count_valid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text("nothing to see here\n", encoding="utf-8")
            result = scan_dependencies.scan_directory(root)
            self.assertEqual(result["count"], 0)
            self.assertEqual(result["scanned"], [])
            self.assertEqual(result["findings"], [])

    def test_nonexistent_directory_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            scan_dependencies.scan_directory(Path("/definitely/does/not/exist/anywhere"))

    def test_ignored_dirs_are_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ignored = root / "node_modules" / "some_pkg"
            ignored.mkdir(parents=True)
            (ignored / "package.json").write_text(
                json.dumps({"dependencies": {"leftpad": "^1.0.0"}}), encoding="utf-8"
            )
            result = scan_dependencies.scan_directory(root)
            self.assertEqual(result["count"], 0)
            self.assertEqual(result["scanned"], [])


class CliTests(unittest.TestCase):
    """Exercises main() via subprocess, matching how the skill invokes it."""

    def test_exit_0_and_valid_json_on_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "requirements.txt").write_text("numpy\n", encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(_SCRIPT_PATH), str(root)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["count"], 1)

    def test_exit_2_and_error_json_on_missing_directory(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(_SCRIPT_PATH), "/definitely/does/not/exist/anywhere"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 2)
        payload = json.loads(proc.stdout)
        self.assertIn("error", payload)
        self.assertNotIn("Traceback", proc.stdout)
        self.assertEqual(proc.stderr, "")


if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    sys.exit(0 if result.wasSuccessful() else 1)
