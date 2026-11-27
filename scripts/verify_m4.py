#!/usr/bin/env python3
"""Create and verify the M4 decoded-motion proof and operator handoff."""
from __future__ import annotations

import argparse
import bisect
import hashlib
import io
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any
from unittest.mock import patch

from ump_core import resources, sandbox, tools, worker
from ump_core.assets import AssetStore
from ump_core.jobs import DomainError, JobStore
from ump_core.quality import assess_motion, build_review, measure_motion
from ump_core.reference import _probe as reference_probe
from ump_core.reference import make_contact_sheet


M2_JOB_ID = "A7w6XhFV1uvUFeVW"
M2_REV = 2
M2_RUN_ID = "7UwjNXDyYLBLDGA_"
M2_PREVIEW_SHA256 = "79d810965ca47be04b339dca3960df3b5f82dbbcaab549bd864d31ed67a7c743"
M2_SOURCE_SHA256 = "1a57a9e6bb12a5378d1837881d39abf77dcf9e0d18779d0f7bce5fdab51bde56"
M2_HTML_SHA256 = "05f0cf0a8aeddc9ada11c9d75521e3e95544ba6ebe5a84032c0ecce7ab1b13e0"
POSITIVE_FLAP = "flap=.22+.52*Math.sin(phase)"
NEGATIVE_FLAP = "flap=.22"
SAMPLE_TIMES_MS = [250, 500, 750, 1000, 1250, 1500, 1750, 2000, 2250, 2500, 2750]
DURING_TIMES_MS = SAMPLE_TIMES_MS[2:-2]
WING_ROI = [-180, -70, 40, 40]
REFERENCE_ROI = [-40, 90, 80, 50]


class ProofError(ValueError):
    """A required M4 evidence invariant is not proven."""


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                          allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as exc:
        raise ProofError("evidence must be bounded canonical JSON") from exc


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ProofError(f"cannot open evidence file: {path}") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise ProofError(f"evidence path is not a private regular file: {path}")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        after = os.fstat(fd)
        if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise ProofError(f"evidence file changed while hashing: {path}")
        return digest.hexdigest()
    finally:
        os.close(fd)


def _data_file(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ProofError("artifact path must be a non-empty relative POSIX path")
    parsed = PurePosixPath(relative)
    if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
        raise ProofError(f"unsafe artifact path: {relative}")
    root = root.resolve(strict=True)
    path = root.joinpath(*parsed.parts)
    cursor = root
    for part in parsed.parts:
        cursor = cursor / part
        try:
            info = cursor.lstat()
        except OSError as exc:
            raise ProofError(f"artifact path is missing: {relative}") from exc
        if stat.S_ISLNK(info.st_mode):
            raise ProofError(f"artifact path contains a symlink: {relative}")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ProofError(f"artifact path is missing: {relative}") from exc
    if not resolved.is_relative_to(root):
        raise ProofError(f"artifact path escapes DATA_ROOT: {relative}")
    info = resolved.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
        raise ProofError(f"artifact is not a private regular file: {relative}")
    return resolved


def create_fresh_evidence_dir(out: str | Path) -> Path:
    """Create a new evidence directory; an existing path is never reused."""
    path = Path(out).expanduser().absolute()
    if path.exists() or path.is_symlink():
        raise ProofError(f"evidence output already exists; refusing overwrite: {path}")
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise ProofError(f"evidence output was created concurrently: {path}") from exc
    if path.is_symlink() or not path.is_dir():
        raise ProofError(f"evidence output is not a real directory: {path}")
    os.chmod(path, 0o700)
    return path


def _write_once(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) \
        | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise ProofError(f"refusing to overwrite evidence output: {path}") from exc
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _write_json_once(path: Path, value: Any) -> None:
    _write_once(path, json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                                 allow_nan=False).encode("utf-8") + b"\n")


def _asset_pin_sha(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("sha256")
    return None


def _safe_archive_name(name: str) -> bool:
    if not isinstance(name, str) or "\\" in name:
        return False
    parsed = PurePosixPath(name)
    return (not parsed.is_absolute() and bool(parsed.parts)
            and all(part not in {"", ".", ".."} for part in parsed.parts))


def _source_files(root: Path, record: dict) -> dict[str, bytes]:
    source = record.get("source")
    if not isinstance(source, dict):
        raise ProofError("run record has no source artifact")
    source_path = _data_file(root, source.get("path"))
    if _sha256_file(source_path) != source.get("sha256"):
        raise ProofError(f"source ZIP hash mismatch for {record.get('run_id')}")
    if type(source.get("size_bytes")) is int and source_path.stat().st_size != source["size_bytes"]:
        raise ProofError(f"source ZIP size mismatch for {record.get('run_id')}")

    files: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(source_path) as archive:
            infos = archive.infolist()
            names = [item.filename for item in infos]
            if len(names) != len(set(names)) or any(not _safe_archive_name(name) for name in names):
                raise ProofError("source ZIP has duplicate or unsafe member names")
            if sum(item.file_size for item in infos) > 128 * 1024 * 1024:
                raise ProofError("source ZIP expands beyond the verifier bound")
            for item in infos:
                mode = (item.external_attr >> 16) & 0xFFFF
                kind = stat.S_IFMT(mode)
                if kind not in (0, stat.S_IFREG):
                    raise ProofError(f"source ZIP contains a non-regular entry: {item.filename}")
                files[item.filename] = archive.read(item)
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise ProofError("source ZIP cannot be decoded") from exc

    if "manifest.json" not in files or "index.html" not in files or "project.json" not in files:
        raise ProofError("source ZIP is missing index.html, project.json, or manifest.json")
    try:
        manifest = json.loads(files["manifest.json"])
    except (TypeError, ValueError) as exc:
        raise ProofError("source manifest is invalid JSON") from exc
    expected = manifest.get("files") if isinstance(manifest, dict) else None
    if manifest.get("schema_version") != 1 or not isinstance(expected, dict):
        raise ProofError("source manifest schema is invalid")
    content_names = set(files) - {"manifest.json"}
    if content_names != set(expected):
        raise ProofError("source manifest member set does not match the ZIP")
    for name, data in files.items():
        if name == "manifest.json":
            continue
        row = expected.get(name)
        if (not isinstance(row, dict) or row.get("sha256") != _sha256_bytes(data)
                or row.get("size_bytes") != len(data)):
            raise ProofError(f"source manifest pin mismatch: {name}")

    html_sha = _sha256_bytes(files["index.html"])
    project_sha = _sha256_bytes(files["project.json"])
    if html_sha != source.get("html_sha256") or project_sha != source.get("project_sha256"):
        raise ProofError(f"source HTML/project hash mismatch for {record.get('run_id')}")
    preview_source = _data_file(root, f"{source['revision_path']}/index.html")
    if preview_source.read_bytes() != files["index.html"]:
        raise ProofError(f"source ZIP HTML differs from revision copy for {record.get('run_id')}")

    pins = source.get("asset_pins")
    if not isinstance(pins, dict):
        raise ProofError("source record is missing revision asset pins")
    archive_assets = {name: data for name, data in files.items() if name.startswith("assets/")}
    if set(archive_assets) != set(pins):
        raise ProofError(f"source asset set differs from revision metadata for {record.get('run_id')}")
    for name, data in archive_assets.items():
        digest = _sha256_bytes(data)
        if digest != _asset_pin_sha(pins[name]):
            raise ProofError(f"source asset pin mismatch: {name}")
        revision_asset = _data_file(root, f"{source['revision_path']}/{name}")
        if revision_asset.read_bytes() != data:
            raise ProofError(f"source asset differs from revision copy: {name}")
        pin = pins[name]
        if isinstance(pin, dict) and pin.get("size_bytes") != len(data):
            raise ProofError(f"source asset size pin mismatch: {name}")
    return files


def _validate_run_record(root: Path, record: dict, calibration_sha256: str) -> dict[str, bytes]:
    if not isinstance(record, dict):
        raise ProofError("run evidence must be an object")
    for key in ("job_id", "run_id"):
        if not isinstance(record.get(key), str) or not record[key]:
            raise ProofError(f"run evidence is missing {key}")
    if type(record.get("rev")) is not int or record["rev"] < 1:
        raise ProofError("run evidence has an invalid revision")
    if record.get("calibration_sha256") != calibration_sha256:
        raise ProofError(f"calibration hash mismatch at run {record['run_id']}")
    preview = record.get("preview")
    if not isinstance(preview, dict) or not isinstance(preview.get("artifact_id"), str):
        raise ProofError("run evidence is missing its registered preview artifact ID")
    media_path = _data_file(root, preview.get("path"))
    if _sha256_file(media_path) != preview.get("sha256"):
        raise ProofError(f"preview MP4 hash mismatch at run {record['run_id']}")
    if preview.get("size_bytes") != media_path.stat().st_size:
        raise ProofError(f"preview MP4 size mismatch at run {record['run_id']}")
    if record.get("technical_status") != "pass":
        raise ProofError(f"technical checks did not pass at run {record['run_id']}")
    return _source_files(root, record)


def validate_pair(root: str | Path, reference: dict, before: dict, after: dict,
                  calibration: dict) -> dict:
    """Validate the actual job/revision/media/source/calibration pair."""
    root = Path(root).resolve(strict=True)
    if not isinstance(calibration, dict) or calibration.get("locked") is not True:
        raise ProofError("motion calibration is not locked")
    calibration_sha256 = _sha256_bytes(_canonical_bytes(calibration))
    if (before.get("job_id") != after.get("job_id")
            or before.get("rev") + 1 != after.get("rev")):
        raise ProofError("before/after previews must be consecutive revisions of the same job")
    if before.get("run_id") == after.get("run_id"):
        raise ProofError("before/after previews must have distinct worker run IDs")
    if before.get("repair_attempts") != 0 or after.get("repair_attempts") != 1:
        raise ProofError("preview must start at repair attempt zero and use exactly one repair")
    if before.get("motion_status") != "fail" or after.get("motion_status") != "pass":
        raise ProofError("decoded motion must fail before and pass after the source repair")
    if before.get("editorial_status") != "pending_review" or after.get("editorial_status") != "pending_review":
        raise ProofError("unreviewed pacing/editorial status must remain pending")

    reference_files = _validate_run_record(root, reference, calibration_sha256)
    before_files = _validate_run_record(root, before, calibration_sha256)
    after_files = _validate_run_record(root, after, calibration_sha256)
    ref_pins = reference["source"]["asset_pins"]
    before_pins = before["source"]["asset_pins"]
    after_pins = after["source"]["asset_pins"]
    if ref_pins != before_pins or ref_pins != after_pins:
        raise ProofError("asset pins changed between the original, negative, and repaired revisions")
    if before_files["project.json"] != after_files["project.json"]:
        raise ProofError("non-motion composition metadata changed across the proof pair")
    try:
        historical_project = json.loads(reference_files["project.json"])
        current_project = json.loads(before_files["project.json"])
    except (TypeError, ValueError) as exc:
        raise ProofError("source project metadata is not JSON") from exc
    if not isinstance(historical_project, dict) or not isinstance(current_project, dict):
        raise ProofError("source project metadata is not an object")
    # M3 made these neutral defaults explicit; M2 omitted them entirely.
    historical_defaults = {"draws_speech_text": False, "speech_anchors": [],
                           "subtitle_mode": "none", "subtitle_effect": None}
    comparable_project = dict(current_project)
    normalized_defaults = []
    for key, default in historical_defaults.items():
        value = comparable_project.get(key)
        if (key not in historical_project and key in comparable_project
                and type(value) is type(default) and value == default):
            comparable_project.pop(key)
            normalized_defaults.append(key)
    if comparable_project != historical_project:
        raise ProofError("non-motion composition metadata changed from the original M2 source")

    reference_html = reference_files["index.html"]
    negative_html = before_files["index.html"]
    repaired_html = after_files["index.html"]
    try:
        reference_text = reference_html.decode("utf-8")
        negative_text = negative_html.decode("utf-8")
        repaired_text = repaired_html.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProofError("composition HTML is not UTF-8") from exc
    if reference_text.count(POSITIVE_FLAP) != 1:
        raise ProofError("original source must contain exactly one pinned flap expression")
    expected_negative = reference_text.replace(POSITIVE_FLAP, NEGATIVE_FLAP, 1)
    if negative_text != expected_negative or repaired_text != reference_text:
        raise ProofError("the negative/repaired revisions changed source beyond the flap expression")

    return {
        "pair_status": "technical_motion_verified",
        "job_id": before["job_id"],
        "before_rev": before["rev"],
        "after_rev": after["rev"],
        "same_job": True,
        "consecutive_revisions": True,
        "same_locked_calibration": True,
        "asset_pins_unchanged_from_original": True,
        "historical_project_defaults_materialized": normalized_defaults,
        "source_delta": "only the flap expression was frozen, then restored byte-for-byte",
        "before_motion_status": before["motion_status"],
        "after_motion_status": after["motion_status"],
        "editorial_status": "pending_review",
    }


def editorial_status(review: dict | None, artifact_sha256: str, *,
                     expected_reviewer: str | None = None) -> str:
    """Accept only explicit reviewer-bound 1× observation, never player completion."""
    if not isinstance(review, dict) or not isinstance(expected_reviewer, str) or not expected_reviewer.strip():
        return "pending_review"
    if (review.get("kind") != "playback_1x" or review.get("status") != "pass"
            or review.get("reviewer") != expected_reviewer
            or review.get("artifact_sha256") != artifact_sha256
            or review.get("speed") != 1.0
            or not isinstance(review.get("observation"), str)
            or not review["observation"].strip()
            or not isinstance(review.get("evidence_ref"), str)
            or not review["evidence_ref"].strip()
            or any(review.get(key) is True for key in
                   ("automatic_completion", "auto_completed", "player_completion"))):
        return "pending_review"
    return "pass"


def _probe_video(path: Path) -> dict:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-count_frames", "-show_streams", "-show_format",
             "-of", "json", str(path)], capture_output=True, text=True, timeout=60, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProofError(f"FFprobe could not inspect {path}") from exc
    if result.returncode:
        raise ProofError(f"FFprobe failed: {result.stderr.strip()[-500:]}")
    try:
        doc = json.loads(result.stdout)
    except ValueError as exc:
        raise ProofError("FFprobe returned invalid JSON") from exc
    streams = doc.get("streams")
    if not isinstance(streams, list):
        raise ProofError("FFprobe returned no stream list")
    video = [stream for stream in streams if stream.get("codec_type") == "video"]
    audio = [stream for stream in streams if stream.get("codec_type") == "audio"]
    subtitles = [stream for stream in streams if stream.get("codec_type") == "subtitle"]
    if len(video) != 1 or audio or subtitles:
        raise ProofError("proof preview must contain one video stream and no audio/subtitle streams")
    stream = video[0]
    try:
        width, height = int(stream["width"]), int(stream["height"])
        frame_count = int(stream.get("nb_read_frames") or stream.get("nb_frames"))
        fps = stream.get("avg_frame_rate")
        duration = float(doc["format"]["duration"])
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ProofError("FFprobe stream properties are incomplete") from exc
    if (stream.get("codec_name") != "h264" or width != 1080 or height != 1920
            or fps != "30/1" or frame_count != 210 or abs(duration - 7.0) > 0.02):
        raise ProofError("preview properties differ from the pinned 1080x1920/30 FPS/7 s contract")
    try:
        decoded = subprocess.run(
            ["ffmpeg", "-hide_banner", "-v", "error", "-xerror", "-i", str(path),
             "-map", "0:v:0", "-f", "null", "-"],
            capture_output=True, text=True, timeout=120, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProofError(f"full FFmpeg decode did not complete: {path}") from exc
    if decoded.returncode:
        raise ProofError(f"full FFmpeg decode failed: {decoded.stderr.strip()[-500:]}")
    pts, duration_ms = reference_probe(path)
    if len(pts) != 210 or abs(float(duration_ms) - 7000) > 20:
        raise ProofError("reference PTS count or duration does not match the 210-frame video")
    return {
        "codec": stream["codec_name"],
        "width": width,
        "height": height,
        "fps": fps,
        "frame_count": frame_count,
        "duration_seconds": duration,
        "audio_streams": 0,
        "subtitle_streams": 0,
        "full_decode": "pass",
        "pts_count": len(pts),
        "duration_ms": float(duration_ms),
    }


def _selected_pts(pts: list, target_times: list[int]) -> list[float]:
    selected = []
    for target in target_times:
        index = bisect.bisect_right(pts, target) - 1
        if index < 0:
            raise ProofError(f"no decoded PTS at or before {target} ms")
        selected.append(float(pts[index]))
    if len(set(selected)) != len(selected):
        raise ProofError("locked calibration samples do not map to distinct decoded PTS")
    return selected


def _body_track(pts_ms: list[float]) -> list[dict[str, Any]]:
    track = []
    for time_ms in pts_ms:
        seconds = time_ms / 1000
        track.append({
            "time_ms": time_ms,
            "anchor": [540 + 142 * math.sin(seconds * 0.63),
                       888 - 132 * math.sin(seconds * 0.83)],
            "angle_deg": math.degrees(0.065 * math.sin(seconds * 0.72)),
            "shot": "moth-flight",
            "confidence": 1.0,
        })
    return track


def _calibration_from_reference(video: Path) -> dict:
    pts_ms, duration_ms = reference_probe(video)
    if len(pts_ms) != 210 or float(duration_ms) < 2800:
        raise ProofError("original M2 reference does not contain the calibration interval")
    selected = _selected_pts(pts_ms, SAMPLE_TIMES_MS)
    return {
        "schema_version": 1,
        "locked": True,
        "intent": "flap",
        "action": {
            "before_ms": 250,
            "start_ms": 500,
            "during_ms": DURING_TIMES_MS,
            "end_ms": 2500,
            "after_ms": 2750,
        },
        "requested_sample_times_ms": SAMPLE_TIMES_MS,
        "reference_selected_pts_ms": selected,
        "wing_roi": WING_ROI,
        "reference_roi": REFERENCE_ROI,
        "body_track": _body_track(selected),
        "body_track_method": (
            "body-sprite center and unchanged canvas rotation evaluated from the original source "
            "transform at reference-selected PTS; source hinge values are not a motion measurement"
        ),
        "sample_tolerance_ms": 20,
        "track_tolerance_ms": 250,
        "min_track_confidence": 0.9,
        "blank_frame_contrast": 1.0,
        "min_crop_contrast": 5.0,
        "movement_threshold": 0.04,
        "still_threshold": 0.025,
        "calibration_basis": {
            "good_reference_job_id": M2_JOB_ID,
            "good_reference_rev": M2_REV,
            "good_reference_run_id": M2_RUN_ID,
            "control_intent": "hold",
        },
    }


def _compose(original_html: str, metadata: dict, *, freeze_flap: bool) -> dict:
    html = original_html
    if freeze_flap:
        if html.count(POSITIVE_FLAP) != 1:
            raise ProofError("source must contain exactly one flap expression to freeze")
        html = html.replace(POSITIVE_FLAP, NEGATIVE_FLAP, 1)
    elif html.count(POSITIVE_FLAP) != 1:
        raise ProofError("positive source flap expression is not the exact original")
    values = {
        key: metadata[key]
        for key in ("width", "height", "fps", "duration_frames", "audio_enabled", "subtitles_enabled")
    }
    values.update({
        "html": html,
        "subtitle_mode": "none",
        "assets": [{"id": item["id"], "version": item["version"]}
                   for item in metadata.get("assets", [])],
    })
    if metadata.get("audio_tracks"):
        raise ProofError("M2 moth source unexpectedly contains audio tracks")
    return values


def _tool(name: str, args: dict, store: JobStore) -> dict:
    with patch.object(tools, "_store", return_value=store):
        raw = tools.dispatch(name, args)
    try:
        response = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ProofError(f"{name} returned malformed JSON") from exc
    if not isinstance(response, dict) or response.get("ok") is not True:
        error = response.get("error", {}) if isinstance(response, dict) else {}
        raise ProofError(f"{name} failed: {error.get('code', 'unknown')}: {error.get('message', '')}")
    return response["data"]


def _create_job(store: JobStore, brief: str, composition: dict) -> dict:
    job = _tool("ump_job", {"action": "create", "payload": {
        "brief": brief, "mode": "autonomous", "requirements": {"checkpoints": []},
    }}, store)
    return _tool("ump_job", {"action": "write_composition", "payload": {
        "job_id": job["job_id"], "rev": job["rev"], "value": composition,
    }}, store)


def _render_preview(store: JobStore, job_id: str, rev: int) -> dict:
    """Call the real `ump_render` route but wait synchronously for its worker child."""
    child_result: dict[str, Any] = {}
    real_spawn = worker._spawn

    def spawn_and_wait(argv):
        child = real_spawn(argv)
        try:
            child_result["returncode"] = child.wait(timeout=worker.RUN_TIMEOUT + 60)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(child.pid, 9)
            except ProcessLookupError:
                pass
            child.wait(timeout=10)
            child_result["returncode"] = child.returncode
            child_result["timeout"] = True
        return child

    with patch.object(worker, "_spawn", side_effect=spawn_and_wait):
        data = _tool("ump_render", {"job_id": job_id, "rev": rev, "target": "preview"}, store)
    run_id = data.get("run_id")
    if not isinstance(run_id, str):
        raise ProofError("ump_render did not return a JobStore run ID")
    if store.get_run(run_id)["status"] == "running":
        worker.reconcile(store, job_id)
    run = store.get_run(run_id)
    if child_result.get("timeout"):
        raise ProofError(f"worker exceeded {worker.RUN_TIMEOUT}s; run {run_id} retained as {run['status']}")
    return run


def _revision_pin_map(metadata: dict) -> dict[str, dict[str, Any]]:
    assets = metadata.get("assets", [])
    if not isinstance(assets, list):
        raise ProofError("revision asset metadata is malformed")
    result = {}
    for item in assets:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise ProofError("revision asset pin is malformed")
        result[item["path"]] = {"sha256": item.get("sha256"), "size_bytes": item.get("size_bytes")}
    return result


def _record_run(store: JobStore, run: dict, calibration_sha256: str) -> dict:
    if (run.get("status") != "finished" or run.get("target") != "preview"
            or not isinstance(run.get("artifact"), dict)):
        raise ProofError(f"preview worker run {run.get('run_id')} did not finish: {run.get('error')}")
    job_id, rev, run_id = run["job_id"], run["rev"], run["run_id"]
    job = store.get(job_id)
    preview_artifact = run["artifact"]
    if (preview_artifact.get("job_id") != job_id or preview_artifact.get("rev") != rev
            or preview_artifact.get("kind") != "preview"
            or preview_artifact.get("id") != run.get("artifact_id")):
        raise ProofError(f"preview artifact is not bound to run {run_id}")
    source_artifacts = [artifact for artifact in job["artifacts"]
                        if artifact["rev"] == rev and artifact["kind"] == "source"
                        and Path(artifact["path"]).parent == Path(preview_artifact["path"]).parent]
    if len(source_artifacts) != 1:
        raise ProofError(f"run {run_id} does not have exactly one registered source ZIP")
    preview_path = _data_file(store.root, preview_artifact["path"])
    source_path = _data_file(store.root, source_artifacts[0]["path"])
    payload = store.get_revision_payload(job_id, rev)
    composition = payload.get("composition")
    metadata = composition.get("metadata") if isinstance(composition, dict) else None
    if not isinstance(metadata, dict):
        raise ProofError(f"revision {job_id}/{rev} has no composition metadata")
    record = {
        "job_id": job_id,
        "rev": rev,
        "run_id": run_id,
        "calibration_sha256": calibration_sha256,
        "preview": {
            "artifact_id": preview_artifact["id"],
            "path": preview_artifact["path"],
            "absolute_path": str(preview_path),
            "sha256": _sha256_file(preview_path),
            "size_bytes": preview_path.stat().st_size,
        },
        "source": {
            "artifact_id": source_artifacts[0]["id"],
            "path": source_artifacts[0]["path"],
            "absolute_path": str(source_path),
            "sha256": _sha256_file(source_path),
            "size_bytes": source_path.stat().st_size,
            "revision_path": f"jobs/{job_id}/revisions/{rev}/composition",
            "html_sha256": composition.get("html_sha256"),
            "project_sha256": composition.get("project_sha256"),
            "asset_pins": _revision_pin_map(metadata),
        },
        "technical_status": "pending",
        "motion_status": "pending_review",
        "editorial_status": "pending_review",
        "repair_attempts": 0,
    }
    _source_files(store.root, record)
    return record


def _catalog_assets_match(root: Path, payload: dict) -> dict[str, dict[str, Any]]:
    metadata = payload.get("composition", {}).get("metadata", {})
    pins = _revision_pin_map(metadata)
    catalog = {(item["id"], item["version"]): item for item in AssetStore(root).list()}
    result = {}
    for pin in metadata.get("assets", []):
        entry = catalog.get((pin.get("id"), pin.get("version")))
        if entry is None:
            raise ProofError(f"asset catalogue pin missing: {pin.get('id')} v{pin.get('version')}")
        path = _data_file(root, entry["path"])
        if (_sha256_file(path) != pin.get("sha256") or path.stat().st_size != pin.get("size_bytes")
                or entry.get("sha256") != pin.get("sha256")):
            raise ProofError(f"revision pin differs from asset catalogue: {pin['id']} v{pin['version']}")
        result[pin["path"]] = {
            "id": pin["id"], "version": pin["version"], "sha256": pin["sha256"],
            "size_bytes": pin["size_bytes"],
        }
    if set(result) != set(pins):
        raise ProofError("asset catalogue verification did not cover every revision pin")
    return result


def _original_reference(store: JobStore) -> tuple[dict, dict, dict]:
    job = store.get(M2_JOB_ID)
    if job.get("rev") != M2_REV or job.get("stage") != "final_ready":
        raise ProofError("original M2 moth job is not unchanged at revision 2/final_ready")
    run = store.get_run(M2_RUN_ID)
    if (run.get("job_id") != M2_JOB_ID or run.get("rev") != M2_REV
            or run.get("target") != "preview" or run.get("status") != "finished"):
        raise ProofError("original M2 preview run is not the pinned finished run")
    preview_path = _data_file(store.root, run["artifact"]["path"])
    if _sha256_file(preview_path) != M2_PREVIEW_SHA256:
        raise ProofError("original M2 preview SHA-256 differs from the pinned value")
    source = next((item for item in job["artifacts"]
                   if item["rev"] == M2_REV and item["kind"] == "source"
                   and Path(item["path"]).parent == Path(run["artifact"]["path"]).parent), None)
    if source is None:
        raise ProofError("original M2 source ZIP is not registered to its preview run")
    source_path = _data_file(store.root, source["path"])
    if _sha256_file(source_path) != M2_SOURCE_SHA256:
        raise ProofError("original M2 source ZIP hash differs from the progress pin")
    payload = store.get_revision_payload(M2_JOB_ID, M2_REV)
    html_path = _data_file(store.root, f"jobs/{M2_JOB_ID}/revisions/{M2_REV}/composition/index.html")
    if _sha256_file(html_path) != M2_HTML_SHA256:
        raise ProofError("original M2 revision HTML differs from the pinned hash")
    _catalog_assets_match(store.root, payload)
    original_signature = {
        "job": {key: job.get(key) for key in ("job_id", "rev", "stage", "status", "artifacts")},
        "run": {key: run.get(key) for key in ("run_id", "job_id", "rev", "target", "status", "artifact_id")},
        "revision_payload_sha256": _sha256_bytes(_canonical_bytes(payload)),
        "preview_sha256": M2_PREVIEW_SHA256,
        "source_sha256": M2_SOURCE_SHA256,
        "html_sha256": M2_HTML_SHA256,
    }
    return payload, original_signature, {"job": job, "run": run, "source_path": source_path,
                                         "preview_path": preview_path, "payload": payload}


def _assert_original_unchanged(store: JobStore, signature: dict) -> None:
    job = store.get(M2_JOB_ID)
    run = store.get_run(M2_RUN_ID)
    payload = store.get_revision_payload(M2_JOB_ID, M2_REV)
    actual = {
        "job": {key: job.get(key) for key in ("job_id", "rev", "stage", "status", "artifacts")},
        "run": {key: run.get(key) for key in ("run_id", "job_id", "rev", "target", "status", "artifact_id")},
        "revision_payload_sha256": _sha256_bytes(_canonical_bytes(payload)),
        "preview_sha256": _sha256_file(_data_file(store.root, run["artifact"]["path"])),
        "source_sha256": _sha256_file(_data_file(store.root, next(
            item["path"] for item in job["artifacts"] if item["rev"] == M2_REV and item["kind"] == "source"
        ))),
        "html_sha256": _sha256_file(_data_file(
            store.root, f"jobs/{M2_JOB_ID}/revisions/{M2_REV}/composition/index.html",
        )),
    }
    if actual != signature:
        raise ProofError("original M2 job, run, revision, or media changed during T6")


def _preflight(root: Path) -> dict:
    projection = resources.estimate_render_projection("custom_render")
    phase = projection["phases"]["initial"]
    sample = resources.probe_resources(
        data_root=root, root=Path("/"), temp_root=sandbox.SHM_ROOT,
    )
    sample["projected_temp_bytes"] = phase["temporary_bytes"]
    budget = resources.check_budget(sample, phase["data_root_bytes"], "m4_creative_proof")
    return {"sample": sample, "projection": projection, "budget": budget}


def _run_focused_proof_tests(root: Path) -> dict:
    command = [sys.executable, "-W", "error::ResourceWarning", "-m", "unittest",
               "discover", "-s", "tests", "-p", "test_m4_proof.py"]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(worker.CODE_ROOT)
    env["UMP_DATA_ROOT"] = str(root)
    result = subprocess.run(command, cwd=worker.CODE_ROOT, env=env,
                            capture_output=True, text=True, timeout=120, check=False)
    output = (result.stdout + result.stderr).strip()
    return {"command": "PYTHONPATH=. UMP_DATA_ROOT=<DATA_ROOT> python3 -W error::ResourceWarning "
                       "-m unittest discover -s tests -p 'test_m4_proof.py'",
            "returncode": result.returncode, "status": "pass" if result.returncode == 0 else "fail",
            "output": output}


def _decode_crop_frames(video: Path, record: dict, out: Path, role: str,
                        width: int, height: int) -> list[dict]:
    try:
        from PIL import Image
    except ImportError as exc:
        raise ProofError("Pillow is required for full-resolution crop evidence") from exc
    samples = record.get("motion_measurements", {}).get("samples", [])
    if not samples:
        raise ProofError(f"no decoded motion samples available for {role} crops")
    indices = sorted({sample["frame_index"] for sample in samples})
    expression = "select=" + "+".join(f"eq(n\\,{index})" for index in indices)
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-xerror", "-i", str(video),
               "-map", "0:v:0", "-vf", expression, "-fps_mode", "passthrough",
               "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"]
    try:
        child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as exc:
        raise ProofError(f"cannot decode crop frames for {role}") from exc
    frame_bytes = width * height * 3
    crops_root = out / "crops"
    crops_root.mkdir(mode=0o700, exist_ok=True)
    role_dir = crops_root / role
    role_dir.mkdir(mode=0o700)
    crop_records = []
    frames_by_index = {}
    assert child.stdout is not None
    try:
        for index in indices:
            raw = child.stdout.read(frame_bytes)
            if len(raw) != frame_bytes:
                raise ProofError(f"decoded crop frame is incomplete for {role}, frame {index}")
            frames_by_index[index] = Image.frombytes("RGB", (width, height), raw)
        if child.stdout.read(1):
            raise ProofError(f"unexpected extra crop frames for {role}")
        stderr = child.stderr.read().decode("utf-8", "replace") if child.stderr else ""
        code = child.wait(timeout=30)
    except Exception:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=10)
        raise
    finally:
        if child.stdout is not None:
            child.stdout.close()
        if child.stderr is not None:
            child.stderr.close()
    if code:
        raise ProofError(f"crop frame decode failed for {role}: {stderr.strip()[-400:]}")

    for sample in samples:
        frame = frames_by_index[sample["frame_index"]]
        anchor_x, anchor_y = sample["anchor"]
        angle = math.radians(sample["angle_deg"])
        cosine, sine = math.cos(angle), math.sin(angle)
        entry = {"phase": sample["phase"], "target_ms": sample["target_ms"],
                 "pts_ms": sample["pts_ms"], "frame_index": sample["frame_index"]}
        for name, region in (("wing", WING_ROI), ("reference", REFERENCE_ROI)):
            x, y, crop_w, crop_h = region
            transform = (
                cosine, -sine, anchor_x + cosine * x - sine * y,
                sine, cosine, anchor_y + sine * x + cosine * y,
            )
            crop = frame.transform(
                (crop_w, crop_h), Image.Transform.AFFINE, transform,
                resample=Image.Resampling.BICUBIC,
            )
            buffer = io.BytesIO()
            crop.save(buffer, format="PNG", optimize=False)
            name_path = f"{name}-{sample['phase']}-{sample['pts_ms']:07.3f}ms.png"
            path = role_dir / name_path
            _write_once(path, buffer.getvalue())
            entry[name] = {
                "path": str(path), "sha256": _sha256_file(path),
                "width": crop_w, "height": crop_h,
            }
        crop_records.append(entry)
    return crop_records


def _contact_sheet(video: Path, role: str, out: Path) -> dict:
    sheet_dir = out / "contact-sheets"
    sheet_dir.mkdir(mode=0o700, exist_ok=True)
    role_dir = sheet_dir / role
    role_dir.mkdir(mode=0o700)
    report = make_contact_sheet(video, SAMPLE_TIMES_MS, role_dir)
    return {
        "sheet_path": str(role_dir / "sheet.png"),
        "frames_path": str(role_dir / "frames.json"),
        "pts_records": report["frames"],
    }


def _review(record: dict, motion_status: str, role: str, crops: list[dict],
            technical: dict) -> dict:
    binding = {
        "job_id": record["job_id"], "rev": record["rev"], "run_id": record["run_id"],
        "artifact_sha256": record["preview"]["sha256"],
    }
    crop_refs = [f"{role}:{sample[name]['path']}" for sample in crops
                 for name in ("wing", "reference")]
    checks = [
        {"category": "technical", "status": "pass", "required": True,
         "criterion": "h264_1080x1920_30fps_210_frames_full_decode", "evidence_ref": role,
         "details": technical},
        {"category": "motion", "status": motion_status, "required": True,
         "criterion": "required_wing_flap" if role != "hold_control" else "intentional_hold",
         "intent": "flap" if role != "hold_control" else "hold",
         "evidence_refs": crop_refs,
         "measurements": record["motion_measurements"]},
        {"category": "visual", "status": "pending_review", "advisory": True,
         "criterion": "frame_inspection", "evidence_ref": role},
        {"category": "editorial", "status": "pending_review", "advisory": True,
         "criterion": "pacing", "requires_playback": True,
         "range_ms": [500, 2500], "evidence_refs": crop_refs},
    ]
    observations = [{
        "shot": "moth-flight", "time_ms": [500, 2500],
        "intent": "flap" if role != "hold_control" else "hold",
        "actual": motion_status,
        "severity": "major" if motion_status == "fail" else "none",
        "suspected_cause": "flap source expression is frozen" if role == "negative" else None,
        "repair": "restore the original flap expression" if role == "negative" else None,
        "impact": "required articulation absent" if motion_status == "fail" else "calibrated pixel motion matches intent",
        "status": motion_status,
        "reviewer": None,
        "observation_type": "decoded_pixels",
        "evidence_refs": crop_refs,
    }]
    return build_review(binding, checks, observations)


def _measure(record: dict, calibration: dict, intent: str, role: str,
             root: Path, out: Path) -> tuple[dict, dict]:
    media_path = _data_file(root, record["preview"]["path"])
    technical = _probe_video(media_path)
    record["technical_status"] = "pass"
    measurements = measure_motion(media_path, calibration)
    status = assess_motion(intent, measurements, calibration)
    record["motion_measurements"] = measurements
    record["motion_status"] = status
    record["technical_media"] = technical
    record["contact_sheet"] = _contact_sheet(media_path, role, out)
    width, height = technical["width"], technical["height"]
    record["crop_evidence"] = _decode_crop_frames(media_path, record, out, role, width, height)
    return measurements, technical


def _resource_report(root: Path, run_id: str) -> dict:
    relative = f"runs/{run_id}/resources.json"
    path = _data_file(root, relative)
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProofError(f"worker resource report is unavailable for run {run_id}") from exc
    checks = report.get("budget_checks")
    if (report.get("status") != "finished" or not isinstance(checks, list) or not checks
            or any(item.get("budget", {}).get("decision") != "allow" for item in checks)):
        raise ProofError(f"T3 resource gate did not allow every phase for run {run_id}")
    return {"path": relative, "absolute_path": str(path), "sha256": _sha256_file(path),
            "report": report}


def _record_from_original(root: Path, store: JobStore, payload: dict, calibration: dict,
                          out: Path) -> dict:
    composition = payload["composition"]
    metadata = composition["metadata"]
    source_artifacts = [
        item for item in store.get(M2_JOB_ID)["artifacts"]
        if item["rev"] == M2_REV and item["kind"] == "source"
        and Path(item["path"]).parent == Path(store.get_run(M2_RUN_ID)["artifact"]["path"]).parent
    ]
    if len(source_artifacts) != 1:
        raise ProofError("original M2 source ZIP is not uniquely registered to its preview run")
    source_artifact = source_artifacts[0]
    preview_artifact = store.get_run(M2_RUN_ID)["artifact"]
    original = {
        "job_id": M2_JOB_ID, "rev": M2_REV, "run_id": M2_RUN_ID,
        "calibration_sha256": _sha256_bytes(_canonical_bytes(calibration)),
        "preview": {
            "artifact_id": preview_artifact["id"], "path": preview_artifact["path"],
            "absolute_path": str(_data_file(root, preview_artifact["path"])),
            "sha256": M2_PREVIEW_SHA256,
            "size_bytes": _data_file(root, preview_artifact["path"]).stat().st_size,
        },
        "source": {
            "artifact_id": source_artifact["id"], "path": source_artifact["path"],
            "absolute_path": str(_data_file(root, source_artifact["path"])),
            "sha256": M2_SOURCE_SHA256,
            "size_bytes": _data_file(root, source_artifact["path"]).stat().st_size,
            "revision_path": f"jobs/{M2_JOB_ID}/revisions/{M2_REV}/composition",
            "html_sha256": composition["html_sha256"],
            "project_sha256": composition["project_sha256"],
            "asset_pins": _revision_pin_map(metadata),
        },
        "technical_status": "pending", "motion_status": "pending_review",
        "editorial_status": "pending_review", "repair_attempts": 0,
    }
    _source_files(root, original)
    measurements, technical = _measure(original, calibration, "flap", "good_reference", root, out)
    if not measurements.get("reliable") or original["motion_status"] != "pass":
        raise ProofError("original M2 good-motion reference did not pass locked pixel calibration")
    return original


def _index_artifact_record(record: dict, resources_record: dict | None = None) -> dict:
    output = dict(record)
    if resources_record is not None:
        output["resource_report"] = resources_record
    return output


def _run_capture(store: JobStore, job_id: str, rev: int, calibration_sha256: str,
                 index: dict, key: str) -> dict:
    run = _render_preview(store, job_id, rev)
    index.setdefault("runs", []).append({
        "job_id": job_id, "rev": rev, "run_id": run.get("run_id"),
        "target": run.get("target"), "status": run.get("status"), "error": run.get("error"),
        "purpose": key,
    })
    if run.get("status") != "finished":
        raise ProofError(f"{key} preview run {run.get('run_id')} failed: {run.get('error')}")
    record = _record_run(store, run, calibration_sha256)
    record["resource_report"] = _resource_report(store.root, run["run_id"])
    return record


def _capture_test_review(store: JobStore, job_id: str, rev: int, review: dict,
                         *, value: dict | None = None, request_id: str) -> dict:
    payload = {"job_id": job_id, "rev": rev, "review": review, "request_id": request_id}
    if value is not None:
        payload["value"] = value
    return _tool("ump_job", {"action": "repair", "payload": payload}, store)


def _check_output_location(root: Path, out: Path) -> None:
    expected = root / "evidence" / "m4"
    if out.parent != expected:
        raise ProofError(f"--out must be a fresh timestamp directory under {expected}")


def _build_proof(root: Path, out: Path, index: dict) -> dict:
    store = JobStore(root)
    index["preflight"] = _preflight(root)
    if index["preflight"]["budget"].get("decision") != "allow":
        raise ProofError("T3 preflight did not allow the render workload; no render started")
    test_result = _run_focused_proof_tests(root)
    index["tests"] = [test_result]
    if test_result["status"] != "pass":
        raise ProofError("focused T6 proof tests failed before rendering")

    original_payload, original_signature, original_state = _original_reference(store)
    original_html_path = _data_file(
        root, f"jobs/{M2_JOB_ID}/revisions/{M2_REV}/composition/index.html",
    )
    original_html = original_html_path.read_text(encoding="utf-8")
    if original_html.count(POSITIVE_FLAP) != 1:
        raise ProofError("original M2 source does not match the pinned positive flap expression")
    original_metadata = original_payload["composition"]["metadata"]
    negative_composition = _compose(original_html, original_metadata, freeze_flap=True)
    positive_composition = _compose(original_html, original_metadata, freeze_flap=False)

    reference_media = original_state["preview_path"]
    calibration = _calibration_from_reference(reference_media)
    calibration_sha256 = _sha256_bytes(_canonical_bytes(calibration))
    _write_json_once(out / "calibration.json", calibration)
    index["calibration"] = {
        "path": str(out / "calibration.json"), "sha256": calibration_sha256,
        "locked_before_pair": True, "intent": calibration["intent"],
        "action": calibration["action"], "requested_sample_times_ms": SAMPLE_TIMES_MS,
        "reference_selected_pts_ms": calibration["reference_selected_pts_ms"],
        "wing_roi_body_local_px": WING_ROI,
        "reference_roi_body_local_px": REFERENCE_ROI,
        "body_track_method": calibration["body_track_method"],
        "thresholds": {key: calibration[key] for key in (
            "movement_threshold", "still_threshold", "sample_tolerance_ms",
            "track_tolerance_ms", "min_track_confidence", "min_crop_contrast",
        )},
    }
    reference = _record_from_original(root, store, original_payload, calibration, out)
    index["good_reference"] = _index_artifact_record(reference)

    control_composition = _compose(original_html, original_metadata, freeze_flap=True)
    control_job = _create_job(
        store, "M4 calibration control: moth motion intentionally held; all other source is copied.",
        control_composition,
    )
    control_job_id, control_rev = control_job["job_id"], control_job["rev"]
    control = _run_capture(store, control_job_id, control_rev, calibration_sha256,
                            index, "intentional_hold_calibration_control")
    measurements, technical = _measure(control, calibration, "hold", "hold_control", root, out)
    if not measurements.get("reliable") or control["motion_status"] != "pass":
        index["calibration_control"] = _index_artifact_record(control)
        raise ProofError("separate intentional-hold control did not pass; demo pair not started")
    control["editorial_status"] = editorial_status(None, control["preview"]["sha256"])
    index["calibration_control"] = _index_artifact_record(control)
    index["calibration"]["good_reference_motion"] = reference["motion_measurements"]
    index["calibration"]["hold_control_motion"] = control["motion_measurements"]
    index["calibration"]["separated"] = True

    demo = _create_job(
        store, "M4 proof copy of M2 moth: freeze only wing flap, then restore it through T5 repair.",
        negative_composition,
    )
    job_id, before_rev = demo["job_id"], demo["rev"]
    index["demo"] = {"job_id": job_id, "initial_revision": before_rev,
                     "initial_repair_attempts": 0}
    before = _run_capture(store, job_id, before_rev, calibration_sha256, index, "negative_preview")
    measurements, technical = _measure(before, calibration, "flap", "negative", root, out)
    before["editorial_status"] = editorial_status(None, before["preview"]["sha256"])
    if not measurements.get("reliable") or before["motion_status"] != "fail":
        index["demo"]["before"] = _index_artifact_record(before)
        raise ProofError("negative demo preview did not fail from decoded MP4 pixels; repair not started")
    before_crops = before["crop_evidence"]
    before_review = _review(before, before["motion_status"], "negative", before_crops, technical)
    repair = _capture_test_review(
        store, job_id, before_rev, before_review, value=positive_composition,
        request_id="m4-restore-original-flap",
    )
    if (repair.get("decision") != "repair" or repair.get("rev") != before_rev + 1
            or repair.get("attempts") != 1 or repair.get("initial_rev") != before_rev):
        index["demo"]["before"] = _index_artifact_record(before)
        index["demo"]["repair"] = repair
        raise ProofError("T5 did not create the first bounded repair revision from the failed negative preview")
    after_rev = repair["rev"]
    index["demo"]["repair"] = repair

    after = _run_capture(store, job_id, after_rev, calibration_sha256, index, "repaired_preview")
    measurements, technical = _measure(after, calibration, "flap", "repaired", root, out)
    after["repair_attempts"] = repair["attempts"]
    after["editorial_status"] = editorial_status(None, after["preview"]["sha256"])
    if not measurements.get("reliable") or after["motion_status"] != "pass":
        index["demo"]["before"] = _index_artifact_record(before)
        index["demo"]["after"] = _index_artifact_record(after)
        raise ProofError("repaired demo preview did not pass the locked decoded-pixel motion threshold")
    after_review = _review(after, after["motion_status"], "repaired", after["crop_evidence"], technical)
    final_review = _capture_test_review(
        store, job_id, after_rev, after_review, request_id="m4-record-repaired-review",
    )
    if final_review.get("decision") != "stop" or final_review.get("attempts") != 1:
        raise ProofError("T5 did not close the one-attempt motion issue without creating another revision")
    if store.get(job_id)["rev"] != after_rev:
        raise ProofError("T5 review unexpectedly created another revision")

    before["repair_attempts"] = 0
    before["t1_quality_review"] = before_review
    after["t1_quality_review"] = after_review
    after["t5_review_result"] = final_review
    pair_validation = validate_pair(root, reference, before, after, calibration)
    index["demo"].update({
        "before": _index_artifact_record(before),
        "after": _index_artifact_record(after),
        "pair_validation": pair_validation,
        "t5_initial_repair": repair,
        "t5_final_review": final_review,
        "same_job": before["job_id"] == after["job_id"],
        "consecutive_revisions": after["rev"] == before["rev"] + 1,
    })
    _assert_original_unchanged(store, original_signature)
    index["original_m2_unchanged"] = True
    index["technical_status"] = "pass"
    index["motion_status"] = "fail_then_repaired_pass"
    index["visual_status"] = "pending_review"
    index["pacing_status"] = "pending_review"
    index["editorial_status"] = "pending_review"
    index["m4_status"] = "open"
    index["overall_status"] = "technical_motion_verified_review_pending"
    index["pending_decisions"] = [
        "Operator must play both MP4s at 1x over 500-2500 ms and judge whether the moth wing action reads clearly and intentionally.",
        "Operator must inspect the supplied full-resolution wing/reference crops and contact sheets for framing or visual defects.",
        "No playback_1x review, visual approval, final acceptance, or M4 closure is claimed by this verifier.",
    ]
    index["operator_handoff"] = {
        "before_mp4": before["preview"]["absolute_path"],
        "before_sha256": before["preview"]["sha256"],
        "before_size_bytes": before["preview"]["size_bytes"],
        "after_mp4": after["preview"]["absolute_path"],
        "after_sha256": after["preview"]["sha256"],
        "after_size_bytes": after["preview"]["size_bytes"],
        "review_interval_ms": [500, 2500],
        "question": "Pada 1x, apakah kepakan sayap terbaca jelas di klip hasil repair, sementara kontrol hold memang terlihat diam? Ada tempo, framing, atau artefak visual yang perlu diperbaiki?",
    }
    return index


def _resume_proof(root: Path, out: Path, index: dict, prior: dict, prior_path: Path) -> dict:
    """Re-validate retained worker runs without creating another job or capture."""
    run_ids = resume_run_ids(prior)
    calibration_path = prior_path.parent / "calibration.json"
    calibration_path = _data_file(root, str(calibration_path.relative_to(root)))
    try:
        calibration = json.loads(calibration_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProofError("retained calibration is unreadable") from exc
    calibration_sha = _sha256_bytes(_canonical_bytes(calibration))
    if (not isinstance(prior.get("calibration"), dict)
            or calibration_sha != prior["calibration"].get("sha256")
            or calibration.get("locked") is not True):
        raise ProofError("retained locked calibration hash does not match")
    _write_json_once(out / "calibration.json", calibration)
    index["resumed_from"] = {"path": str(prior_path), "sha256": _sha256_file(prior_path),
                             "reason": prior["blocker"]["message"],
                             "new_worker_captures": 0}
    index["preflight"] = _preflight(root)
    focused = _run_focused_proof_tests(root)
    index["tests"] = [focused]
    if focused["status"] != "pass":
        raise ProofError("focused T6 proof tests failed before revalidation")
    index["calibration"] = {**prior["calibration"], "path": str(out / "calibration.json")}
    store = JobStore(root)
    original_payload, original_signature, _ = _original_reference(store)
    reference = _record_from_original(root, store, original_payload, calibration, out)
    index["good_reference"] = _index_artifact_record(reference)

    def restored(purpose: str, role: str, intent: str) -> tuple[dict, dict]:
        run = store.get_run(run_ids[purpose])
        record = _record_run(store, run, calibration_sha)
        record["resource_report"] = _resource_report(root, run["run_id"])
        measurements, technical = _measure(record, calibration, intent, role, root, out)
        index["runs"].append({"job_id": run["job_id"], "rev": run["rev"],
                              "run_id": run["run_id"], "target": run["target"],
                              "status": run["status"], "purpose": purpose,
                              "reused_without_capture": True})
        if not measurements.get("reliable"):
            raise ProofError(f"retained {role} run has unreliable decoded motion evidence")
        return record, technical

    control, _ = restored("intentional_hold_calibration_control", "hold_control", "hold")
    if control["motion_status"] != "pass":
        raise ProofError("retained intentional-hold control no longer passes")
    index["calibration_control"] = _index_artifact_record(control)
    index["calibration"]["good_reference_motion"] = reference["motion_measurements"]
    index["calibration"]["hold_control_motion"] = control["motion_measurements"]
    index["calibration"]["separated"] = True
    before, before_technical = restored("negative_preview", "negative", "flap")
    after, after_technical = restored("repaired_preview", "repaired", "flap")
    if before["motion_status"] != "fail" or after["motion_status"] != "pass":
        raise ProofError("retained before/after decoded-motion result differs from the original proof")
    if before["job_id"] == M2_JOB_ID or before["job_id"] == control["job_id"]:
        raise ProofError("demo pair must be a new job distinct from M2 and the hold control")
    job = store.get(before["job_id"])
    attempt = store.get_repair_attempt_for_revision(before["job_id"], after["rev"])
    repairs = job.get("repairs", [])
    if (not isinstance(attempt, dict) or attempt.get("initial_rev") != before["rev"]
            or attempt.get("request_id") != "m4-restore-original-flap"
            or attempt.get("status") != "reviewed"
            or len(repairs) != 1 or repairs[0].get("attempts") != 1
            or repairs[0].get("status") != "complete"
            or repairs[0].get("best_run_id") != after["run_id"]):
        raise ProofError("retained T5 repair is not one reviewed attempt linked to the new run")
    before["t1_quality_review"] = _review(before, "fail", "negative",
                                         before["crop_evidence"], before_technical)
    after["repair_attempts"] = 1
    after["t1_quality_review"] = _review(after, "pass", "repaired",
                                        after["crop_evidence"], after_technical)
    pair = validate_pair(root, reference, before, after, calibration)
    index["demo"] = {"job_id": before["job_id"], "initial_revision": before["rev"],
                     "initial_repair_attempts": 0, "before": _index_artifact_record(before),
                     "after": _index_artifact_record(after), "pair_validation": pair,
                     "t5_repair_attempt": {key: attempt[key] for key in (
                         "initial_rev", "issue_id", "request_id", "rev", "status")},
                     "t5_chain": {key: repairs[0][key] for key in (
                         "status", "stop_reason", "best_rev", "best_run_id", "attempts",
                         "remaining", "history")}}
    _assert_original_unchanged(store, original_signature)
    index.update({"original_m2_unchanged": True, "technical_status": "pass",
                  "motion_status": "fail_then_repaired_pass",
                  "visual_status": "pending_review", "pacing_status": "pending_review",
                  "editorial_status": "pending_review", "m4_status": "open",
                  "overall_status": "technical_motion_verified_review_pending"})
    index["operator_handoff"] = {
        "before_mp4": before["preview"]["absolute_path"],
        "before_sha256": before["preview"]["sha256"],
        "before_size_bytes": before["preview"]["size_bytes"],
        "after_mp4": after["preview"]["absolute_path"],
        "after_sha256": after["preview"]["sha256"],
        "after_size_bytes": after["preview"]["size_bytes"],
        "review_interval_ms": [500, 2500],
        "question": "Pada 1x, apakah kepakan sayap di klip repair terbaca jelas? Ada masalah tempo atau framing?",
    }
    index["pending_decisions"] = [
        "Operator must watch both MP4s at 1x over 500-2500 ms and assess pacing/visual clarity.",
        "Only decoded-frame technical/motion verification is complete; M4 creative review remains open.",
    ]
    return index


def resume_run_ids(prior: dict) -> dict[str, str]:
    """Select only the completed run triplet from this known verifier-only failure."""
    expected = ("intentional_hold_calibration_control", "negative_preview", "repaired_preview")
    if (not isinstance(prior, dict) or prior.get("status") != "blocked_or_incomplete"
            or not isinstance(prior.get("blocker"), dict)
            or prior["blocker"].get("message") != "non-motion composition metadata changed across the proof pair"):
        raise ProofError("resume requires the retained project-metadata verifier failure")
    rows = prior.get("runs")
    if not isinstance(rows, list) or len(rows) != len(expected):
        raise ProofError("resume requires exactly the original three worker runs")
    result = {}
    for row, purpose in zip(rows, expected):
        if (not isinstance(row, dict) or row.get("purpose") != purpose
                or row.get("status") != "finished" or not isinstance(row.get("run_id"), str)
                or not 8 <= len(row["run_id"]) <= 64 or row["run_id"] in result.values()):
            raise ProofError("resume worker run sequence is incomplete or duplicated")
        result[purpose] = row["run_id"]
    return result


def _timestamped_output(root: Path) -> Path:
    stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%f")
    return root / "evidence" / "m4" / stamp


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--resume-index", type=Path,
                        help="revalidate a retained metadata-check failure without another render")
    args = parser.parse_args(argv)
    prior = None
    prior_path = None
    try:
        root = args.root.expanduser().resolve(strict=True)
        expected_root = (Path.home() / ".hermes" / "ump-v2").resolve(strict=True)
        if root != expected_root:
            raise ProofError(f"T6 is restricted to the isolated test DATA_ROOT {expected_root}")
        out = args.out.expanduser().absolute()
        _check_output_location(root, out)
        if args.resume_index is not None:
            candidate = args.resume_index.expanduser().absolute()
            if candidate.name != "index.json" or candidate.parent.parent != root / "evidence" / "m4":
                raise ProofError("--resume-index must name a retained M4 evidence index")
            prior_path = _data_file(root, str(candidate.relative_to(root)))
            prior = json.loads(prior_path.read_text(encoding="utf-8"))
            resume_run_ids(prior)
        evidence_dir = create_fresh_evidence_dir(out)
    except (OSError, ProofError, ValueError) as exc:
        print(f"verify_m4: {exc}", file=sys.stderr)
        return 2

    index: dict[str, Any] = {
        "schema_version": 1,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "data_root": str(root),
        "evidence_dir": str(evidence_dir),
        "status": "running",
        "technical_status": "pending",
        "motion_status": "pending_review",
        "visual_status": "pending_review",
        "pacing_status": "pending_review",
        "editorial_status": "pending_review",
        "m4_status": "open",
        "runs": [],
        "tests": [],
        "pending_decisions": [],
    }
    try:
        if prior is not None and prior_path is not None:
            index = _resume_proof(root, evidence_dir, index, prior, prior_path)
        elif prior is None:
            index = _build_proof(root, evidence_dir, index)
        else:
            raise ProofError("resume index path is missing")
        index["status"] = "technical_motion_complete_review_pending"
        exit_code = 0
    except (Exception, SystemExit) as exc:
        index["status"] = "blocked_or_incomplete"
        index["blocker"] = {"type": type(exc).__name__, "message": str(exc)[:1000]}
        index["m4_status"] = "open"
        index["pending_decisions"] = index.get("pending_decisions", []) + [
            "Technical/motion proof did not complete; inspect the exact blocker and retained JobStore/resource state.",
            "No M4 acceptance or 1x playback review is inferred.",
        ]
        exit_code = 1
    try:
        _write_json_once(evidence_dir / "index.json", index)
    except Exception as exc:
        print(f"verify_m4: could not publish fresh evidence index: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "status": index["status"], "index": str(evidence_dir / "index.json"),
        "evidence_dir": str(evidence_dir), "runs": index.get("runs", []),
        "blocker": index.get("blocker"), "m4_status": index.get("m4_status"),
    }, ensure_ascii=False, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
