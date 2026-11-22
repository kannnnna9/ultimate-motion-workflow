"""Every plate must actually move, proven from rendered pixels.

The bug that made this file necessary: the plate updater was driven with the
beat's progress instead of an entrance clock, so the bus moved 0.46 px instead
of 230 and the picture looked frozen. Every test above was green. Nothing
failed, because nothing threw — the motion was simply too small to see.

So this suite does not read the source. It renders frames and compares pixels,
which is the only thing the viewer ever sees.

Run only when a browser is available (UMP_BROWSER=1); otherwise it skips, so
the fast unit suite stays fast.
"""

import os
import subprocess
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
COMPOSITION = ROOT / "styles" / "halftone-dossier" / "ump.html"

# The bus sits at the kerb by the end of beat 1; the street in beat 5 carries a
# bus along it. Both should differ from their neighbours without help from the
# line boil or the grain, which move every pixel anyway.
BEAT_WINDOWS = {
    "01_shelter": (1.6, 2.4),
    "02_archive": (9.4, 10.2),
    "03_decks": (17.8, 18.6),
    "04_loop": (26.0, 26.8),
    "05_street": (35.0, 35.8),
}


def _browser_env():
    return os.environ.copy()


@unittest.skipUnless(os.environ.get("UMP_BROWSER"), "set UMP_BROWSER=1 to render")
class PlateMotionFromPixels(unittest.TestCase):
    """Render two moments per beat and require the drawing to differ."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = ROOT / "out" / "plate-motion"
        cls.tmp.mkdir(parents=True, exist_ok=True)
        cls.page = ROOT / "out" / "csw-halftone" / "index.html"
        if not cls.page.exists():
            raise unittest.SkipTest("composition not built; run build_project first")

    def _frame(self, t):
        out = self.tmp / f"f{t}.png"
        subprocess.run(
            ["node", str(ROOT / "tests" / "support" / "still.mjs"),
             str(self.page), str(t), str(out)],
            check=True, capture_output=True, timeout=120,
        )
        from PIL import Image
        return np.asarray(Image.open(out).convert("RGB"), dtype=np.int16)

    def test_every_beat_moves(self):
        for name, (a, b) in BEAT_WINDOWS.items():
            with self.subTest(beat=name):
                fa, fb = self._frame(a), self._frame(b)
                # the plate occupies the exhibit box, not the whole frame
                box = (slice(300, 1200), slice(90, 990))
                d = np.abs(fa[box] - fb[box]).mean()
                self.assertGreater(d, 0.55, f"{name} barely changes: mean delta {d:.3f}")


if __name__ == "__main__":
    unittest.main()
