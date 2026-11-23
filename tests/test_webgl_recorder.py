import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ump_core import worker
from ump_core.jobs import DomainError


class WebglRecorderRoutingTests(unittest.TestCase):
    """A preset that renders per-frame from an explicit seek must be captured by
    record_frames.mjs, not HyperFrames.

    HyperFrames falls back to screenshot capture on software GL ("BeginFrame did
    not run") and grabs the canvas before it settles, so the whole video came out
    as one frozen scene while the beat cards advanced correctly.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _preset(self, style_id, body):
        d = self.root / "styles" / style_id
        d.mkdir(parents=True)
        (d / "preset.json").write_text(json.dumps(body), encoding="utf-8")
        return d

    def test_webgl_preset_opts_into_the_recorder(self):
        self._preset("stage", {"renderer": "webgl"})
        with mock.patch.object(worker, "ROOT", self.root):
            self.assertTrue(worker._records_own_frames({"style_id": "stage"}))

    def test_ordinary_preset_stays_on_hyperframes(self):
        self._preset("editorial", {"draws_captions": True})
        with mock.patch.object(worker, "ROOT", self.root):
            self.assertFalse(worker._records_own_frames({"style_id": "editorial"}))

    def test_missing_preset_is_not_a_crash(self):
        with mock.patch.object(worker, "ROOT", self.root):
            self.assertFalse(worker._records_own_frames({"style_id": "nope"}))
            self.assertFalse(worker._records_own_frames({}))

    def test_recorder_refuses_an_empty_capture(self):
        directory = self.root / "run"
        directory.mkdir()
        calls = []

        def fake_command(argv, cwd=None):
            calls.append(argv)
            # the recorder "succeeds" but wrote nothing

        with self.assertRaisesRegex(DomainError, "RENDER_FAILED"):
            worker._record_frames(directory, self.root, fake_command)
        self.assertTrue(calls and "record_frames.mjs" in " ".join(map(str, calls[0])))

    def test_recorder_encodes_the_captured_frames(self):
        directory = self.root / "run"
        raw = directory / "framesraw"
        raw.mkdir(parents=True)
        (raw / "f00000.png").write_bytes(b"png")

        def fake_command(argv, cwd=None):
            if "libx264" in argv:
                (directory / "frames.mp4").write_bytes(b"mp4")

        worker._record_frames(directory, self.root, fake_command)
        self.assertTrue((directory / "frames.mp4").is_file())

    def test_a_finished_capture_is_not_recorded_again(self):
        """An interrupted encode leaves the PNGs; re-encoding is enough and the
        capture (minutes of GPU time) must not be repeated."""
        directory = self.root / "run"
        raw = directory / "framesraw"
        raw.mkdir(parents=True)
        project = self.root / "project"
        project.mkdir()
        (project / "meta.json").write_text(json.dumps({"fps": 30, "duration_s": 0.1}))
        for i in range(3):
            (raw / f"f{i:05d}.png").write_bytes(b"png")

        calls = []

        def fake_command(argv, cwd=None):
            calls.append(argv)
            if "libx264" in argv:
                (directory / "frames.mp4").write_bytes(b"mp4")

        with mock.patch.object(worker, "_frames_intact", return_value=False):
            worker._record_frames(directory, project, fake_command)
        joined = [" ".join(map(str, c)) for c in calls]
        self.assertFalse(any("record_frames.mjs" in j for j in joined),
                         "capture was repeated although every frame was already present")
        self.assertTrue(any("libx264" in j for j in joined))

    def test_a_partial_capture_is_refused_rather_than_encoded(self):
        directory = self.root / "run"
        raw = directory / "framesraw"
        raw.mkdir(parents=True)
        project = self.root / "project"
        project.mkdir()
        (project / "meta.json").write_text(json.dumps({"fps": 30, "duration_s": 1.0}))
        (raw / "f00000.png").write_bytes(b"png")

        def fake_command(argv, cwd=None):
            pass  # capture "runs" but produces nothing new

        with self.assertRaisesRegex(DomainError, "recorder captured"):
            worker._record_frames(directory, project, fake_command)


if __name__ == "__main__":
    unittest.main()
