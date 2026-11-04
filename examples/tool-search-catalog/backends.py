"""Canned, deterministic results for the synthetic catalog's tools.

The catalog's services are fictional, so nothing real is called. Each result
is labeled synthetic so a transcript can never be mistaken for live data.
Tool search is the subject of this example, not the tools.
"""

from __future__ import annotations

import json

_CANNED: dict[str, dict] = {
    "get_weather": {"conditions": "overcast", "temp_f": 41, "wind_mph": 12},
    "get_stock_data": {"price": 123.45, "change_pct": -0.8},
    "get_sales_data": {"revenue_usd": 1_250_000},
}


def run_tool(name: str, tool_input: dict) -> str:
    """A JSON string answering a call to ``name`` with ``tool_input``. Never raises.

    Known tools get a fixed payload; every other catalog tool gets a generic
    acknowledgement that echoes its input. The caller is responsible for only
    passing names that exist in the catalog.
    """
    payload = _CANNED.get(name, {"ok": True})
    return json.dumps({"synthetic": True, "tool": name, "input": tool_input, **payload}, sort_keys=True)
