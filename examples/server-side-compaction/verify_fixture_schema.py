"""Validates this example's response fixtures against Anthropic's own schema.

The offline self-test proves the core reads its fixtures correctly. That is
worth nothing if the fixtures are wrong. This script closes the loop: it parses
every fixture `test_compaction.py` uses through the real
`anthropic.types.beta.BetaMessage` model, then feeds the SDK's own
`model_dump(mode="json")` back through `report.read_response` and checks it
produces the same report as the hand-written dict did.

So the claim being verified is not "our parser agrees with our fixtures" but
"our fixtures are shaped like real responses, and our parser reads the SDK's
output of them identically".

Needs the SDK (`pip install -r requirements.txt`). Still needs **no API key and
no network**: nothing here constructs a client or makes a request.

Run:
    pip install -r requirements.txt
    python3 verify_fixture_schema.py

See the research note this came from:
    research/2026-08-12-server-side-compaction.md
"""

from __future__ import annotations

import sys
import typing

import policy as policy_module
import report as report_module
import test_compaction

MISSING_SDK_MESSAGE = (
    "error: the anthropic SDK is not installed, so there is no schema to "
    "validate against.\n"
    "  pip install -r requirements.txt   (no API key or network is needed)\n"
    "  The offline self-test, 'python3 test_compaction.py', needs neither."
)

EXIT_OK = 0
EXIT_NO_SDK = 1


def main() -> int:
    """Validate every fixture and the two literal-union facts the example rests on.

    Failure modes: returns ``EXIT_NO_SDK`` with one line on stderr if `anthropic`
    is not importable. Any schema mismatch raises - a `pydantic.ValidationError`
    from `model_validate`, or an `AssertionError` naming the fixture whose report
    changed after a round trip through the SDK. Nothing here is caught and
    downgraded to a warning.
    """
    try:
        import anthropic
        from anthropic.types.beta import BetaMessage
        from anthropic.types.beta.beta_stop_reason import BetaStopReason
    except ImportError:
        print(MISSING_SDK_MESSAGE, file=sys.stderr)
        return EXIT_NO_SDK

    from anthropic.types.anthropic_beta_param import AnthropicBetaParam

    print(f"anthropic {anthropic.__version__}")

    stop_reasons = typing.get_args(BetaStopReason)
    assert report_module.COMPACTION_STOP_REASON in stop_reasons, stop_reasons
    print(
        f"ok  {report_module.COMPACTION_STOP_REASON!r} is a real BetaStopReason "
        f"({len(stop_reasons)} in the union)"
    )

    # The inverse of the reassurance recorded for context editing: this beta
    # string is *not* type-checked, so a typo in it fails only at the server.
    # Asserted, not assumed, because the whole reason BETA is a named constant is
    # that this is true.
    beta_literals = typing.get_args(typing.get_args(AnthropicBetaParam)[-1])
    assert policy_module.BETA not in beta_literals, policy_module.BETA
    assert "context-management-2025-06-27" in beta_literals
    print(
        f"ok  {policy_module.BETA!r} is absent from AnthropicBetaParam's "
        f"{len(beta_literals)} literals, so a typo is not a type error"
    )

    for name, fixture in test_compaction.SCHEMA_CHECKED_FIXTURES.items():
        parsed = BetaMessage.model_validate(fixture)
        from_sdk = report_module.read_response(parsed.model_dump(mode="json"))
        from_fixture = report_module.read_response(fixture)
        assert from_sdk == from_fixture, f"{name}: {from_sdk} != {from_fixture}"
        print(
            f"ok  {name} parses as BetaMessage and reads back identically "
            f"({from_sdk.total_billed_tokens} tokens billed)"
        )

    count = len(test_compaction.SCHEMA_CHECKED_FIXTURES)
    print(
        f"\nAll {count} fixtures validated against the SDK, "
        "with no key and no network."
    )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
