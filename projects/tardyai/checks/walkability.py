"""Checks that every TardyAI route segment stays where a person can walk.

The router draws each NavEdge as a straight line on the floor plan, so an edge
is only walkable if that line stays in corridors. Plans colour-fill rooms and
draw walls dark, so sampling the plan's pixels along each edge tells corridor
from room:

- Corridor edges (no room endpoint) must be at least 90% open pixels, ignoring
  the 2 m at each end where a node sits inside an elevator or stair box.
- A room edge is the last few steps through a door, so it may cross the room's
  fill but must be short (at most 15 m). A long one is a diagonal across other
  rooms, standing in for a missing door node on the corridor.
- No edge may cross a wall. Fill percentages miss thin walls (a 4 px wall is 2%
  of a 200 px edge) and rooms drawn white, so walls are counted as crossings:
  runs of dark pixels along the edge. The ends are skipped (2 m at a door or
  corridor node, 4 m at a room node, whose label box is outlined in ink), so a
  room edge passes only if it goes through the doorway gap.

Usage: python3 -I walkability.py <package-dir>
Exit 0 when every edge is walkable; exit 1 listing each violation; exit 2 when
a seed file or plan image is missing or unreadable.
"""

import json
import math
import sys
from pathlib import Path
from typing import Callable, Literal

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir
import png_rgb  # noqa: E402

Pixel = Literal["open", "fill", "wall"]
PixelClass = Callable[[str, float, float], Pixel]

ROOM_KINDS = frozenset({"room", "restroom"})
MAX_BLOCKED_FRACTION = 0.10
MAX_ROOM_EDGE_METERS = 15.0
ENDPOINT_MARGIN_METERS = 2.0
ROOM_END_MARGIN_METERS = 4.0
MIN_WALL_RUN_PX = 3       # dark runs thinner than this are door swings or hatching, not walls
_WALL_MAX_CHANNEL = 110   # every channel this dark or darker: a wall line or text
_FILL_MIN_SPREAD = 40     # channel spread this wide or wider: a room's colour fill
_PUBLIC_DIR = "ui/react/public"


def classify(r: int, g: int, b: int) -> Pixel:
    """What a plan pixel shows: dark wall/ink, a coloured room fill, or open floor."""
    if max(r, g, b) < _WALL_MAX_CHANNEL:
        return "wall"
    if max(r, g, b) - min(r, g, b) >= _FILL_MIN_SPREAD:
        return "fill"
    return "open"


def violations(floors: list[dict], nodes: list[dict], edges: list[dict], pixel: PixelClass) -> list[str]:
    """Every unwalkable edge, as human-readable lines.

    `pixel(floor_id, x, y)` classifies a plan pixel; it is only called for points
    inside the floor's image bounds. Edges are judged once per node pair, so a
    graph that lists both directions is not reported twice.
    """
    floor_by_id = {f["id"]: f for f in floors}
    node_by_id = {n["id"]: n for n in nodes}
    problems, seen = [], set()
    for n in nodes:
        f = floor_by_id.get(n["floor"]["id"])
        if f and not (0 <= n["imageX"] < f["imageWidth"] and 0 <= n["imageY"] < f["imageHeight"]):
            problems.append(f"MapNode {n['id']}: ({n['imageX']}, {n['imageY']}) is outside the plan image")
    for e in edges:
        a, b = node_by_id.get(e["fromNode"]["id"]), node_by_id.get(e["toNode"]["id"])
        if a is None or b is None or a["floor"]["id"] != b["floor"]["id"]:
            continue  # seed_integrity reports these
        pair = tuple(sorted((a["id"], b["id"])))
        if pair in seen:
            continue
        seen.add(pair)
        f = floor_by_id[a["floor"]["id"]]
        length = math.dist((a["imageX"], a["imageY"]), (b["imageX"], b["imageY"]))
        walls = _wall_crossings(f, a, b, length, pixel)
        if walls:
            problems.append(
                f"edge {a['id']} -- {b['id']}: crosses {walls} wall(s); "
                "move the door node into the doorway gap, or route around the wall"
            )
        if a["kind"] in ROOM_KINDS or b["kind"] in ROOM_KINDS:
            meters = length / f["pixelsPerMeter"]
            if meters > MAX_ROOM_EDGE_METERS:
                problems.append(
                    f"room edge {a['id']} -- {b['id']}: {meters:.0f} m long (max {MAX_ROOM_EDGE_METERS:.0f}); "
                    "put a door node on the corridor and connect the room to it"
                )
            continue
        blocked = _blocked_fraction(f, a, b, length, pixel)
        if blocked is not None and blocked > MAX_BLOCKED_FRACTION:
            problems.append(
                f"corridor edge {a['id']} -- {b['id']}: {blocked:.0%} of its line crosses rooms or walls "
                f"(max {MAX_BLOCKED_FRACTION:.0%}); route it along the corridor"
            )
    return problems


def _wall_crossings(floor: dict, a: dict, b: dict, length: float, pixel: PixelClass) -> int:
    def margin(n: dict) -> float:
        meters = ROOM_END_MARGIN_METERS if n["kind"] in ROOM_KINDS else ENDPOINT_MARGIN_METERS
        return meters * floor["pixelsPerMeter"]

    start, end = margin(a), length - margin(b)
    steps = max(int(length), 1)
    crossings = run = 0
    for k in range(steps + 1):
        d = length * k / steps
        x = a["imageX"] + (b["imageX"] - a["imageX"]) * k / steps
        y = a["imageY"] + (b["imageY"] - a["imageY"]) * k / steps
        inside = 0 <= x < floor["imageWidth"] and 0 <= y < floor["imageHeight"]
        if start <= d <= end and inside and pixel(floor["id"], x, y) == "wall":
            run += 1
            continue
        crossings += run >= MIN_WALL_RUN_PX
        run = 0
    return crossings + (run >= MIN_WALL_RUN_PX)


def _blocked_fraction(floor: dict, a: dict, b: dict, length: float, pixel: PixelClass) -> float | None:
    steps = max(int(length), 1)
    margin = ENDPOINT_MARGIN_METERS * floor["pixelsPerMeter"] * steps / max(length, 1)
    total = blocked = 0
    for k in range(steps + 1):
        if k < margin or k > steps - margin:
            continue
        x = a["imageX"] + (b["imageX"] - a["imageX"]) * k / steps
        y = a["imageY"] + (b["imageY"] - a["imageY"]) * k / steps
        if 0 <= x < floor["imageWidth"] and 0 <= y < floor["imageHeight"]:
            total += 1
            blocked += pixel(floor["id"], x, y) != "open"
    return blocked / total if total else None


def _plan_pixels(root: Path, floors: list[dict]) -> PixelClass:
    """Loads each floor's plan image. Raises ValueError/OSError if one is unusable."""
    planes = {}
    for f in floors:
        path = root / _PUBLIC_DIR / f["planImageUrl"].lstrip("/")
        width, height, px = png_rgb.decode(path.read_bytes())
        if (width, height) != (f["imageWidth"], f["imageHeight"]):
            raise ValueError(f"{path.name} is {width}x{height} but Floor {f['id']} says {f['imageWidth']}x{f['imageHeight']}")
        planes[f["id"]] = (width, px)

    def pixel(floor_id: str, x: float, y: float) -> Pixel:
        width, px = planes[floor_id]
        i = (int(y) * width + int(x)) * 3
        return classify(px[i], px[i + 1], px[i + 2])

    return pixel


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: walkability.py <package-dir>", file=sys.stderr)
        return 2
    root = Path(sys.argv[1])
    try:
        floors, nodes, edges = (
            json.loads((root / f"data/{t}/{t}.json").read_text()) for t in ("Floor", "MapNode", "NavEdge")
        )
        pixel = _plan_pixels(root, floors)
    except (OSError, ValueError, KeyError) as exc:
        print(f"walkability: cannot load seed data or plans: {exc}", file=sys.stderr)
        return 2
    problems = violations(floors, nodes, edges, pixel)
    for p in problems:
        print(f"walkability: {p}")
    if not problems:
        print(f"walkability: OK ({len(edges)} edges stay in corridors)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
