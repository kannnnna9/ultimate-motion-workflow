from __future__ import annotations

import json
import math
import subprocess
import tempfile
import unittest
from pathlib import Path

from ump_core.quality import assess_motion, build_review, measure_motion
from ump_core.reference import _probe as probe_reference_clock


class QualityMotionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._temp = tempfile.TemporaryDirectory(prefix="ump-quality-")
        cls.addClassCleanup(cls._temp.cleanup)
        cls.root = Path(cls._temp.name)

    def make_video(self, name, *, articulation=False, confidence=1.0, shot_cut=False,
                   blank_index=None, rotate=False, video_format="mkv"):
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

        video = folder / ("fixture.mp4" if video_format == "mp4" else "fixture.mkv")
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-framerate", "4",
            "-i", str(folder / "frame%03d.ppm"),
        ]
        if video_format == "mp4":
            command.extend(["-c:v", "libx264", "-preset", "ultrafast", "-crf", "0",
                            "-pix_fmt", "yuv444p", "-threads", "1"])
        else:
            command.extend(["-c:v", "ffv1", "-pix_fmt", "gray", "-threads", "1"])
        command.append(str(video))
        subprocess.run(command, check=True, capture_output=True, text=True)
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

    def test_between_sample_uses_last_pts_at_or_before_target(self):
        video, calibration = self.make_video("between-pts")
        calibration["action"]["start_ms"] = 690
        calibration["sample_tolerance_ms"] = 250
        measurements = measure_motion(video, calibration)
        self.assertTrue(measurements["reliable"], measurements)
        self.assertEqual(measurements["samples"][1]["target_ms"], 690.0)
        self.assertEqual(measurements["samples"][1]["pts_ms"], 500.0)

    def test_sample_outside_explicit_capture_tolerance_is_pending(self):
        video, calibration = self.make_video("capture-tolerance")
        calibration["action"]["start_ms"] = 690
        self.assertEqual(calibration["sample_tolerance_ms"], 30)
        measurements = measure_motion(video, calibration)
        self.assertFalse(measurements["reliable"])
        self.assertEqual(measurements["reason"], "sample_time_unavailable")

    def test_sample_at_video_duration_is_end_exclusive(self):
        video, calibration = self.make_video("end-exclusive")
        duration_ms = float(probe_reference_clock(video)[1])
        calibration["action"]["after_ms"] = duration_ms
        calibration["sample_tolerance_ms"] = duration_ms
        measurements = measure_motion(video, calibration)
        self.assertFalse(measurements["reliable"])
        self.assertEqual(measurements["reason"], "sample_time_unavailable")

    def test_predeclared_dense_in_action_samples_are_compared_adjacently(self):
        video, calibration = self.make_video("dense-action", articulation=True)
        calibration["action"]["during_ms"] = [750, 1000, 1250]
        measurements = measure_motion(video, calibration)
        self.assertTrue(measurements["reliable"], measurements)
        self.assertEqual(measurements["sample_phases"], [
            "before", "during_start", "during_1", "during_2", "during_3",
            "during_end", "after",
        ])
        self.assertEqual([sample["pts_ms"] for sample in measurements["samples"]],
                         [250.0, 500.0, 750.0, 1000.0, 1250.0, 1500.0, 1750.0])
        self.assertEqual(len(measurements["wing_pair_differences"]), 4)

    def test_in_action_sample_count_is_bounded(self):
        video, calibration = self.make_video("too-many-action-samples")
        calibration["action"]["during_ms"] = [520 + index * 30 for index in range(25)]
        measurements = measure_motion(video, calibration)
        self.assertFalse(measurements["reliable"])

    def test_invalid_motion_thresholds_cannot_certify_zero_motion(self):
        measurements = {"source": "decoded_pixels", "reliable": True,
                        "blank_detected": False, "relative_motion": 0.0}
        for movement_threshold in (0.0, float("nan"), float("inf")):
            with self.subTest(movement_threshold=movement_threshold):
                calibration = {"locked": True, "movement_threshold": movement_threshold,
                               "still_threshold": 0.025}
                self.assertEqual(assess_motion("flap", measurements, calibration),
                                 "pending_review")

    def test_nonfinite_track_confidence_and_tolerances_fail_closed(self):
        video, calibration = self.make_video("nonfinite-calibration-values")
        cases = [
            ("track_confidence", lambda value: value.update({
                "body_track": [dict(point, confidence=float("nan"))
                               for point in value["body_track"]],
            })),
            ("min_track_confidence", lambda value: value.update({
                "min_track_confidence": float("nan"),
            })),
            ("sample_tolerance_ms", lambda value: value.update({
                "sample_tolerance_ms": float("nan"),
            })),
            ("track_tolerance_ms", lambda value: value.update({
                "track_tolerance_ms": float("inf"),
            })),
        ]
        for name, invalidate in cases:
            with self.subTest(name=name):
                invalid_calibration = dict(calibration)
                invalid_calibration["action"] = dict(calibration["action"])
                invalidate(invalid_calibration)
                measurements = measure_motion(video, invalid_calibration)
                self.assertFalse(measurements["reliable"], measurements)
                self.assertEqual(assess_motion("flap", measurements, invalid_calibration),
                                 "pending_review")

    def test_h264_mp4_motion_is_measured_from_decoded_frames(self):
        video, calibration = self.make_video(
            "h264-articulation", articulation=True, video_format="mp4")
        probe = subprocess.run([
            "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
            "stream=codec_name", "-of", "json", str(video),
        ], check=True, capture_output=True, text=True)
        self.assertEqual(json.loads(probe.stdout)["streams"][0]["codec_name"], "h264")
        measurements = measure_motion(video, calibration)
        self.assertTrue(measurements["reliable"], measurements)
        self.assertEqual(assess_motion("flap", measurements, calibration), "pass")

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

    def playback_check(self, artifact_sha256, *, playback_overrides=None, **check_overrides):
        evidence_ref = f"playback:{artifact_sha256}:pacing:500-1500"
        playback_observation = {
            "reviewer": "reviewer-17",
            "speed": 1.0,
            "observation": "The wing movement accelerates and settles within the requested beat.",
            "artifact_sha256": artifact_sha256,
            "criterion": "pacing",
            "range_ms": [500, 1500],
            "evidence_ref": evidence_ref,
        }
        playback_observation.update(playback_overrides or {})
        check = {
            "category": "editorial",
            "status": "pass",
            "criterion": "pacing",
            "requires_playback": True,
            "range_ms": [500, 1500],
            "evidence_ref": evidence_ref,
            "playback_observation": playback_observation,
        }
        check.update(check_overrides)
        return check

    def test_playback_flags_or_evidence_type_alone_do_not_approve(self):
        artifact_sha256 = "a" * 64
        review = build_review(
            {"job_id": "job-1", "rev": 3, "run_id": "run-9",
             "artifact_sha256": artifact_sha256},
            [{"category": "editorial", "status": "pass", "criterion": "pacing",
              "requires_playback": True, "playback_reviewed": True,
              "evidence_type": "playback"}],
            [],
        )
        self.assertEqual(review["editorial"]["status"], "pending_review")

    def test_playback_flag_on_another_check_cannot_vouch_for_pacing(self):
        artifact_sha256 = "a" * 64
        review = build_review(
            {"job_id": "job-1", "rev": 3, "run_id": "run-9",
             "artifact_sha256": artifact_sha256},
            [{"category": "editorial", "status": "pass", "criterion": "pacing",
              "requires_playback": True},
             {"category": "editorial", "status": "pass", "criterion": "composition",
              "playback_reviewed": True, "evidence_type": "normal_speed_playback"}],
            [],
        )
        self.assertEqual(review["editorial"]["status"], "pending_review")
        self.assertEqual(review["editorial"]["checks"][0]["status"], "pending_review")

    def test_playback_observation_for_wrong_artifact_is_pending(self):
        artifact_sha256 = "a" * 64
        review = build_review(
            {"job_id": "job-1", "rev": 3, "run_id": "run-9",
             "artifact_sha256": artifact_sha256},
            [self.playback_check(artifact_sha256, playback_overrides={
                "artifact_sha256": "b" * 64,
            }, playback_reviewed=True, evidence_type="playback")],
            [],
        )
        self.assertEqual(review["editorial"]["status"], "pending_review")

    def test_playback_observation_must_match_criterion_range_and_be_human(self):
        artifact_sha256 = "a" * 64
        for mismatch in ({"criterion": "timing"}, {"range_ms": [600, 1500]},
                         {"evidence_ref": "playback:other-artifact:pacing:500-1500"},
                         {"automatic_completion": True}, {"reviewer": " "},
                         {"speed": 1.5}):
            with self.subTest(mismatch=mismatch):
                review = build_review(
                    {"job_id": "job-1", "rev": 3, "run_id": "run-9",
                     "artifact_sha256": artifact_sha256},
                    [self.playback_check(artifact_sha256, playback_overrides=mismatch,
                                         playback_reviewed=True, evidence_type="playback")],
                    [],
                )
                self.assertEqual(review["editorial"]["status"], "pending_review")

    def test_matched_normal_speed_playback_observation_approves_editorial_check(self):
        artifact_sha256 = "a" * 64
        review = build_review(
            {"job_id": "job-1", "rev": 3, "run_id": "run-9",
             "artifact_sha256": artifact_sha256},
            [self.playback_check(artifact_sha256)],
            [],
        )
        self.assertEqual(review["editorial"]["status"], "pass")
        self.assertEqual(review["editorial"]["checks"][0]["status"], "pass")

    def test_advisory_anti_slide_failure_remains_visible_without_rejecting_brief(self):
        review = build_review(
            {"job_id": "job-1", "rev": 3, "run_id": "run-9",
             "artifact_sha256": "a" * 64},
            [{"category": "technical", "status": "pass"},
             {"category": "motion", "status": "pass"},
             {"category": "visual", "status": "fail", "criterion": "anti_slideshow",
              "advisory": True, "evidence_ref": "anti-slide:shot-2"},
             {"category": "editorial", "status": "pass", "criterion": "composition"}],
            [],
        )
        self.assertEqual(review["visual"]["status"], "pass")
        self.assertEqual(review["visual"]["checks"][0]["status"], "fail")
        self.assertEqual(review["overall_status"], "pass")
        self.assertIn("anti-slide:shot-2", {item["reference"] for item in review["evidence"]})

    def test_explicit_required_conflict_remains_a_hard_failure(self):
        review = build_review(
            {"job_id": "job-1", "rev": 3, "run_id": "run-9",
             "artifact_sha256": "a" * 64},
            [{"category": "technical", "status": "pass"},
             {"category": "motion", "status": "pass"},
             {"category": "visual", "status": "fail", "criterion": "required_action",
              "required": True, "conflicts_with_intent": True},
             {"category": "editorial", "status": "pass", "criterion": "composition"}],
            [],
        )
        self.assertEqual(review["visual"]["status"], "fail")
        self.assertEqual(review["overall_status"], "fail")

    def test_required_or_intent_conflict_failure_overrides_advisory(self):
        binding = {"job_id": "job-1", "rev": 3, "run_id": "run-9",
                   "artifact_sha256": "a" * 64}
        baseline = [{"category": category, "status": "pass"}
                    for category in ("technical", "motion", "visual", "editorial")]
        for precedence in ({"required": True}, {"conflicts_with_intent": True},
                           {"required": True, "conflicts_with_intent": True}):
            with self.subTest(precedence=precedence):
                failure = {"category": "visual", "status": "fail",
                           "criterion": "legibility_required_by_brief", "advisory": True,
                           "evidence_ref": "brief:legibility", **precedence}
                review = build_review(binding, [*baseline, failure], [])
                self.assertEqual(review["visual"]["status"], "fail")
                self.assertEqual(review["overall_status"], "fail")
                self.assertEqual(review["visual"]["checks"][-1]["status"], "fail")
                self.assertTrue(review["visual"]["checks"][-1]["advisory"])
                self.assertIn("brief:legibility",
                              {item["reference"] for item in review["evidence"]})

    def test_explicit_playback_requirement_without_observation_is_pending_in_any_category(self):
        binding = {"job_id": "job-1", "rev": 3, "run_id": "run-9",
                   "artifact_sha256": "a" * 64}
        baseline = [{"category": category, "status": "pass"}
                    for category in ("technical", "motion", "visual", "editorial")]
        for category in ("technical", "motion", "visual", "editorial"):
            with self.subTest(category=category):
                check = {"category": category, "status": "pass",
                         "criterion": f"required {category} playback", "requires_playback": True}
                review = build_review(binding, [*baseline, check], [])
                self.assertEqual(review[category]["status"], "pending_review")
                self.assertEqual(review[category]["checks"][-1]["status"], "pending_review")

    def test_matched_normal_speed_playback_approves_non_editorial_check(self):
        artifact_sha256 = "a" * 64
        check = self.playback_check(artifact_sha256)
        check["category"] = "visual"
        review = build_review(
            {"job_id": "job-1", "rev": 3, "run_id": "run-9",
             "artifact_sha256": artifact_sha256},
            [check],
            [],
        )
        self.assertEqual(review["visual"]["status"], "pass")
        self.assertEqual(review["visual"]["checks"][0]["status"], "pass")

    def test_advisory_playback_suggestion_does_not_block(self):
        review = build_review(
            {"job_id": "job-1", "rev": 3, "run_id": "run-9",
             "artifact_sha256": "a" * 64},
            [{"category": "visual", "status": "pass", "criterion": "suggested rhythm",
              "requires_playback": True, "advisory": True}],
            [],
        )
        self.assertEqual(review["visual"]["status"], "pass")
        self.assertEqual(review["visual"]["checks"][0]["status"], "pass")

    def test_timing_inference_is_editorial_only(self):
        baseline = [{"category": category, "status": "pass"}
                    for category in ("technical", "motion", "visual", "editorial")]
        review = build_review(
            {"job_id": "job-1", "rev": 3, "run_id": "run-9",
             "artifact_sha256": "a" * 64},
            [*baseline,
             {"category": "technical", "status": "pass", "criterion": "timing"},
             {"category": "editorial", "status": "pass", "criterion": "timing"}],
            [],
        )
        self.assertEqual(review["technical"]["status"], "pass")
        self.assertEqual(review["technical"]["checks"][-1]["status"], "pass")
        self.assertEqual(review["editorial"]["status"], "pending_review")
        self.assertEqual(review["editorial"]["checks"][-1]["status"], "pending_review")


if __name__ == "__main__":
    unittest.main()
