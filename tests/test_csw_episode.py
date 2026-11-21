import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EPISODE = ROOT / "demo" / "csw-fakta"


class CswEpisodeTests(unittest.TestCase):
    def test_episode_validates_and_keeps_approved_fact_refs(self):
        result = subprocess.run(["python3", "templates/episode/validate.py", str(EPISODE)], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        brand = json.loads((EPISODE / "brand.json").read_text())
        naskah = json.loads((EPISODE / "naskah.json").read_text())
        cue = json.loads((EPISODE / "cue.json").read_text())
        self.assertEqual(brand["style_id"], "csw-transit-editorial")
        beats = {beat["id"] for beat in naskah["beats"]}
        self.assertTrue(beats)
        self.assertTrue(all(item["beat"] in beats for item in cue["cues"]))
        source_ids = {source["id"] for source in naskah["sources"]}
        self.assertTrue(all(ref in source_ids for beat in naskah["beats"] for ref in beat["fact_refs"]))


if __name__ == "__main__":
    unittest.main()
