"""The demo's one tool: a read-only SQLite query over a synthetic sales table.

The tool edge: each call builds a fresh in-memory database, so calls on
different threads share nothing. The data is made up and labeled as such.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing

_SCHEMA = "CREATE TABLE sales (region TEXT, quarter TEXT, revenue_usd INTEGER, target_usd INTEGER)"
_ROWS = (
    ("West", "2026-Q2", 410_000, 400_000),
    ("West", "2026-Q3", 370_000, 400_000),
    ("East", "2026-Q2", 520_000, 500_000),
    ("East", "2026-Q3", 515_000, 500_000),
    ("Central", "2026-Q2", 290_000, 300_000),
    ("Central", "2026-Q3", 255_000, 300_000),
    ("South", "2026-Q2", 330_000, 320_000),
    ("South", "2026-Q3", 345_000, 320_000),
)

# Programmatic tools: Claude's code parses the returned string, so the
# description states the output format (docs advice).
TOOLS: list[dict] = [
    {"type": "code_execution_20260120", "name": "code_execution"},
    {
        "name": "query_database",
        "description": (
            "Run ONE read-only SQLite SELECT against a synthetic sales database. "
            "Table: sales(region TEXT, quarter TEXT, revenue_usd INTEGER, target_usd INTEGER). "
            "Regions: West, East, Central, South. Quarters: '2026-Q2', '2026-Q3'. "
            "Returns a JSON array of row objects (column name -> value) as a string."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"sql": {"type": "string", "description": "A single SQLite SELECT statement."}},
            "required": ["sql"],
        },
        "allowed_callers": ["code_execution_20260120"],
    },
]


def query_database(tool_input: dict) -> str:
    """Rows from running ``tool_input["sql"]`` as a JSON array string.

    Raises:
        KeyError: no ``sql`` key.
        sqlite3.Error: bad SQL, more than one statement, or any write. The
            connection is ``query_only``. The loop reports this to the model
            as an ``Error:`` string.
    """
    sql = tool_input["sql"]
    with closing(sqlite3.connect(":memory:")) as conn:
        conn.execute(_SCHEMA)
        conn.executemany("INSERT INTO sales VALUES (?, ?, ?, ?)", _ROWS)
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.execute(sql)
        columns = [col[0] for col in cursor.description or ()]
        return json.dumps([dict(zip(columns, row)) for row in cursor.fetchall()])


HANDLERS = {"query_database": query_database}
