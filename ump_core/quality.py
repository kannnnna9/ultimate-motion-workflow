from __future__ import annotations

import json
import math
import re
import subprocess
from bisect import bisect_right
from decimal import Decimal
from pathlib import Path
from typing import Any

from .reference import _probe as _reference_probe


class QualityError(ValueError):
    pass


MAX_IN_ACTION_SAMPLES = 24


def _run(args: list[str]) -> str:
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode:
        raise QualityError(result.stderr.strip() or "video probe/decode failed")
    return result.stdout


def _rotation(stream: dict[str, Any]) -> int:
    candidates = []
    for side_data in stream.get("side_data_list", []):
        if "rotation" in side_data:
            candidates.append(side_data["rotation"])
    tags = stream.get("tags", {})
    if "rotate" in tags:
        candidates.append(tags["rotate"])
    for value in candidates:
        try:
            degrees = round(float(value) / 90) * 90
            if abs(float(value) - degrees) < 0.1:
                return int(degrees) % 360
        except (TypeError, ValueError):
            continue
    return 0


def _probe(video: Path) -> tuple[list[Decimal], Decimal, int, int, int]:
    pts_ms, duration_ms = _reference_probe(video)
    doc = json.loads(_run([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_streams",
        "-of", "json", str(video),
    ]))
    stream = doc["streams"][0]
    width, height = int(stream["width"]), int(stream["height"])
    rotation = _rotation(stream)
    if rotation in (90, 270):
        width, height = height, width
    return pts_ms, duration_ms, width, height, rotation


def _sample_targets(action: dict[str, Any]) -> list[tuple[str, float]]:
    try:
        before = float(action["before_ms"])
        start = float(action["start_ms"])
        end = float(action["end_ms"])
        after = float(action["after_ms"])
    except (KeyError, TypeError, ValueError) as exc:
        raise QualityError("action requires before_ms, start_ms, end_ms and after_ms") from exc
    values = (before, start, end, after)
    if any(not math.isfinite(value) or value < 0 for value in values):
        raise QualityError("action sample times must be finite non-negative milliseconds")
    if not before < start < end < after:
        raise QualityError("action samples must be ordered before < start < end < after")
    if "during_ms" not in action:
        during = [("during_mid", (start + end) / 2)]
    else:
        requested = action["during_ms"]
        if (not isinstance(requested, list)
                or not 1 <= len(requested) <= MAX_IN_ACTION_SAMPLES
                or any(isinstance(value, bool) for value in requested)):
            raise QualityError("during_ms must contain 1–24 in-action sample times")
        try:
            during_times = [float(value) for value in requested]
        except (TypeError, ValueError) as exc:
            raise QualityError("during_ms must contain numeric sample times") from exc
        if (any(not math.isfinite(value) or not start < value < end for value in during_times)
                or during_times != sorted(set(during_times))):
            raise QualityError("during_ms must be unique, ordered and inside the action")
        during = [(f"during_{index}", value)
                  for index, value in enumerate(during_times, start=1)]
    return [("before", before), ("during_start", start), *during,
            ("during_end", end), ("after", after)]


def _roi(value: Any, name: str) -> tuple[int, int, int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise QualityError(f"{name} must be [x, y, width, height] in body-local pixels")
    if any(not isinstance(item, (int, float)) or isinstance(item, bool) or not math.isfinite(item)
           for item in value):
        raise QualityError(f"{name} contains a non-numeric coordinate")
    x, y, width, height = (int(round(item)) for item in value)
    if width < 2 or height < 2:
        raise QualityError(f"{name} is too small")
    return x, y, width, height


def _track_at(track: list[dict[str, Any]], time_ms: float, tolerance_ms: float) -> dict[str, Any]:
    times = [float(point["time_ms"]) for point in track]
    index = bisect_right(times, time_ms)
    if index and times[index - 1] == time_ms:
        left = right = track[index - 1]
    elif index == 0 or index == len(track):
        point = track[0] if index == 0 else track[-1]
        if abs(float(point["time_ms"]) - time_ms) > tolerance_ms:
            raise QualityError("body track does not cover a requested sample")
        left = right = point
    else:
        left, right = track[index - 1], track[index]
        if float(right["time_ms"]) - float(left["time_ms"]) > tolerance_ms * 2:
            raise QualityError("body track has a gap at a requested sample")
    if left is not right and left.get("shot", left.get("shot_id")) != right.get("shot", right.get("shot_id")):
        raise QualityError("shot_boundary")
    left_anchor = left["anchor"]
    right_anchor = right["anchor"]
    if len(left_anchor) != 2 or len(right_anchor) != 2:
        raise QualityError("body track anchor must have two coordinates")
    t0, t1 = float(left["time_ms"]), float(right["time_ms"])
    fraction = 0.0 if t0 == t1 else (time_ms - t0) / (t1 - t0)
    left_angle = float(left.get("angle_deg", 0.0))
    right_angle = float(right.get("angle_deg", 0.0))
    angle_delta = (right_angle - left_angle + 180) % 360 - 180
    return {
        "anchor": [float(left_anchor[i]) + (float(right_anchor[i]) - float(left_anchor[i])) * fraction
                   for i in range(2)],
        "angle_deg": left_angle + angle_delta * fraction,
        "confidence": min(float(left.get("confidence", 1.0)),
                          float(right.get("confidence", 1.0))),
        "shot": left.get("shot", left.get("shot_id")),
    }


def _crop(frame: bytes, frame_width: int, frame_height: int,
          anchor: list[float], angle_deg: float,
          region: tuple[int, int, int, int]) -> list[int]:
    x, y, width, height = region
    angle = math.radians(angle_deg)
    cosine, sine = math.cos(angle), math.sin(angle)
    crop = []
    for row in range(height):
        local_y = y + row
        for column in range(width):
            local_x = x + column
            px = round(anchor[0] + cosine * local_x - sine * local_y)
            py = round(anchor[1] + sine * local_x + cosine * local_y)
            if px < 0 or py < 0 or px >= frame_width or py >= frame_height:
                raise QualityError("body-relative crop is outside the decoded frame")
            crop.append(frame[py * frame_width + px])
    return crop


def _contrast(values: bytes | list[int]) -> float:
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))


def _difference(first: list[int], second: list[int]) -> float:
    return sum(abs(a - b) for a, b in zip(first, second)) / (255 * len(first))


def _uncertain(reason: str, *, blank_detected: bool = False) -> dict[str, Any]:
    return {
        "source": "decoded_pixels",
        "reliable": False,
        "reason": reason,
        "blank_detected": blank_detected,
        "samples": [],
    }


def measure_motion(video_path: str | Path, calibration: dict[str, Any]) -> dict[str, Any]:
    """Measure a calibrated body-relative region from decoded grayscale frames.

    `body_track` contains image-tracked body anchors and clockwise image-plane
    angles keyed by presentation time. `wing_roi` and `reference_roi` are
    rectangles in that canonical body coordinate system. No scene-reported
    hinge angle or whole-frame motion score is used to classify the action.
    """
    blank_detected = False
    try:
        if not isinstance(calibration, dict) or calibration.get("locked") is not True:
            return _uncertain("calibration_not_locked")
        video = Path(video_path)
        if not video.is_file():
            return _uncertain("video_not_found")
        pts_ms, duration_ms, frame_width, frame_height, rotation_degrees = _probe(video)
        targets = _sample_targets(calibration["action"])
        wing_roi = _roi(calibration["wing_roi"], "wing_roi")
        reference_roi = _roi(calibration["reference_roi"], "reference_roi")
        track = calibration["body_track"]
        if not isinstance(track, list) or not track:
            return _uncertain("body_track_missing")
        if any(not isinstance(point, dict) for point in track):
            return _uncertain("body_track_invalid")
        track = sorted(track, key=lambda point: float(point["time_ms"]))
        track_times = [float(point["time_ms"]) for point in track]
        track_confidences = [float(point.get("confidence", 1.0)) for point in track]
        if (any(not math.isfinite(value) for value in track_times + track_confidences)
                or any(not 0 <= confidence <= 1 for confidence in track_confidences)):
            return _uncertain("body_track_invalid")

        sample_tolerance = float(calibration.get("sample_tolerance_ms", 100))
        track_tolerance = float(calibration.get("track_tolerance_ms", 250))
        min_track_confidence = float(calibration.get("min_track_confidence", 0.5))
        if (not all(math.isfinite(value) for value in
                    (sample_tolerance, track_tolerance, min_track_confidence))
                or sample_tolerance < 0 or track_tolerance <= 0
                or not 0 <= min_track_confidence <= 1):
            return _uncertain("invalid_tolerance")
        blank_limit = float(calibration.get("blank_frame_contrast", 1.0))
        min_contrast = float(calibration.get("min_crop_contrast", 5.0))
        if (not math.isfinite(blank_limit) or not math.isfinite(min_contrast)
                or blank_limit < 0 or min_contrast <= 0):
            return _uncertain("invalid_contrast_threshold")
        selected = []
        for label, target in targets:
            requested_time = Decimal(str(target))
            if requested_time < 0 or requested_time >= duration_ms:
                return _uncertain("sample_time_unavailable")
            frame_index = bisect_right(pts_ms, requested_time) - 1
            if frame_index < 0:
                return _uncertain("sample_time_unavailable")
            actual_pts = pts_ms[frame_index]
            if requested_time - actual_pts > Decimal(str(sample_tolerance)):
                return _uncertain("sample_time_unavailable")
            actual_time = float(actual_pts)
            selected.append({"label": label, "target_ms": target, "time_ms": actual_time,
                             "frame_index": frame_index})
        if len({sample["frame_index"] for sample in selected}) != len(selected):
            return _uncertain("insufficient_distinct_samples")

        poses = []
        for sample in selected:
            pose = _track_at(track, sample["time_ms"], track_tolerance)
            if pose["confidence"] < min_track_confidence:
                return _uncertain("body_track_unreliable")
            sample["pose"] = pose
            poses.append(pose)
        shot_ids = {pose["shot"] for pose in poses if pose["shot"] is not None}
        if len(shot_ids) > 1:
            return _uncertain("shot_boundary")

        indices = sorted({sample["frame_index"] for sample in selected})
        expression = "select=" + "+".join(f"eq(n\\,{index})" for index in indices)
        raw = subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(video),
            "-map", "0:v:0", "-vf", expression, "-fps_mode", "passthrough",
            "-pix_fmt", "gray", "-f", "rawvideo", "pipe:1",
        ], capture_output=True)
        if raw.returncode:
            return _uncertain("frame_decode_failed")
        frame_size = frame_width * frame_height
        if frame_size <= 0 or len(raw.stdout) != frame_size * len(indices):
            return _uncertain("decoded_frame_count_mismatch")
        decoded = {index: raw.stdout[offset * frame_size:(offset + 1) * frame_size]
                   for offset, index in enumerate(indices)}

        samples = []
        wing_crops = []
        reference_crops = []
        for sample in selected:
            frame = decoded[sample["frame_index"]]
            mean = sum(frame) / len(frame)
            frame_contrast = _contrast(frame)
            is_blank = frame_contrast <= blank_limit and (mean <= 20 or mean >= 235)
            blank_detected = blank_detected or is_blank
            pose = sample["pose"]
            wing = _crop(frame, frame_width, frame_height, pose["anchor"],
                         pose["angle_deg"], wing_roi)
            reference = _crop(frame, frame_width, frame_height, pose["anchor"],
                              pose["angle_deg"], reference_roi)
            if _contrast(wing) < min_contrast or _contrast(reference) < min_contrast:
                return _uncertain("crop_has_insufficient_contrast", blank_detected=blank_detected)
            wing_crops.append(wing)
            reference_crops.append(reference)
            samples.append({
                "phase": sample["label"],
                "target_ms": round(sample["target_ms"], 3),
                "pts_ms": round(sample["time_ms"], 3),
                "frame_index": sample["frame_index"],
                "shot": pose["shot"],
                "anchor": [round(value, 3) for value in pose["anchor"]],
                "angle_deg": round(pose["angle_deg"], 3),
                "blank_detected": is_blank,
                "wing_crop": {"roi_body_local": list(wing_roi), "width": wing_roi[2],
                              "height": wing_roi[3], "contrast": round(_contrast(wing), 5)},
                "reference_crop": {"roi_body_local": list(reference_roi),
                                   "width": reference_roi[2], "height": reference_roi[3],
                                   "contrast": round(_contrast(reference), 5)},
            })
        if blank_detected:
            return _uncertain("blank_frame", blank_detected=True)

        during_indices = [index for index, sample in enumerate(selected)
                          if sample["label"].startswith("during_")]
        during_pairs = tuple(zip(during_indices, during_indices[1:]))
        wing_diffs = [_difference(wing_crops[a], wing_crops[b]) for a, b in during_pairs]
        reference_diffs = [_difference(reference_crops[a], reference_crops[b])
                           for a, b in during_pairs]
        wing_motion = max(wing_diffs)
        reference_motion = max(reference_diffs)
        relative_motion = max(0.0, wing_motion - reference_motion * 1.5)
        displacement = max(math.dist(poses[a]["anchor"], poses[b]["anchor"])
                           for a, b in during_pairs)
        return {
            "source": "decoded_pixels",
            "reliable": True,
            "reason": None,
            "blank_detected": False,
            "samples": samples,
            "sample_phases": [sample["phase"] for sample in samples],
            "wing_motion": round(wing_motion, 7),
            "reference_motion": round(reference_motion, 7),
            "relative_motion": round(relative_motion, 7),
            "wing_pair_differences": [round(value, 7) for value in wing_diffs],
            "reference_pair_differences": [round(value, 7) for value in reference_diffs],
            "body_displacement_px": round(displacement, 4),
            "shot": next(iter(shot_ids), None),
            "frame_size": [frame_width, frame_height],
            "video_rotation_deg": rotation_degrees,
        }
    except (OSError, KeyError, IndexError, TypeError, ValueError, ArithmeticError,
            subprocess.SubprocessError, json.JSONDecodeError) as exc:
        reason = str(exc) if str(exc) == "shot_boundary" else "measurement_unavailable"
        return _uncertain(reason, blank_detected=blank_detected)


def assess_motion(intent: str, measurements: dict[str, Any], calibration: dict[str, Any]) -> str:
    """Return pass/fail only for reliable decoded-pixel evidence and locked calibration."""
    if (not isinstance(calibration, dict) or calibration.get("locked") is not True
            or not isinstance(measurements, dict)
            or measurements.get("source") != "decoded_pixels"
            or measurements.get("reliable") is not True
            or measurements.get("blank_detected") is True
            or not isinstance(intent, str)):
        return "pending_review"
    try:
        motion = float(measurements["relative_motion"])
        movement_threshold = float(calibration.get("movement_threshold", 0.04))
        still_threshold = float(calibration.get("still_threshold", 0.025))
    except (KeyError, TypeError, ValueError, OverflowError):
        return "pending_review"
    if (not all(math.isfinite(value) for value in
                (motion, movement_threshold, still_threshold))
            or not 0 <= motion <= 1
            or not 0 < movement_threshold <= 1
            or not 0 < still_threshold <= 1):
        return "pending_review"

    normalized = intent.strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in {"hold", "still", "stillness", "intentional_stillness"}:
        if motion <= still_threshold:
            return "pass"
        if motion >= still_threshold * 2:
            return "fail"
        return "pending_review"
    if normalized in {"flap", "wing_flap", "articulate", "articulation", "move", "motion"}:
        if motion >= movement_threshold:
            return "pass"
        if motion < movement_threshold * 0.5:
            return "fail"
        return "pending_review"
    return "pending_review"


def _normalized_range(value: Any) -> tuple[float, float] | None:
    if (not isinstance(value, (list, tuple)) or len(value) != 2
            or any(not isinstance(item, (int, float)) or isinstance(item, bool)
                   or not math.isfinite(item) for item in value)):
        return None
    start, end = (float(item) for item in value)
    if start < 0 or end <= start:
        return None
    return start, end


def _requires_playback(check: dict[str, Any], category: str) -> bool:
    if check.get("requires_playback") is True:
        return True
    criterion = check.get("criterion")
    return (category == "editorial" and isinstance(criterion, str)
            and criterion.strip().lower() in {"pacing", "timing", "rhythm"})


def _is_blocking_check(check: dict[str, Any]) -> bool:
    return (check.get("advisory") is not True
            or check.get("required") is True
            or check.get("conflicts_with_intent") is True)


def _playback_observation_matches(binding: dict[str, Any], check: dict[str, Any]) -> bool:
    playback = check.get("playback_observation")
    if not isinstance(playback, dict):
        return False
    criterion = check.get("criterion")
    if (not isinstance(criterion, str) or not criterion.strip()
            or not isinstance(playback.get("criterion"), str)
            or playback["criterion"].strip().lower() != criterion.strip().lower()):
        return False
    expected_range = _normalized_range(check.get("range_ms"))
    observed_range = _normalized_range(playback.get("range_ms"))
    if expected_range is None or observed_range != expected_range:
        return False
    artifact_sha256 = playback.get("artifact_sha256")
    if (not isinstance(artifact_sha256, str)
            or artifact_sha256.lower() != binding["artifact_sha256"].lower()):
        return False
    reviewer = playback.get("reviewer")
    observation = playback.get("observation")
    if (not isinstance(reviewer, str) or not reviewer.strip()
            or not isinstance(observation, str) or not observation.strip()):
        return False
    speed = playback.get("speed")
    if (not isinstance(speed, (int, float)) or isinstance(speed, bool)
            or not math.isfinite(speed) or speed != 1.0):
        return False
    if any(playback.get(key) is True
           for key in ("automatic_completion", "auto_completed", "player_completion")):
        return False
    evidence_ref = playback.get("evidence_ref")
    if not isinstance(evidence_ref, str) or not evidence_ref.strip():
        return False
    references = check.get("evidence_refs", check.get("evidence_ref"))
    references = references if isinstance(references, list) else [references]
    return evidence_ref in references


def _category_status(checks: list[dict[str, Any]]) -> str:
    if not checks:
        return "pending_review"
    statuses = [check.get("status") for check in checks if _is_blocking_check(check)]
    if not statuses:
        return "pass"
    if "fail" in statuses:
        return "fail"
    if "pending_review" in statuses:
        return "pending_review"
    if all(status == "pass" for status in statuses):
        return "pass"
    return "pending_review"


def build_review(binding: dict[str, Any], checks: list[dict[str, Any]],
                 observations: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a provenance-bound review with independent category decisions."""
    required = ("job_id", "rev", "run_id", "artifact_sha256")
    if not isinstance(binding, dict) or any(key not in binding or binding[key] in (None, "")
                                             for key in required):
        raise QualityError("binding requires job_id, rev, run_id and artifact_sha256")
    if not isinstance(binding["artifact_sha256"], str) or not re.fullmatch(
            r"[0-9a-fA-F]{64}", binding["artifact_sha256"]):
        raise QualityError("artifact_sha256 must be a 64-character hexadecimal digest")
    if not isinstance(checks, list) or not isinstance(observations, list):
        raise QualityError("checks and observations must be lists")
    categories = ("technical", "motion", "visual", "editorial")
    grouped = {category: [] for category in categories}
    for check in checks:
        if not isinstance(check, dict):
            raise QualityError("each check must be an object")
        category = check.get("category")
        status = check.get("status")
        if category not in grouped or status not in {"pass", "fail", "pending_review"}:
            raise QualityError("checks require a known category and pass/fail/pending_review status")
        grouped[category].append(dict(check))

    category_reviews = {}
    for category in categories:
        for check in grouped[category]:
            if (_is_blocking_check(check) and check["status"] == "pass"
                    and _requires_playback(check, category)
                    and not _playback_observation_matches(binding, check)):
                check["status"] = "pending_review"
        status = _category_status(grouped[category])
        category_reviews[category] = {"status": status, "checks": grouped[category]}

    normalized_observations = []
    observation_fields = (
        "shot", "time_ms", "intent", "actual", "severity", "suspected_cause", "repair",
        "impact", "status", "reviewer", "observation_type",
    )
    for observation in observations:
        if not isinstance(observation, dict):
            raise QualityError("each observation must be an object")
        normalized = {key: observation.get(key) for key in observation_fields}
        if normalized["status"] not in {"pass", "fail", "pending_review"}:
            normalized["status"] = "pending_review"
        normalized_observations.append(normalized)

    evidence = []
    for category, category_checks in grouped.items():
        for index, check in enumerate(category_checks):
            refs = check.get("evidence_refs", check.get("evidence_ref"))
            if refs is not None:
                refs = refs if isinstance(refs, list) else [refs]
                evidence.extend({"category": category, "check_index": index, "reference": ref}
                                for ref in refs)
    for index, observation in enumerate(observations):
        refs = observation.get("evidence_refs", observation.get("evidence_ref"))
        if refs is not None:
            refs = refs if isinstance(refs, list) else [refs]
            evidence.extend({"observation_index": index, "reference": ref} for ref in refs)

    overall = _category_status([{"status": category_reviews[name]["status"]}
                                for name in categories])
    return {
        "binding": {key: binding[key] for key in required},
        **category_reviews,
        "observations": normalized_observations,
        "evidence": evidence,
        "overall_status": overall,
    }
