"""What a cautious MCP client *could* do with tool annotations: a pure rule.

Nothing in MCP requires a client to do this, and the spec says annotations
from an untrusted server MUST be treated as untrusted. This module only
encodes the spec's defaults so their consequence is checkable:

  readOnlyHint    default False
  destructiveHint default True   (meaningful only when not read-only)
  idempotentHint  default False  (irrelevant to confirmation here)
  openWorldHint   default True   (irrelevant to confirmation here)

The SDK does NOT fill those defaults in -- unset fields arrive as None, and a
tool declared with no annotations arrives as `annotations is None` -- so the
defaults have to be applied here, by the reader.
"""

from mcp.types import ToolAnnotations


def needs_confirmation(annotations: ToolAnnotations | None) -> bool:
    """Return True if a cautious client should ask a human before calling.

    Rule (spec defaults applied to unset fields):
      - no annotations at all            -> True  (assume destructive writer)
      - read_only_hint is True           -> False (destructive_hint ignored)
      - destructive_hint unset or True   -> True
      - destructive_hint is False        -> False (additive-only writer)

    Pure; cannot fail.
    """
    if annotations is None:
        return True
    if annotations.read_only_hint is True:
        return False
    return annotations.destructive_hint is not False
