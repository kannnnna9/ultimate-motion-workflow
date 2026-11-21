import json
import shutil
import tempfile
import unittest
from pathlib import Path

from ump_core.project import DomainError, build_project, frame_at, load_preset

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "demo" / "explain-9x16"


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dst = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_episode_drives_two_styles_and_literal_text(self):
        first = build_project(ROOT, FIXTURE, "dark-editorial", self.dst / "dark")
        second = build_project(ROOT, FIXTURE, "kinetic-bold", self.dst / "kinetic")
        dark = json.loads((first / "staging.json").read_text())
        kinetic = json.loads((second / "staging.json").read_text())
        self.assertEqual(dark["cues"], kinetic["cues"])
        self.assertNotEqual(dark["layout"], kinetic["layout"])
        self.assertNotEqual(dark["motion"], kinetic["motion"])
        self.assertEqual(dark["beats"][0]["on_screen_text"], "BERHENTI DULU")

        altered = self.dst / "explain-9x16"
        shutil.copytree(FIXTURE, altered)
        naskah = json.loads((altered / "naskah.json").read_text())
        naskah["beats"][0]["on_screen_text"] = "KOPI JAM LIMA <b>TEKS</b>"
        (altered / "naskah.json").write_text(json.dumps(naskah), encoding="utf-8")
        staged = build_project(ROOT, altered, "dark-editorial", self.dst / "literal")
        html = (staged / "index.html").read_text()
        self.assertIn("KOPI JAM LIMA &lt;b&gt;TEKS&lt;/b&gt;", html)
        self.assertNotIn("<b>TEKS</b>", html)

    def test_shared_cue_clock_drives_visual_and_sfx(self):
        self.assertEqual(frame_at(8560), 257)
        self.assertEqual(frame_at(9200), 276)
        staged = build_project(ROOT, FIXTURE, "kinetic-bold", self.dst / "clock")
        data = json.loads((staged / "staging.json").read_text())
        c007 = next(c for c in data["cues"] if c["id"] == "c007")
        self.assertEqual(c007["cue_ms"], 8560)
        self.assertEqual(c007["visual_frame"], 257)
        self.assertEqual(c007["sfx_ms"], 8560)
        self.assertEqual(c007["sfx_offset_frames"], 1)
        html = (staged / "index.html").read_text()
        self.assertIn('visual_frame', html)
        self.assertNotIn('frameAt(c.visual_frame)', html)
        self.assertIn('c008', html)

    def test_entry_template_is_used_and_duration_follows_episode(self):
        repo = self.dst / "repo"
        shutil.copytree(ROOT / "styles", repo / "styles")
        custom = repo / "styles" / "dark-editorial" / "custom-entry.html"
        custom.write_text("CUSTOM_ENTRY <main data-duration=\"__UMP_DURATION__\">__UMP_CARDS__ __UMP_EPISODE__ __UMP_TIMELINE__</main>", encoding="utf-8")
        manifest_path = repo / "styles" / "dark-editorial" / "preset.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["entry"] = "custom-entry.html"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        (repo / "styles" / "registry.json").write_text((ROOT / "styles" / "registry.json").read_text(), encoding="utf-8")
        out = build_project(repo, FIXTURE, "dark-editorial", self.dst / "entry")
        html = (out / "index.html").read_text()
        self.assertTrue(html.startswith("CUSTOM_ENTRY"))
        self.assertIn("BERHENTI DULU", html)
        self.assertIn('data-duration="15"', html)

    def test_changed_timing_updates_every_cue_and_duration(self):
        episode = self.dst / "explain-9x16-timed"
        shutil.copytree(FIXTURE, episode)
        cue = json.loads((episode / "cue.json").read_text())
        cue["episode"] = episode.name
        cue["cues"][6]["t_ms"] = 9200
        cue["cues"][7]["t_ms"] = 10840
        naskah = json.loads((episode / "naskah.json").read_text())
        naskah["episode"] = episode.name
        brand = json.loads((episode / "brand.json").read_text())
        brand["episode"] = episode.name
        (episode / "brand.json").write_text(json.dumps(brand), encoding="utf-8")
        (episode / "cue.json").write_text(json.dumps(cue), encoding="utf-8")
        out = build_project(ROOT, episode, "dark-editorial", self.dst / "timed-out")
        data = json.loads((out / "staging.json").read_text())
        self.assertEqual(next(c for c in data["cues"] if c["id"] == "c007")["visual_frame"], 276)
        self.assertIn('data-at-ms="9200"', (out / "index.html").read_text())
        self.assertIn('data-duration="15.64"', (out / "index.html").read_text())

    def test_invalid_preset_is_rejected(self):
        with self.assertRaisesRegex(DomainError, "MANIFEST_ID"):
            load_preset(ROOT, "broken-id")
        fake_root = self.dst / "fake-root"
        package = fake_root / "styles" / "bad"
        package.mkdir(parents=True)
        manifest = json.loads((ROOT / "styles" / "kinetic-bold" / "preset.json").read_text())
        manifest["id"] = "bad"
        manifest["assets"] = ["missing.svg"]
        (package / "preset.json").write_text(json.dumps(manifest), encoding="utf-8")
        (package / "index.html").write_text("<html></html>", encoding="utf-8")
        (fake_root / "styles" / "registry.json").write_text(json.dumps({"styles": [{"id": "bad", "manifest": "styles/bad/preset.json"}]}), encoding="utf-8")
        with self.assertRaisesRegex(DomainError, "ASSET_NOT_FOUND"):
            load_preset(fake_root, "bad")

    def test_episode_schema_errors_are_stable(self):
        episode = self.dst / "bad-episode"
        shutil.copytree(FIXTURE, episode)
        for filename in ("brand.json", "cue.json", "naskah.json"):
            path = episode / filename
            value = json.loads(path.read_text())
            value["episode"] = episode.name
            path.write_text(json.dumps(value), encoding="utf-8")
        cue = json.loads((episode / "cue.json").read_text())
        cue["cues"][0].pop("t_ms")
        (episode / "cue.json").write_text(json.dumps(cue), encoding="utf-8")
        with self.assertRaisesRegex(DomainError, "EPISODE_SCHEMA:cue.json"):
            build_project(ROOT, episode, "dark-editorial", self.dst / "bad-out")


if __name__ == "__main__":
    unittest.main()
