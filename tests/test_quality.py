from __future__ import annotations

import math
import subprocess
import tempfile
import unittest
from pathlib import Path

from ump_core.quality import assess_motion, build_review, measure_motion


class QualityMotionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._temp = tempfile.TemporaryDirectory(prefix="ump-quality-")
        cls.addClassCleanup(cls._temp.cleanup)
        cls.root = Path(cls._temp.name)

    def make_video(self, name, *, articulation=False, confidence=1.0, shot_cut=False,
                   blank_index=None, rotate=False):
        folder = self.root / name
        folder.mkdir()
        width, height = 112, 72
        tracks = []
        for frame_index in range(9):
            cx, cy = 24 + frame_index * 3, 36
            angle = (90 if frame_index >= 4 else 0) if rotate else 0
            pixels = [[38 + (((x + frame_index * 2) // 6 + y // 5) % 2) * 16
                       for x in range(width)] for y in range(height)]

            def draw_local(x, y, value):
                radians = math.radians(angle)
                px = round(cx + math.cos(radians) * x - math.sin(radians) * y)
                py = round(cy + math.sin(radians) * x + math.cos(radians) * y)
                if 0 <= px < width and 0 <= py < height:
                    pixels[py][px] = value

            for y in range(-4, 5):
                for x in range(-6, 7):
                    draw_local(x, y, 95 + ((x + y) % 2) * 32)

            if articulation and frame_index in (2, 3, 4, 5, 6):
                pose = (frame_index - 2) % 3
                wing_x, wing_y = ((9, -5), (13, -8), (17, -3))[pose]
            else:
                wing_x, wing_y = 10, -5
            for y in range(7):
                for x in range(8):
                    draw_local(wing_x + x, wing_y + y,
                               205 if (x + y) % 2 else 238)

            for y in range(-4, 5):
                for x in range(-15, -8):
                    draw_local(x, y, 72 + ((x - y) % 2) * 35)

            if frame_index == blank_index:
                pixels = [[12 for _ in range(width)] for _ in range(height)]

            ppm = folder / f"frame{frame_index:03d}.ppm"
            with ppm.open("wb") as output:
                output.write(f"P6\n{width} {height}\n255\n".encode("ascii"))
                output.write(bytes(channel for row in pixels for value in row
                                   for channel in (value, value, value)))
            tracks.append({
                "time_ms": frame_index * 250,
                "anchor": [cx, cy],
                "angle_deg": angle,
                "shot": "B" if shot_cut and frame_index >= 5 else "A",
                "confidence": confidence,
            })

        video = folder / "fixture.mkv"
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-framerate", "4",
            "-i", str(folder / "frame%03d.ppm"), "-c:v", "ffv1", "-pix_fmt", "gray",
            "-threads", "1", str(video),
        ], check=True, capture_output=True, text=True)
        calibration = {
            "locked": True,
            "body_track": tracks,
            "wing_roi": [8, -8, 18, 15],
            "reference_roi": [-15, -4, 7, 9],
            "action": {"before_ms": 250, "start_ms": 500, "end_ms": 1500,
                       "after_ms": 1750},
            "sample_tolerance_ms": 30,
            "min_crop_contrast": 4.0,
            "movement_threshold": 0.04,
            "still_threshold": 0.025,
        }
        return video, calibration

    def test_translation_and_background_motion_do_not_hide_frozen_wing(self):
        video, calibration = self.make_video("translated-frozen")
        measurements = measure_motion(video, calibration)
        self.assertTrue(measurements["reliable"], measurements)
        self.assertEqual(measurements["source"], "decoded_pixels")
        self.assertGreater(measurements["body_displacement_px"], 0)
        self.assertEqual([sample["pts_ms"] for sample in measurements["samples"]],
                         [250.0, 500.0, 1000.0, 1500.0, 1750.0])
        self.assertLessEqual(measurements["wing_motion"], calibration["movement_threshold"])
        self.assertEqual(assess_motion("flap", measurements, calibration), "fail")

    def test_genuine_articulation_passes(self):
        video, calibration = self.make_video("articulation", articulation=True)
        measurements = measure_motion(video, calibration)
        self.assertTrue(measurements["reliable"], measurements)
        self.assertGreater(measurements["relative_motion"], calibration["movement_threshold"])
        self.assertEqual(assess_motion("flap", measurements, calibration), "pass")

    def test_requested_hold_is_valid(self):
        video, calibration = self.make_video("intentional-hold")
        measurements = measure_motion(video, calibration)
        self.assertEqual(assess_motion("hold", measurements, calibration), "pass")

    def test_low_confidence_crop_is_pending_review(self):
        video, calibration = self.make_video("uncertain-crop", confidence=0.1)
        measurements = measure_motion(video, calibration)
        self.assertFalse(measurements["reliable"])
        self.assertEqual(assess_motion("flap", measurements, calibration), "pending_review")

    def test_action_crossing_a_shot_boundary_is_pending_review(self):
        video, calibration = self.make_video("shot-boundary", shot_cut=True)
        measurements = measure_motion(video, calibration)
        self.assertEqual(measurements["reason"], "shot_boundary")
        self.assertEqual(assess_motion("flap", measurements, calibration), "pending_review")

    def test_blank_during_action_is_not_certified_as_motion(self):
        video, calibration = self.make_video("blank-frame", blank_index=4)
        measurements = measure_motion(video, calibration)
        self.assertTrue(measurements["blank_detected"])
        self.assertFalse(measurements["reliable"])
        self.assertEqual(assess_motion("flap", measurements, calibration), "pending_review")

    def test_body_orientation_is_corrected_before_comparing_regions(self):
        video, calibration = self.make_video("rotated-body", rotate=True)
        measurements = measure_motion(video, calibration)
        self.assertTrue(measurements["reliable"], measurements)
        self.assertLessEqual(measurements["relative_motion"], calibration["still_threshold"])
        self.assertEqual(assess_motion("hold", measurements, calibration), "pass")

    def test_still_image_evidence_cannot_certify_pacing_without_playback(self):
        review = build_review(
            {"job_id": "job-1", "rev": 3, "run_id": "run-9",
             "artifact_sha256": "a" * 64},
            [
                {"category": "technical", "status": "pass", "evidence_ref": "ffprobe:video"},
                {"category": "motion", "status": "pass",
                 "evidence_refs": ["frame:2", "frame:4"]},
                {"category": "visual", "status": "pass", "evidence_type": "still_image"},
                {"category": "editorial", "status": "pass", "criterion": "pacing",
                 "requires_playback": True, "evidence_type": "still_image"},
            ],
            [{"shot": "S1", "time_ms": 1000, "intent": "flap", "actual": "frozen",
              "severity": "major", "suspected_cause": "wing crop is static",
              "repair": "restore visible articulation", "impact": "action reads as a hold",
              "status": "fail", "reviewer": "operator",
              "observation_type": "decoded_video_crop", "evidence_ref": "crop:S1@1000ms"}],
        )
        self.assertEqual(review["technical"]["status"], "pass")
        self.assertEqual(review["motion"]["status"], "pass")
        self.assertEqual(review["visual"]["status"], "pass")
        self.assertEqual(review["editorial"]["status"], "pending_review")
        self.assertEqual(review["observations"][0]["observation_type"], "decoded_video_crop")
        self.assertEqual(review["binding"]["artifact_sha256"], "a" * 64)
        self.assertEqual({item["reference"] for item in review["evidence"]},
                         {"ffprobe:video", "frame:2", "frame:4", "crop:S1@1000ms"})
        self.assertEqual(set(review["observations"][0]), {
            "shot", "time_ms", "intent", "actual", "severity", "suspected_cause", "repair",
            "impact", "status", "reviewer", "observation_type",
        })


if __name__ == "__main__":
    unittest.main()
