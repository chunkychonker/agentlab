"""Acceptance tests for the TardyAI platform-parity checks. Run: python3 -I projects/tardyai/checks/test_checks.py"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import c3_parity  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
