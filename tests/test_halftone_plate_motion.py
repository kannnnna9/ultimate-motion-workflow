"""The exhibit plates must move, not just sit there.

A drawn plate that never changes is a diagram. The rule for this style is that
nothing in the frame is dead: people walk, the bus pulls in and its doors open,
the crowd shifts. These tests hold the plates to that, and hold the result to
the same determinism contract as the rest of the engine — the plate at time t
must be the same on every run, and must not depend on which frames came before.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSITION = ROOT / "styles" / "halftone-dossier" / "ump.html"


class PlateAnimationTests(unittest.TestCase):
    def setUp(self):
        self.source = COMPOSITION.read_text(encoding="utf-8")
        start = self.source.index("const EXHIBIT_DRAWERS = [")
        end = self.source.index("const exDraw =")
        self.plates = self.source[start:end]

    def test_each_plate_is_a_function_of_time(self):
        """A plate returns an updater instead of drawing once."""
        self.assertEqual(self.plates.count("return function (t, t0)"), 5)

    def test_the_updater_runs_every_frame(self):
        render = self.source[self.source.index("window.__umpRender"):]
        render = render[:render.index("\n};")]
        self.assertIn("plateUpdate(", render)

    def test_people_walk(self):
        """A walk cycle, not a sway: legs or body offset driven by t."""
        self.assertIn("walk", self.plates)
        self.assertRegex(self.source, r"function walk\(people, t\)")

    def test_the_bus_arrives_and_opens_its_doors(self):
        self.assertIn("busDoor", self.plates)
        self.assertRegex(self.source, r"function busDoor\(")

    def test_plates_do_not_read_the_wall_clock(self):
        self.assertNotIn("performance.now", self.source)
        self.assertNotIn("Date.now", self.source)
        self.assertNotIn("requestAnimationFrame", self.source)

    def test_still_shape_is_built_once_not_per_frame(self):
        """Only the moving parts are updated; the geometry is built on beat change."""
        self.assertIn("plateIndex", self.source)
        self.assertIn("plateUpdate", self.source)

    def test_entrances_finish_early_in_the_beat(self):
        """p spans the whole beat (6 to 10 s), so an arrival scaled to p is invisible.

        The bus moved 0.46 px instead of 46 because its 0.1-0.55 window of a
        nine-second beat is four seconds long. Entrances get their own clock.
        """
        self.assertIn("function beatIn(", self.source)
        self.assertIn("beatIn(t, t0)", self.plates)
        self.assertNotIn("seg(p, 0.10, 0.55)", self.plates)

    def test_walking_moves_enough_to_see(self):
        """A 3 px bob over 2.5 s reads as a still frame."""
        walk = self.source[self.source.index("function walk("):]
        walk = walk[:walk.index("\n}\n")]
        self.assertIn("Math.abs(Math.sin(w)) * 7", walk)
        self.assertIn("f.node.setAttribute('transform'", walk)

    def test_moving_parts_are_addressable(self):
        """The updater needs handles on the parts it moves, not a DOM query."""
        self.assertIn("people.push", self.plates)
        self.assertIn("leafA", self.plates)


if __name__ == "__main__":
    unittest.main()
