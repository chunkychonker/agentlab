"""Offline self-test for the SDK 2.x compat scanner, one test per acceptance criterion.

Stdlib only: no API key, no network, no third-party packages. Runs under
``python3 test_rules.py`` and ``python3 -I test_rules.py`` alike.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))  # explicit, so isolated mode (-I) can import the core

from rules import Rule, scan_requirement, scan_source  # noqa: E402


def ids(source: str) -> list[tuple[str, int]]:
    """(rule id, line) for every unsuppressed finding in ``source``."""
    return [(f.rule.rule_id, f.line) for f in scan_source("t.py", source).findings]


def test_ac1_sampling_params() -> None:
    flagged = "x = 1\nclient.messages.create(model=M, max_tokens=1, temperature=0, messages=[])\n"
    assert ids(flagged) == [("SAMPLING-PARAM", 2)], ids(flagged)
    clean = "client.messages.create(model=M, max_tokens=1, messages=[])\n"
    assert ids(clean) == [], ids(clean)
    default = "client.messages.create(model=M, max_tokens=1, temperature=1.0, messages=[])\n"
    assert ids(default) == [("SAMPLING-PARAM", 1)], ids(default)
    # every documented entry point, beta included, and all three params
    for call in (
        "client.beta.messages.stream(top_p=0.9)",
        "client.messages.count_tokens(top_k=5)",
        "client.beta.messages.tool_runner(temperature=0)",
        "client.messages.parse(temperature=0)",
    ):
        assert [r for r, _ in ids(call)] == ["SAMPLING-PARAM"], call
    # a batch request's params dict, and the false-positive guards
    batch = 'reqs = [{"custom_id": "a", "params": {"model": M, "max_tokens": 9, "top_k": 3}}]\n'
    assert ids(batch) == [("SAMPLING-PARAM", 1)], ids(batch)
    assert ids("openai.chat.completions.create(temperature=0)") == []
    assert ids('cfg = {"temperature": 0.2}') == []
    print("ok  AC1 sampling params flagged on messages.* and request dicts, even at default")


def test_ac2_messageparam() -> None:
    assert ids('MessageParam(role="user", content="x")') == [("MESSAGEPARAM-CALL", 1)]
    assert ids("m = types.BetaMessageParam(role='user', content='x')") == [("MESSAGEPARAM-CALL", 1)]
    assert ids("class Mine(MessageParam):\n    pass\n") == [("MESSAGEPARAM-CALL", 1)]
    assert ids("x: list[MessageParam] = []\n") == []
    assert ids('m: MessageParam = {"role": "user", "content": "x"}\n') == []
    print("ok  AC2 MessageParam called or subclassed is flagged; annotations and dicts are not")


def test_ac3_dual_credential() -> None:
    assert ids("c = Anthropic(api_key=k, auth_token=t)") == [("DUAL-CREDENTIAL", 1)]
    assert ids("c = anthropic.AsyncAnthropic(profile=p, config=c)") == [("DUAL-CREDENTIAL", 1)]
    assert ids("c2 = c.with_options(api_key=k, credentials=cr)") == [("DUAL-CREDENTIAL", 1)]
    assert ids("c = Anthropic(api_key=k, auth_token=None)") == []
    assert ids("c = Anthropic(api_key=k)") == []
    print("ok  AC3 two non-None credentials on a client or with_options are flagged")


def test_ac4_models_betas() -> None:
    assert ids("client.models.list(betas=[b])") == [("MODELS-BETAS", 1)]
    assert ids("client.models.retrieve('m', betas=[b])") == [("MODELS-BETAS", 1)]
    assert ids("client.beta.models.list(betas=[b])") == []
    assert ids("client.models.list()") == []
    print("ok  AC4 models.list/retrieve(betas=) flagged unless called through client.beta")


def test_ac5_requirements() -> None:
    def rule(line: str) -> str | None:
        finding = scan_requirement("requirements.txt", 3, line)
        if finding is not None:
            assert finding.line == 3 and finding.path == "requirements.txt", finding
        return None if finding is None else finding.rule.rule_id

    for unbounded in ("anthropic>=0.120.0", "anthropic", "Anthropic >= 1.0  # live run only",
                      "anthropic[bedrock]>=1,!=1.5", "anthropic>1 ; python_version>='3.9'"):
        assert rule(unbounded) == "UNBOUNDED-PIN", unbounded
    for capped in ("anthropic>=1.15.0,<2", "anthropic==1.4.0", "anthropic~=1.4",
                   "anthropic<=1.9", "anthropic==2.3.0"):
        assert rule(capped) is None, capped
    for other in ("anthropic-foo>=1", "# anthropic>=1", "", "   ", "-r base.txt", "pydantic>=2"):
        assert rule(other) is None, other
    for broken in ("anthropic >>1", "anthropic @ https://example.invalid/a.whl"):
        assert rule(broken) == "PARSE-ERROR", broken
    print("ok  AC5 unbounded anthropic pins flagged; capped, exact, other packages are not")


def test_ac6_parse_error_and_bad_root() -> None:
    report = scan_source("bad.py", "x = 1\ndef broken(:\n")
    assert [(f.rule.rule_id, f.line) for f in report.findings] == [("PARSE-ERROR", 2)], report
    with tempfile.TemporaryDirectory() as tmp:
        missing = Path(tmp) / "does-not-exist"
        run = subprocess.run([sys.executable, str(HERE / "scan.py"), str(missing)],
                             capture_output=True, text=True)
    assert run.returncode == 2 and "not a directory" in run.stderr, (run.returncode, run.stderr)
    print("ok  AC6 a syntax error is a PARSE-ERROR finding; an unreadable root exits 2")


def test_ac7_scan_a_tree_end_to_end() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "app").mkdir()
        (root / "app" / "agent.py").write_text(
            "r = client.messages.create(model=M, max_tokens=8,\n"
            "                           temperature=0, messages=[])\n"
        )
        (root / "app" / "requirements.txt").write_text("# live only\nanthropic>=0.120.0\n")
        (root / "clean").mkdir()
        (root / "clean" / "ok.py").write_text("print('hi')\n")
        (root / "clean" / "requirements-dev.txt").write_text("anthropic>=1.15.0,<2\n")
        (root / ".venv").mkdir()  # skipped: would otherwise be flagged
        (root / ".venv" / "x.py").write_text("MessageParam(role='user')\n")

        run = subprocess.run([sys.executable, "-I", str(HERE / "scan.py"), str(root)],
                             capture_output=True, text=True)
        assert run.returncode == 1, (run.returncode, run.stderr)
        lines = run.stdout.splitlines()
        assert lines[0].startswith("app/agent.py:2: error SAMPLING-PARAM"), lines
        assert lines[1].startswith("app/requirements.txt:2: warn  UNBOUNDED-PIN"), lines
        assert lines[2] == "2 finding(s) (1 error, 1 warn) in 4 file(s); 0 suppressed by pragma", lines

        (root / "app" / "agent.py").write_text("print('fixed')\n")
        (root / "app" / "requirements.txt").write_text("anthropic==1.4.0\n")
        clean = subprocess.run([sys.executable, str(HERE / "scan.py"), str(root)],
                               capture_output=True, text=True)
        assert clean.returncode == 0, (clean.returncode, clean.stdout)
        assert "not a guarantee" in clean.stdout, clean.stdout
    print("ok  AC7 scan.py over a tmp tree: right lines, skips .venv, exit 1 then 0 once fixed")


def test_message_shape_rules() -> None:
    doc = ('m = {"role": "assistant",\n'
           '     "content": [{"type": "document", "source": s}]}\n')
    assert ids(doc) == [("DOC-IN-ASSISTANT", 2)], ids(doc)
    nested = ('m = {"role": "user", "content": [{"type": "tool_result", "tool_use_id": i,\n'
              '     "content": [{"type": "document", "source": s}]}]}\n')
    assert ids(nested) == []
    cited = ('m = {"role": "user", "content": [{"type": "search_result", "content": [\n'
             '     {"type": "text", "text": "t", "citations": [c]}]}]}\n')
    assert ids(cited) == [("CITATIONS-IN-USER", 2)], ids(cited)
    assert ids('m = {"role": "user", "content": [{"type": "text", "text": "t", "citations": []}]}') == []
    assert ids('m = {"role": "assistant", "content": [{"type": "text", "citations": [c]}]}') == []
    print("ok  document-in-assistant and citations-in-user flagged only in the documented shapes")


def test_xapikey_and_pragma() -> None:
    assert ids('headers = {"X-Api-Key": key}') == [("XAPIKEY-HEADER", 1)]
    report = scan_source("t.py", 'h = "x-api-key"  # sdk2-compat: ignore[XAPIKEY-HEADER]\n')
    assert report.findings == () and [f.rule for f in report.suppressed] == [Rule.XAPIKEY_HEADER]
    # a pragma for a different rule does not suppress; a typo'd pragma is loud
    assert ids('h = "x-api-key"  # sdk2-compat: ignore[SAMPLING-PARAM]') == [("XAPIKEY-HEADER", 1)]
    assert ids("x = 1  # sdk2-compat: ignore[NO-SUCH-RULE]") == [("PARSE-ERROR", 1)]
    # pragma text inside a string is not a pragma
    assert ids('s = "# sdk2-compat: ignore[NO-SUCH-RULE]"') == []
    print("ok  x-api-key literal flagged; pragmas suppress only their rule and typos are loud")


def main() -> int:
    tests: list[Callable[[], None]] = [
        test_ac1_sampling_params,
        test_ac2_messageparam,
        test_ac3_dual_credential,
        test_ac4_models_betas,
        test_ac5_requirements,
        test_ac6_parse_error_and_bad_root,
        test_ac7_scan_a_tree_end_to_end,
        test_message_shape_rules,
        test_xapikey_and_pragma,
    ]
    for test in tests:
        test()
    print(f"\nAll {len(tests)} self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
