import json
import tempfile
import unittest
from pathlib import Path

from scripts.verify_ump import inspect_media, verify_checkout
from ump_core.project import DomainError, build_project, load_preset

ROOT = Path(__file__).resolve().parents[1]
EPISODE = ROOT / "demo" / "explain-9x16"


class AcceptanceTests(unittest.TestCase):
    def test_minimal_explainer_is_registry_driven_and_buildable(self):
        preset = load_preset(ROOT, "minimal-explainer")
        self.assertEqual(preset["id"], "minimal-explainer")
        with tempfile.TemporaryDirectory() as raw:
            staged = build_project(ROOT, EPISODE, "minimal-explainer", Path(raw) / "stage")
            self.assertTrue((staged / "index.html").is_file())

    def test_missing_preset_asset_is_rejected_without_python_changes(self):
        with tempfile.TemporaryDirectory() as raw:
            repo = Path(raw) / "repo"
            (repo / "styles" / "fixture-paper").mkdir(parents=True)
            (repo / "styles" / "fixture-paper" / "preset.json").write_text(json.dumps({
                "id": "fixture-paper", "version": 1, "entry": "index.html",
                "assets": ["missing.js"], "palette": {}, "typography": {}, "layout": {},
                "motion": {}, "caption_safe_area": {}, "sources": []
            }))
            (repo / "styles" / "fixture-paper" / "index.html").write_text("ok")
            (repo / "styles" / "registry.json").write_text(json.dumps({"styles": [{"id": "fixture-paper", "manifest": "styles/fixture-paper/preset.json"}]}))
            with self.assertRaisesRegex(DomainError, "ASSET_NOT_FOUND"):
                load_preset(repo, "fixture-paper")

    def test_media_inspection_rejects_silent_audio(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "silent.mp4"
            import subprocess
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=16x16:d=1", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "1", "-r", "30", "-c:v", "libx264", "-c:a", "aac", str(path)], check=True)
            with self.assertRaisesRegex(ValueError, "silent"):
                inspect_media(path, expected_width=16, expected_height=16, expected_fps=30)


if __name__ == "__main__":
    unittest.main()
