"""Checks that the TardyAI navigation seed data forms a graph the router can use.

The router assumes three things about `data/` that `upsertSeedData` never
checks: every NavEdge endpoint is a real MapNode, no NavEdge spans floors
(cross-floor movement is synthesised from connectorKey), and every connectorKey
links at least two floors. A violation loads fine and surfaces later as a
missing or nonsensical route in the Studio preview.

Usage: python3 -I seed_integrity.py <package-dir>
Exit 0 when the data is consistent; exit 1 listing each violation;
exit 2 when a seed file is missing or is not a JSON list.
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

_FILES = {
    "floors": "data/Floor/Floor.json",
    "nodes": "data/MapNode/MapNode.json",
    "edges": "data/NavEdge/NavEdge.json",
}


def _ref(obj: dict, key: str):
    value = obj.get(key)
    return value.get("id") if isinstance(value, dict) else None


def violations(floors: list[dict], nodes: list[dict], edges: list[dict]) -> list[str]:
    """Every consistency violation in the graph, as human-readable lines."""
    problems = []
    floor_ids = {f["id"] for f in floors}
    node_floor = {}
    for n in nodes:
        fid = _ref(n, "floor")
        if fid not in floor_ids:
            problems.append(f"MapNode {n['id']}: floor {fid!r} is not a Floor")
        node_floor[n["id"]] = fid

    for e in edges:
        a, b = _ref(e, "fromNode"), _ref(e, "toNode")
        missing = [x for x in (a, b) if x not in node_floor]
        if missing:
            problems.append(f"NavEdge {e['id']}: endpoint(s) {missing} are not MapNodes")
        elif node_floor[a] != node_floor[b]:
            problems.append(f"NavEdge {e['id']}: spans floors {node_floor[a]} -> {node_floor[b]}")
        if not isinstance(e.get("seconds"), (int, float)) or e["seconds"] <= 0:
            problems.append(f"NavEdge {e['id']}: seconds must be a positive number, got {e.get('seconds')!r}")

    floors_by_key = defaultdict(set)
    for n in nodes:
        if n.get("connectorKey"):
            floors_by_key[n["connectorKey"]].add(node_floor[n["id"]])
    for key, fs in sorted(floors_by_key.items()):
        if len(fs) < 2:
            problems.append(f"connectorKey {key!r}: links only {sorted(fs)} — needs at least two floors")
    return problems


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: seed_integrity.py <package-dir>", file=sys.stderr)
        return 2
    root = Path(sys.argv[1])
    loaded = {}
    for name, rel in _FILES.items():
        try:
            data = json.loads((root / rel).read_text())
        except (OSError, json.JSONDecodeError) as exc:
            print(f"seed_integrity: cannot load {rel}: {exc}", file=sys.stderr)
            return 2
        if not isinstance(data, list):
            print(f"seed_integrity: {rel} is not a JSON list", file=sys.stderr)
            return 2
        loaded[name] = data
    problems = violations(loaded["floors"], loaded["nodes"], loaded["edges"])
    for p in problems:
        print(f"seed_integrity: {p}")
    if not problems:
        print(f"seed_integrity: OK ({len(loaded['nodes'])} nodes, {len(loaded['edges'])} edges)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
