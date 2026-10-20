"""Acceptance tests for the TardyAI platform-parity checks. Run: python3 -I projects/tardyai/checks/test_checks.py"""

import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import c3_parity  # noqa: E402
import png_rgb  # noqa: E402
import walkability  # noqa: E402
import seed_integrity  # noqa: E402

NAVROUTER_TYP = """
@js(env='server')
type NavRouter {
  /** doc */
  findRoute: function(startNodeId: !string, destNodeId: !string, stepFree: boolean): NavRoute js
  nearestNode: function(floorId: !string, imageX: !double, imageY: !double): MapNode js
}
"""


class C3Parity(unittest.TestCase):
    def _pkg(self, typ: str, js: str | None) -> Path:
        d = Path(tempfile.mkdtemp())
        (d / "NavRouter.c3typ").write_text(typ)
        if js is not None:
            (d / "NavRouter.js").write_text(js)
        return d

    def test_parses_declared_js_methods(self):
        self.assertEqual(c3_parity.declared_methods(NAVROUTER_TYP), {"findRoute": "js", "nearestNode": "js"})

    def test_complete_implementation_has_no_gaps(self):
        js = "function findRoute(a, b, c) {}\nfunction nearestNode(f, x, y) {}\n"
        self.assertEqual(c3_parity.gaps(self._pkg(NAVROUTER_TYP, js)), [])

    def test_missing_function_is_reported(self):
        gaps = c3_parity.gaps(self._pkg(NAVROUTER_TYP, "function findRoute(a, b, c) {}\n"))
        self.assertEqual(len(gaps), 1)
        self.assertIn("NavRouter.nearestNode", gaps[0])

    def test_missing_source_file_is_reported(self):
        gaps = c3_parity.gaps(self._pkg(NAVROUTER_TYP, None))
        self.assertEqual(len(gaps), 1)
        self.assertIn("NavRouter.js is missing", gaps[0])


def _node(nid, floor, key=None):
    n = {"id": nid, "floor": {"id": floor}}
    if key:
        n["connectorKey"] = key
    return n


def _edge(eid, a, b, seconds=5.0):
    return {"id": eid, "fromNode": {"id": a}, "toNode": {"id": b}, "seconds": seconds}


FLOORS = [{"id": "f1"}, {"id": "f2"}]


class SeedIntegrity(unittest.TestCase):
    def test_consistent_graph_passes(self):
        nodes = [_node("a", "f1", "elev"), _node("b", "f1"), _node("c", "f2", "elev")]
        self.assertEqual(seed_integrity.violations(FLOORS, nodes, [_edge("e", "a", "b")]), [])

    def test_edge_to_unknown_node_fails(self):
        out = seed_integrity.violations(FLOORS, [_node("a", "f1")], [_edge("e", "a", "ghost")])
        self.assertTrue(any("not MapNodes" in p for p in out))

    def test_cross_floor_edge_fails(self):
        out = seed_integrity.violations(FLOORS, [_node("a", "f1"), _node("c", "f2")], [_edge("e", "a", "c")])
        self.assertTrue(any("spans floors" in p for p in out))

    def test_single_floor_connector_fails(self):
        out = seed_integrity.violations(FLOORS, [_node("a", "f1", "stairs-w"), _node("b", "f1", "stairs-w")], [])
        self.assertTrue(any("stairs-w" in p for p in out))

    def test_non_positive_seconds_fails(self):
        out = seed_integrity.violations(FLOORS, [_node("a", "f1"), _node("b", "f1")], [_edge("e", "a", "b", 0)])
        self.assertTrue(any("seconds" in p for p in out))


# A 200x100 plan at 10 px/m: open floor, a room fill at x 80..120 y 0..60, a 4 px wall at
# x 60..64 for y < 70 with a doorway gap at y 40..50, and a 2 px door-swing line at y 75..77.
PLAN = [{"id": "f1", "imageWidth": 200, "imageHeight": 100, "pixelsPerMeter": 10.0}]


def _plan_pixel(floor_id, x, y):
    if 60 <= x < 64 and y < 70 and not 40 <= y < 50:
        return "wall"
    if 130 <= x and 75 <= y < 77:
        return "wall"
    return "fill" if 80 <= x < 120 and y < 60 else "open"


def _at(nid, x, y, kind="hallway"):
    return {"id": nid, "floor": {"id": "f1"}, "kind": kind, "imageX": x, "imageY": y}


class Walkability(unittest.TestCase):
    def _check(self, nodes, pairs):
        edges = [_edge(f"e{i}", a, b) for i, (a, b) in enumerate(pairs)]
        return walkability.violations(PLAN, nodes, edges, _plan_pixel)

    def test_corridor_edge_in_open_floor_passes(self):
        self.assertEqual(self._check([_at("a", 10, 80), _at("b", 190, 80)], [("a", "b")]), [])

    def test_corridor_edge_through_a_room_fails(self):
        out = self._check([_at("a", 70, 30), _at("b", 190, 30)], [("a", "b")])
        self.assertEqual(len(out), 1)
        self.assertIn("corridor edge a -- b", out[0])

    def test_both_directions_reported_once(self):
        out = self._check([_at("a", 70, 30), _at("b", 190, 30)], [("a", "b"), ("b", "a")])
        self.assertEqual(len(out), 1)

    def test_crossing_only_near_an_endpoint_passes(self):
        # The node sits inside the fill (an elevator box); only its first metre is blocked.
        self.assertEqual(self._check([_at("a", 115, 55, "elevator"), _at("b", 115, 95)], [("a", "b")]), [])

    def test_short_room_edge_through_its_own_fill_passes(self):
        self.assertEqual(self._check([_at("r", 100, 30, "room"), _at("d", 100, 70)], [("r", "d")]), [])

    def test_long_room_edge_fails_even_over_open_floor(self):
        out = self._check([_at("r", 10, 80, "room"), _at("h", 190, 80)], [("r", "h")])
        self.assertEqual(len(out), 1)
        self.assertIn("door node", out[0])

    def test_corridor_edge_through_a_thin_wall_fails(self):
        # The wall is only 6% of this edge, under the fill limit; it still may not be crossed.
        out = self._check([_at("a", 10, 65), _at("b", 110, 65)], [("a", "b")])
        self.assertEqual(len(out), 1)
        self.assertIn("crosses 1 wall", out[0])

    def test_room_edge_through_the_doorway_gap_passes(self):
        self.assertEqual(self._check([_at("r", 20, 45, "room"), _at("d", 100, 45, "door")], [("r", "d")]), [])

    def test_room_edge_through_a_wall_fails(self):
        out = self._check([_at("r", 20, 30, "room"), _at("d", 100, 30, "door")], [("r", "d")])
        self.assertEqual(len(out), 1)
        self.assertIn("doorway gap", out[0])

    def test_wall_beside_the_room_node_is_ignored(self):
        # Within 4 m of the room node: the outline of the room's label box, not a wall to cross.
        self.assertEqual(self._check([_at("r", 40, 30, "room"), _at("d", 100, 30, "door")], [("r", "d")]), [])

    def test_line_thinner_than_a_wall_is_not_a_crossing(self):
        self.assertEqual(self._check([_at("a", 160, 10), _at("b", 160, 99)], [("a", "b")]), [])

    def test_node_outside_the_plan_fails(self):
        out = self._check([_at("a", 250, 50)], [])
        self.assertTrue(any("outside the plan" in p for p in out))

    def test_classifies_plan_colours(self):
        self.assertEqual(walkability.classify(255, 255, 255), "open")
        self.assertEqual(walkability.classify(200, 200, 200), "open")
        self.assertEqual(walkability.classify(30, 30, 30), "wall")
        self.assertEqual(walkability.classify(250, 220, 150), "fill")

    def test_floor_tint_is_open_where_it_applies(self):
        self.assertEqual(walkability.classify(*PINK, open_tint=PINK), "open")
        self.assertEqual(walkability.classify(*PURPLE, open_tint=PURPLE), "open")
        self.assertEqual(walkability.classify(255, 170, 212, open_tint=PINK), "open")  # antialiased edge

    def test_same_colour_outside_a_tinted_area_is_a_room(self):
        # L3 offices 3301-3307 are filled with the L1 Commons purple.
        self.assertEqual(walkability.classify(*PURPLE), "fill")
        self.assertEqual(walkability.classify(*PURPLE, open_tint=PINK), "fill")

    def test_room_fills_stay_blocked_inside_a_tinted_area(self):
        self.assertEqual(walkability.classify(179, 255, 155, open_tint=PINK), "fill")  # classroom green
        self.assertEqual(walkability.classify(0, 191, 255, open_tint=PINK), "fill")    # elevator cyan
        self.assertEqual(walkability.classify(255, 59, 59, open_tint=PINK), "fill")    # restroom red

    def test_ink_inside_a_tinted_area_is_still_wall(self):
        self.assertEqual(walkability.classify(*BLACK, open_tint=PINK), "wall")

    def test_tint_applies_only_on_its_plan_inside_the_commons(self):
        self.assertEqual(walkability.tint_at("floor_p1.png", 800, 450), PURPLE)
        self.assertEqual(walkability.tint_at("floor_p2.png", 800, 450), PINK)
        self.assertEqual(walkability.tint_at("floor_p3.png", 800, 450), PINK)
        self.assertIsNone(walkability.tint_at("floor_p3.png", 1310, 650))  # office 3301
        self.assertIsNone(walkability.tint_at("floor_p2.png", 180, 400))   # offices 2111-2117
        self.assertIsNone(walkability.tint_at("floor_p4.png", 800, 450))   # no tint on an unknown plan


PINK, PURPLE, BLACK = (255, 159, 207), (159, 126, 255), (0, 0, 0)


# A 200x100 plan at 10 px/m tinted pink, with a 4 px black wall at x 100..104 for y < 50.
def _tinted_pixel(floor_id, x, y):
    rgb = BLACK if 100 <= x < 104 and y < 50 else PINK
    return walkability.classify(*rgb, open_tint=PINK)


class WalkabilityOverFloorTint(unittest.TestCase):
    def _check(self, nodes):
        return walkability.violations(PLAN, nodes, [_edge("e", "a", "b")], _tinted_pixel)

    def test_corridor_edge_across_the_tint_passes(self):
        self.assertEqual(self._check([_at("a", 10, 80), _at("b", 190, 80)]), [])

    def test_wall_inside_the_tint_is_still_a_crossing(self):
        out = self._check([_at("a", 10, 30), _at("b", 190, 30)])
        self.assertEqual(len(out), 1)
        self.assertIn("crosses 1 wall", out[0])


def _png(width, height, rows, filt=0):
    raw = b"".join(bytes([filt]) + r for r in rows)
    chunk = lambda k, d: struct.pack(">I", len(d)) + k + d + struct.pack(">I", zlib.crc32(k + d))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class PngDecode(unittest.TestCase):
    def test_decodes_unfiltered_rgb(self):
        rows = [bytes([1, 2, 3, 4, 5, 6]), bytes([7, 8, 9, 10, 11, 12])]
        self.assertEqual(png_rgb.decode(_png(2, 2, rows)), (2, 2, b"".join(rows)))

    def test_decodes_sub_filter(self):
        # Sub stores each byte minus the pixel to its left: decoded row is 10,20,30 then 15,25,35.
        self.assertEqual(png_rgb.decode(_png(2, 1, [bytes([10, 20, 30, 5, 5, 5])], filt=1))[2],
                         bytes([10, 20, 30, 15, 25, 35]))

    def test_rejects_non_png(self):
        with self.assertRaises(ValueError):
            png_rgb.decode(b"GIF89a....")


if __name__ == "__main__":
    unittest.main()
