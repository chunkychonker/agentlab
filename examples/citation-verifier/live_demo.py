"""Live entry point: one cited answer from Claude, every citation re-checked.

The only file that reads the environment or talks to the network. Sends one
``messages.create`` with two citation-enabled documents (one plain text, one
custom content), re-derives each citation's span from the request's own
document blocks, prints a table, and exits non-zero unless there was at least
one citation and every citation is MATCH.

Billed: one short Haiku call, well under a cent. Without ``ANTHROPIC_API_KEY``
it prints a skip note and exits 0.
"""

from __future__ import annotations

import os
import sys

from sources import sources_from_messages
from verifier import Status, Verdict, count_by_status, uncited_text_blocks, verify_response

MODEL = "claude-haiku-4-5"
MAX_TOKENS = 512

EXIT_OK = 0
EXIT_UNVERIFIED = 1
EXIT_SETUP = 2

LAKE_TEXT = (
    "Lake Mendota covers about 40 square kilometres. "
    "Its deepest point is 25 metres. "
    "The lake usually freezes over in mid-December."
)
TERRACE_BLOCKS = (
    "The Memorial Union terrace opened in 1928.",
    "Its sunburst chairs were introduced in the 1930s.",
    "The terrace closes for the season each November.",
)
QUESTION = (
    "How deep is Lake Mendota, and when did the Memorial Union terrace open? "
    "Answer in two short sentences, citing the documents."
)


def build_request() -> dict:
    """Return the ``messages.create`` keyword arguments. Pure."""
    citations_on = {"enabled": True}
    lake = {
        "type": "document",
        "title": "Lake Mendota facts",
        "source": {"type": "text", "media_type": "text/plain", "data": LAKE_TEXT},
        "citations": citations_on,
    }
    terrace = {
        "type": "document",
        "title": "Memorial Union terrace",
        "source": {
            "type": "content",
            "content": [{"type": "text", "text": t} for t in TERRACE_BLOCKS],
        },
        "citations": citations_on,
    }
    return {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "messages": [
            {"role": "user", "content": [lake, terrace, {"type": "text", "text": QUESTION}]}
        ],
    }


def render(verdicts: list[Verdict], uncited: list[int]) -> str:
    """Format verdicts as a fixed-width table plus counts. Pure."""
    lines = [f"{'block':>5} {'cite':>4}  {'status':<18} detail"]
    for v in verdicts:
        lines.append(f"{v.block_index:>5} {v.citation_index:>4}  {v.status:<18} {v.detail}")
    counts = ", ".join(f"{s}={n}" for s, n in count_by_status(verdicts).items() if n)
    lines.append(f"\n{len(verdicts)} citation(s): {counts or 'none'}")
    lines.append(f"uncited text blocks: {uncited}")
    return "\n".join(lines)


def run(client) -> int:
    """Send the request through ``client`` (an ``anthropic.Anthropic``), verify, print.

    Returns ``EXIT_OK`` only if there was at least one citation and all are
    MATCH; otherwise ``EXIT_UNVERIFIED``. An empty citation list is a failure:
    "nothing to check" must not read as "all checked".

    Raises:
        anthropic.APIError (and subclasses): propagated unchanged from the call.
        ValueError: the response or request has a shape the verifier rejects.
    """
    request = build_request()
    message = client.messages.create(**request)
    content = [block.model_dump(exclude_none=True) for block in message.content]
    sources = sources_from_messages(request["messages"])
    verdicts = verify_response(sources, content)

    print(f"model={message.model} stop_reason={message.stop_reason}")
    print("answer: " + "".join(b["text"] for b in content if b.get("type") == "text"))
    print()
    print(render(verdicts, uncited_text_blocks(content)))

    if verdicts and all(v.status is Status.MATCH for v in verdicts):
        return EXIT_OK
    return EXIT_UNVERIFIED


def main() -> int:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        print("skip: ANTHROPIC_API_KEY is not set; the live check makes one billed call.")
        print("The offline self-test is: python3 -m unittest -v")
        return EXIT_OK
    try:
        import anthropic
    except ImportError:
        print("error: ANTHROPIC_API_KEY is set but the SDK is missing; pip install -r requirements.txt",
              file=sys.stderr)
        return EXIT_SETUP
    return run(anthropic.Anthropic(api_key=key))


if __name__ == "__main__":
    sys.exit(main())
