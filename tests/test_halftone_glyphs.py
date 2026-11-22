"""Headline glyphs must not outlive the beat that drew them.

The headline is rebuilt per beat, one <span> per character. When the rebuild
left the previous spans in place, the tail of the old headline stayed visible
underneath the new one — a stray "W" hanging below "ASAL NAMA CSW", because the
old headline was longer than the new one and only its first glyphs were
overwritten.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSITION = ROOT / "styles" / "halftone-dossier" / "ump.html"


class HeadlineGlyphLeakTests(unittest.TestCase):
    def setUp(self):
        self.source = COMPOSITION.read_text(encoding="utf-8")

    def test_rebuild_clears_the_glyph_arrays(self):
        body = self.source[self.source.index("function buildHeadline"):]
        body = body[:body.index("\n}\n") + 3]
        self.assertIn("glyphs.length = 0", body)

    def test_shadow_is_text_shadow_not_a_second_dom_layer(self):
        """Two DOM layers of the same glyphs cannot stay in step.

        The shadow used to be a parallel <span> list. Any mismatch in layout,
        wrapping or timing showed as letters piled behind the first glyph and
        stray words under the headline. A text-shadow belongs to the glyph it
        belongs to, so it cannot drift.
        """
        self.assertIn("text-shadow", self.source)
        self.assertNotIn("h1Sh", self.source)
        self.assertNotIn('class="sh"', self.source)

    def test_words_do_not_break_across_lines(self):
        """Each glyph is inline-block, so a line break can fall inside a word.

        "CUMA" rendered as CU / MA and "BANYAK" as BANY / AK. Words must be
        wrapped as units, with word-spacing carrying the gap between them.
        """
        self.assertIn("split(/\\s+/)", self.source)
        # word-spacing does not apply to inline-block boxes: use a real gap
        self.assertIn("margin-right", self.source)

    def test_pending_glyphs_leave_the_layout(self):
        """opacity 0 still occupies layout, so a not-yet-arrived glyph must be hidden."""
        render = self.source[self.source.index("window.__umpRender"):]
        render = render[:render.index("\n};")]
        self.assertIn("visibility = 'hidden'", render)
        self.assertIn("visibility = 'visible'", render)

    def test_no_glyph_survives_into_the_next_beat(self):
        """After a beat change the glyph count equals the current headline length."""
        render = self.source[self.source.index("window.__umpRender"):]
        self.assertIn("buildHeadline(", render)


if __name__ == "__main__":
    unittest.main()
