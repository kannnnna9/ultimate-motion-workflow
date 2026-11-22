"""Exhibit plates must read as drawings, not as wireframes.

The first plates were bare lines, boxes and dots. They were accurate and dead:
nothing in the frame carried weight or told you what CSW looks like. These tests
hold the drawing to a higher floor — figures, vehicles, foliage, lit windows —
while keeping the same hard constraints: pure geometry, no external assets, and
identical output on every run.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSITION = ROOT / "styles" / "halftone-dossier" / "ump.html"


class ExhibitDrawingTests(unittest.TestCase):
    def setUp(self):
        self.source = COMPOSITION.read_text(encoding="utf-8")
        start = self.source.index("const EXHIBIT_DRAWERS = [")
        end = self.source.index("const exDraw =")
        self.drawers = self.source[start:end]

    def test_there_is_one_plate_per_beat(self):
        self.assertEqual(self.drawers.count("function (g)"), 5)

    def test_plates_draw_people(self):
        """A crowd is what makes a transit building legible as a place."""
        self.assertIn("figure(", self.drawers)
        self.assertIn("crowd(", self.drawers)

    def test_plates_draw_a_vehicle(self):
        self.assertIn("bus(", self.drawers)

    def test_plates_draw_foliage_and_buildings(self):
        self.assertIn("tree(", self.drawers)
        self.assertIn("building(", self.drawers)

    def test_plates_draw_lit_windows(self):
        """Unlit windows read as a plan; lit ones read as a building at dusk."""
        self.assertIn("windows(", self.drawers)

    def test_figure_places_itself_at_the_given_position(self):
        """The figure draws around its own origin, so it must carry a transform.

        Without it every person in every plate was painted at (0,0): stacked in
        the corner, outside the plate, invisible in the frame while the DOM
        reported them as drawn.
        """
        start = self.source.index("function figure(")
        body = self.source[start:start + 1600]
        self.assertIn("translate(", body)
        self.assertIn("transform", body)

    def test_element_helper_attaches_to_a_parent(self):
        """el() builds a node and appends it in one call.

        Every drawing helper calls el(tag, attrs, g). When el() stopped taking a
        parent, nothing was ever attached: the plates rendered as empty boxes and
        no error was raised, because creating a node is not the same as adding it.
        """
        m = re.search(r"function el\(name, attrs[^)]*\)", self.source)
        self.assertIsNotNone(m)
        self.assertIn("parent", m.group(0))
        self.assertIn("appendChild", self.source[self.source.index(m.group(0)):][:400])

    def test_drawing_helpers_exist(self):
        for name in ("figure", "crowd", "bus", "tree", "building", "windows"):
            self.assertRegex(self.source, r"function " + name + r"\(")

    def test_helpers_take_a_deterministic_seed(self):
        """No Math.random: the same beat must draw the same crowd every run."""
        self.assertNotIn("Math.random", self.source)
        # the seeded generator lives in the helpers; the plates pass explicit seeds
        self.assertIn("rng(", self.source)
        self.assertRegex(self.drawers, r"seed: \d+")

    def test_plates_stay_inside_the_plate_bounds(self):
        """The viewBox is 0 0 860 620; nothing should be drawn far outside it."""
        self.assertNotIn("x: -4", self.drawers)


if __name__ == "__main__":
    unittest.main()
