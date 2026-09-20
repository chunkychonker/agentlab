"""Detect JSON-Schema keywords that Anthropic's strict grammar compiler rejects.

`"strict": true` on a tool definition compiles the tool's `input_schema` into a
sampling grammar, and only a documented subset of JSON Schema survives that
compilation. A schema outside the subset is a 400 at request time, not a quiet
degradation - so it is worth checking before you ship.

This module is pure: no I/O, no clock, no globals. It only *detects*; it never
rewrites a schema (deliberately out of scope - see the research note).

See the research note this came from:
    research/2026-09-03-strict-tool-schemas.md
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

# Transcribed from the "limitations" section of
# https://platform.claude.com/docs/en/build-with-claude/structured-outputs
# (fetched 2026-09-03; recorded in research/2026-09-03-strict-tool-schemas.md).
#
# Notably absent, and therefore reported: `oneOf` (only `anyOf` and `allOf` are
# supported), numeric bounds (`minimum`/`maximum`/`multipleOf`), string
# constraints (`minLength`/`maxLength`/`pattern`), and array constraints other
# than `minItems`.
SUPPORTED_KEYWORDS: frozenset[str] = frozenset(
    {
        # Structural.
        "type",
        "properties",
        "items",
        "required",
        # `additionalProperties` is supported only with the value `false`; this
        # module checks keywords, not values, so the value check lives in the
        # example's self-test (acceptance criterion 4).
        "additionalProperties",
        # Composition. `$ref`/`$defs`/`definitions` are supported for internal
        # references only; an external `$ref` is not, and is not detectable
        # from the keyword alone.
        "anyOf",
        "allOf",
        "$ref",
        "$defs",
        "definitions",
        # Values.
        "enum",
        "const",
        "default",
        "format",
        "minItems",
        # Annotations. `title` and `description` are not named in the docs'
        # supported-features list, but they carry no constraint and the SDK
        # emits `title` on every Pydantic-generated property while shipping
        # `strict=True` on that same generation path. Tolerated here for that
        # reason; see the "Open questions" section of
        # research/2026-09-03-strict-tool-schemas.md.
        "title",
        "description",
    }
)

# Keywords whose value is a mapping of *names* to sub-schemas. The mapping's
# own keys are property/definition names, never schema keywords, so they are
# recursed into but never reported.
_NAMED_SUBSCHEMAS = frozenset({"properties", "$defs", "definitions"})

# Keywords whose value is a list of sub-schemas.
_SUBSCHEMA_LISTS = frozenset({"anyOf", "allOf", "oneOf"})

# Keywords whose value is a single sub-schema (or, in older drafts, a list of
# them). `additionalProperties` is excluded on purpose: in the strict subset its
# only legal value is `false`, so there is no sub-schema to walk.
_SINGLE_SUBSCHEMAS = frozenset({"items"})


def unsupported_keywords(schema: Mapping[str, object]) -> list[str]:
    """Return sorted unique JSON-Schema keywords in `schema` (recursively) that
    the strict grammar compiler does not support.

    `[]` means the schema is strict-subset-compatible as far as keywords go.
    Property names, `$defs` entry names, and the *values* of `enum` / `const` /
    `default` / `required` are data, not keywords, and are never reported.

    Unsupported branch keywords are still walked, so a `oneOf` reports both
    itself and anything unsupported inside its members.

    Does not dereference `$ref`; an external reference (also unsupported) is
    invisible here because the keyword itself is legal.

    Failure modes: none by design. A non-mapping found where a sub-schema is
    expected is skipped rather than raised on, because real schemas nest lists
    and scalars in those positions. Never mutates `schema`.
    """
    found: set[str] = set()
    _collect(schema, found)
    return sorted(found)


def _collect(node: object, found: set[str]) -> None:
    """Accumulate unsupported keywords from one schema node into `found`."""
    if not isinstance(node, Mapping):
        return

    for key, value in node.items():
        if key not in SUPPORTED_KEYWORDS:
            found.add(str(key))

        if key in _NAMED_SUBSCHEMAS:
            if isinstance(value, Mapping):
                for sub in value.values():
                    _collect(sub, found)
        elif key in _SUBSCHEMA_LISTS:
            for sub in _as_schema_list(value):
                _collect(sub, found)
        elif key in _SINGLE_SUBSCHEMAS:
            if isinstance(value, Mapping):
                _collect(value, found)
            else:
                for sub in _as_schema_list(value):
                    _collect(sub, found)


def _as_schema_list(value: object) -> Sequence[object]:
    """The members of a list-of-sub-schemas keyword, or `()` if it is not one."""
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return value
    return ()
