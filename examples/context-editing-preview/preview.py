"""Measures what a clearing policy would do to one request, from two free token
counts.

This is the pure core. It declares the `CountTokens` interface it needs and
never imports `anthropic`, never reads an env var, and never touches the
network; `main.py` supplies the SDK-backed implementation.

The two counts are the same request sent twice to the *free* token-counting
endpoint: once plain, once with `context_management`. The delta is the saving.
Nothing is generated, so nothing is billed.

Why a plain count at all, when the edited response already carries
`original_input_tokens`? Because when the trigger does not fire that field is
absent, and "the original count" would otherwise have to be inferred from the
very number we are trying to compare it against. The plain count is the
independent measurement; when the API also reports an original, the two must
agree or something is wrong and we say so.

See the research note this came from:
    research/2026-08-11-context-editing-preview.md
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from typing import Protocol

# Percentages are reported to this many decimal places. One is enough to rank
# two policies and few enough that the number does not look measured to the
# micro-token.
PERCENT_PRECISION = 1


class InconsistentCount(ValueError):
    """The two token counts contradict each other.

    `count_tokens` uses no prompt caching and the tokenizer is deterministic, so
    the same messages counted twice must give the same original size. If they do
    not, every number derived from them is untrustworthy - raised rather than
    picking one of the two and reporting a saving that no measurement supports.
    """


@dataclasses.dataclass(frozen=True)
class TokenCount:
    """One response from the token-counting endpoint.

    ``original_input_tokens`` is ``None`` when the response carried no
    `context_management` object - meaning no edit was applied, either because
    none was requested, the trigger did not fire, or `clear_at_least` could not
    be met.

    Note the asymmetry this type absorbs: the *count* endpoint's
    `context_management` carries only `original_input_tokens`. It has no
    `applied_edits`, unlike the generation response. Nothing downstream should
    reach for one.

    Failure modes: ``ValueError`` if either count is negative.
    """

    input_tokens: int
    original_input_tokens: int | None

    def __post_init__(self) -> None:
        if self.input_tokens < 0:
            raise ValueError(f"input_tokens must be >= 0, got {self.input_tokens}")
        if self.original_input_tokens is not None and self.original_input_tokens < 0:
            raise ValueError(
                "original_input_tokens must be >= 0 when present, "
                f"got {self.original_input_tokens}"
            )


@dataclasses.dataclass(frozen=True)
class PreviewReport:
    """What the policy would do to this request.

    ``tokens_saved`` and ``percent_saved`` are derived, not stored, so they
    cannot drift from the two counts they describe.

    Failure modes: ``ValueError`` if either count is negative, if the edited
    count exceeds the original (a negative saving is never a valid report), or
    if ``applied`` is false while the two counts differ (nothing was cleared, so
    there is nothing for them to differ by).
    """

    applied: bool
    original_input_tokens: int
    edited_input_tokens: int

    def __post_init__(self) -> None:
        if self.original_input_tokens < 0 or self.edited_input_tokens < 0:
            raise ValueError(
                "token counts must be >= 0, got original="
                f"{self.original_input_tokens}, edited={self.edited_input_tokens}"
            )
        if self.edited_input_tokens > self.original_input_tokens:
            raise ValueError(
                "editing cannot grow a request: edited="
                f"{self.edited_input_tokens} > original={self.original_input_tokens}"
            )
        if not self.applied and self.edited_input_tokens != self.original_input_tokens:
            raise ValueError(
                "no edit was applied, so the counts must be equal; got original="
                f"{self.original_input_tokens}, edited={self.edited_input_tokens}"
            )

    @property
    def tokens_saved(self) -> int:
        """Tokens the edit would remove from this request. Never negative."""
        return self.original_input_tokens - self.edited_input_tokens

    @property
    def percent_saved(self) -> float:
        """``tokens_saved`` as a percentage of the original, to one decimal.

        0.0 for an empty request, which is the only case where the ratio is
        undefined and also the only case where there is nothing to save.
        """
        if self.original_input_tokens == 0:
            return 0.0
        ratio = self.tokens_saved / self.original_input_tokens
        return round(100.0 * ratio, PERCENT_PRECISION)


class EditPolicy(Protocol):
    """Anything that can render itself as a `context_management` request value.

    Declared here so the core depends on the *capability* rather than on one
    strategy: `policy.ClearToolUsesPolicy` satisfies it structurally, and a
    future `clear_thinking_20251015` policy would too, with no change to this
    module. Implementations validate themselves at construction, so `to_config`
    cannot fail.
    """

    def to_config(self) -> Mapping[str, object]: ...


class CountTokens(Protocol):
    """Counts the tokens of one request, optionally under a context-management
    config.

    Implementations do the I/O. The core only requires that passing
    ``context_management=None`` counts the request untouched, and that a
    response without a `context_management` object maps to
    ``original_input_tokens=None``.

    Implementations propagate transport and API errors; they never substitute a
    zero or an empty count for a failed call.
    """

    def __call__(
        self,
        *,
        messages: Sequence[Mapping[str, object]],
        tools: Sequence[Mapping[str, object]],
        context_management: Mapping[str, object] | None,
    ) -> TokenCount: ...


def preview(
    count: CountTokens,
    transcript: Sequence[Mapping[str, object]],
    tools: Sequence[Mapping[str, object]],
    policy: EditPolicy,
) -> PreviewReport:
    """Count ``transcript`` twice - plain, then under ``policy`` - and report
    the difference.

    Makes exactly two calls to ``count``, both idempotent and free, and is safe
    to re-run.

    Failure modes: anything ``count`` raises propagates untouched - an API error
    must never arrive as a zero-saving report. ``InconsistentCount`` if the two
    counts disagree about the original size. ``ValueError`` from
    ``PreviewReport`` if the edited count exceeds the original.
    """
    plain = count(messages=transcript, tools=tools, context_management=None)
    edited = count(
        messages=transcript, tools=tools, context_management=policy.to_config()
    )

    if edited.original_input_tokens is None:
        # No edit applied: the "edited" count is just another plain count, so it
        # must match the first one.
        if edited.input_tokens != plain.input_tokens:
            raise InconsistentCount(
                "the request was reported unedited, but counting it twice gave "
                f"{plain.input_tokens} then {edited.input_tokens} tokens"
            )
        return PreviewReport(
            applied=False,
            original_input_tokens=plain.input_tokens,
            edited_input_tokens=plain.input_tokens,
        )

    if edited.original_input_tokens != plain.input_tokens:
        raise InconsistentCount(
            "the plain count and the API's reported original disagree: "
            f"plain={plain.input_tokens}, "
            f"original_input_tokens={edited.original_input_tokens}"
        )

    return PreviewReport(
        applied=True,
        original_input_tokens=edited.original_input_tokens,
        edited_input_tokens=edited.input_tokens,
    )
