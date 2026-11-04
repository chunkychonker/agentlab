"""A deterministic synthetic tool catalog, shaped like a multi-service MCP setup.

Pure: no I/O, no SDK, no randomness. The same ``n`` always yields the same list,
so token counts and search results are reproducible across runs.

See research/2026-11-04-tool-search-deferred-tools.md.
"""

from __future__ import annotations

from itertools import product

Tool = dict  # an Anthropic custom-tool definition: name, description, input_schema


def _tool(name: str, description: str, args: dict[str, str]) -> Tool:
    """One tool definition whose arguments are all required strings."""
    return {
        "name": name,
        "description": description,
        "input_schema": {
            "type": "object",
            "properties": {arg: {"type": "string", "description": text} for arg, text in args.items()},
            "required": list(args),
        },
    }


# Hand-written tools: the ones the tests and the live demo search for by name.
_BASE: tuple[Tool, ...] = (
    _tool("read_file", "Read a UTF-8 text file from the workspace and return its contents.",
          {"path": "Workspace-relative path of the file to read."}),
    _tool("send_email", "Send a plain-text email to one recipient.",
          {"to": "Recipient email address.", "subject": "Subject line.", "body": "Message body."}),
    _tool("search_docs", "Full-text search over the internal documentation wiki.",
          {"query": "Words to search for."}),
    _tool("get_weather", "Get the current weather conditions and temperature for a city.",
          {"location": "City and region, e.g. 'Madison, WI'."}),
    _tool("get_stock_data", "Get the latest share price and daily change for a ticker symbol.",
          {"ticker": "Exchange ticker symbol, e.g. 'ACME'."}),
    _tool("get_sales_data", "Get total sales revenue for one region and quarter.",
          {"region": "Sales region name.", "quarter": "Quarter, e.g. '2026-Q3'."}),
    _tool("github_create_issue", "Open a new issue in a GitHub repository.",
          {"repo": "owner/name of the repository.", "title": "Issue title.", "body": "Issue body."}),
    _tool("github_list_pull_requests", "List open pull requests in a GitHub repository.",
          {"repo": "owner/name of the repository."}),
    _tool("slack_post_message", "Post a message to a Slack channel.",
          {"channel": "Channel name without the leading '#'.", "text": "Message text."}),
    _tool("slack_search_messages", "Search Slack message history.",
          {"query": "Words to search for."}),
    _tool("jira_create_ticket", "Create a Jira ticket in a project.",
          {"project": "Jira project key, e.g. 'OPS'.", "summary": "One-line summary."}),
    _tool("jira_transition_ticket", "Move a Jira ticket to a new workflow status.",
          {"ticket": "Ticket key, e.g. 'OPS-12'.", "status": "Target status name."}),
)

# The tools a typical turn needs. They stay non-deferred (the docs advise 3-5).
HOT_TOOLS: frozenset[str] = frozenset({"read_file", "send_email", "search_docs"})

_SERVICES = ("github", "slack", "jira", "gdrive", "calendar", "notion", "stripe", "datadog")
_VERBS = ("list", "get", "create", "update", "delete", "archive")
_NOUNS = ("item", "comment", "label", "member", "webhook", "report")


def _generated() -> tuple[Tool, ...]:
    """Filler tools ``<service>_<verb>_<noun>``, in a fixed order, none clashing with _BASE."""
    taken = {tool["name"] for tool in _BASE}
    tools = []
    for verb, noun, service in product(_VERBS, _NOUNS, _SERVICES):  # services interleave
        name = f"{service}_{verb}_{noun}"
        if name in taken:
            continue
        tools.append(_tool(
            name,
            f"{verb.capitalize()} a {noun} in {service.capitalize()}.",
            {"id": f"Identifier of the {service.capitalize()} {noun}.",
             "workspace": f"{service.capitalize()} workspace or account to act in."},
        ))
    return tuple(tools)


_ALL: tuple[Tool, ...] = _BASE + _generated()

# Largest n make_catalog accepts.
MAX_CATALOG_SIZE: int = len(_ALL)


def make_catalog(n: int) -> list[Tool]:
    """The first ``n`` tools: the 12 hand-written ones, then generated filler.

    Every call returns fresh dicts, so callers may mutate the result freely.

    Raises:
        ValueError: if ``n < len(base tools)`` (the hand-written tools, including
            every HOT_TOOLS member, must all be present) or
            ``n > MAX_CATALOG_SIZE``.
    """
    if not len(_BASE) <= n <= MAX_CATALOG_SIZE:
        raise ValueError(f"n must be in [{len(_BASE)}, {MAX_CATALOG_SIZE}], got {n}")
    return [_copy(tool) for tool in _ALL[:n]]


def _copy(tool: Tool) -> Tool:
    """A deep copy of one tool definition (dicts of dicts and lists of str only)."""
    schema = tool["input_schema"]
    return {
        "name": tool["name"],
        "description": tool["description"],
        "input_schema": {
            "type": schema["type"],
            "properties": {k: dict(v) for k, v in schema["properties"].items()},
            "required": list(schema["required"]),
        },
    }
