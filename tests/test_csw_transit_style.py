import json
import shutil
import tempfile
import unittest
from pathlib import Path

from ump_core.project import build_project, load_preset

ROOT = Path(__file__).resolve().parents[1]


class CswTransitStyleTests(unittest.TestCase):
    def test_csw_transit_editorial_is_renderable_and_builds(self):
        manifest = load_preset(ROOT, "csw-transit-editorial")
        self.assertEqual(manifest["id"], "csw-transit-editorial")
        self.assertTrue(manifest["_entry_path"].endswith("styles/csw-transit-editorial/ump.html"))
        with tempfile.TemporaryDirectory() as raw:
            project = build_project(ROOT, ROOT / "demo" / "csw-fakta", "csw-transit-editorial", Path(raw) / "stage")
            self.assertTrue((project / "index.html").is_file())
            self.assertTrue((project / "staging.json").is_file())
            html = (project / "index.html").read_text()
            self.assertIn('data-composition-id="root"', html)
            self.assertIn("CSW", html)
            for selector in (".bus", ".elevated", ".skybridge", ".sign", ".route"):
                self.assertIn(selector, html)

    def test_composition_has_no_external_runtime_urls(self):
        manifest = load_preset(ROOT, "csw-transit-editorial")
        html = Path(manifest["_entry_path"]).read_text()
        self.assertNotRegex(html, r"https?://")


if __name__ == "__main__":
    unittest.main()
