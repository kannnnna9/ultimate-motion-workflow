import hashlib
import json
import shutil
import struct
import subprocess
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

from ump_core import audio, worker
from ump_core.jobs import JobStore
from ump_core.render_cache import stage_key, validate_unit
from ump_core.subtitles import MOBILE_ASS_STYLE


CAPTURE_INPUTS = {
    "source_sha256": "1" * 64,
    "assets": {"assets/wing.svg": "2" * 64},
    "timeline_sha256": None,
    "browser": "chrome",
    "browser_version": "Chrome 123.0.0.0",
    "backend": "angle-swiftshader",
    "backend_version": "1",
    "width": 32,
    "height": 32,
    "fps": 30,
    "frame_range": [0, 30],
}


def _png(color):
    width = height = 32

    def chunk(kind, payload):
        return (struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))

    raw = (b"\0" + bytes(color) * width) * height
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">2I5B", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


def _capture_unit(path, key, *, total=30, start=0, end=None, indices=None, extra=()):
    end = total if end is None else end
    path.mkdir(mode=0o700, parents=True)
    indices = list(range(start, end)) if indices is None else list(indices)
    frames = []
    for index in indices:
        name = f"f{index:05d}.png"
        data = _png((index % 256, 0, 0))
        (path / name).write_bytes(data)
        frames.append({"index": index, "name": name,
                       "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    for name, data in extra:
        (path / name).write_bytes(data)
    manifest = {
        "schema_version": 1,
        "stage": "capture",
        "key": key,
        "frame_range": [start, end],
        "total_frames": total,
        "width": 32,
        "height": 32,
        "frames": frames,
    }
    (path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _encode_png_sequence(source, output):
    subprocess.run([
        "ffmpeg", "-nostdin", "-v", "error", "-y", "-framerate", "30",
        "-i", str(source / "f%05d.png"), "-c:v", "libx264", "-profile:v", "high",
        "-pix_fmt", "yuv420p", "-crf", "18", "-threads", "2", str(output),
    ], check=True, capture_output=True, timeout=30)


def _video_signature(path):
    probe = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_frames",
        "-show_entries", "frame=best_effort_timestamp_time", "-of", "json", str(path),
    ], check=True, capture_output=True, text=True, timeout=30)
    pts = [float(frame["best_effort_timestamp_time"])
           for frame in json.loads(probe.stdout)["frames"]]
    decoded = subprocess.run([
        "ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-map", "0:v:0",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ], check=True, capture_output=True, timeout=30).stdout
    frame_size = 32 * 32 * 3
    if len(decoded) % frame_size:
        raise AssertionError("decoded byte stream is not a whole number of frames")
    hashes = [hashlib.sha256(decoded[offset:offset + frame_size]).hexdigest()
              for offset in range(0, len(decoded), frame_size)]
    return pts, hashes


def _voice_bytes(sample):
    import io
    import wave

    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(struct.pack("<h", sample) * 16000)
    return output.getvalue()


def _voice_record(data, blob_ref):
    return {
        "track_id": "voice", "blob_ref": blob_ref, "kind": "voice",
        "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data),
        "extension": ".wav", "source_format": "wav", "media_type": "audio/wav",
        "codec": "pcm_s16le", "duration_ms": 1000, "sample_rate": 16000, "channels": 1,
        "provenance": "test fixture", "rights_status": "unverified",
    }


def _composition(blob_ref, font_size=56):
    return {
        "html": "<!doctype html><body><main>scene</main></body>",
        "width": 1080, "height": 1920, "fps": 30, "duration_frames": 30,
        "audio_enabled": True, "subtitles_enabled": True, "subtitle_mode": "ass",
        "ass_style": {**MOBILE_ASS_STYLE, "font_size": font_size},
        "audio_tracks": [{
            "track_id": "voice", "blob_ref": blob_ref, "kind": "voice", "offset_ms": 0,
            "trim_start_ms": 0, "trim_end_ms": 1000, "gain_db": 0.0,
            "fade_in_ms": 0, "fade_out_ms": 0,
        }],
        "assets": [],
    }


def _custom_voice_job(store):
    job_id = store.create("cache integration", mode="autonomous")["job_id"]
    data = _voice_bytes(0)
    record = _voice_record(data, "ABCDEFGHIJKLMNOP")
    audio_root = store._job_dir(job_id) / "audio_blobs"
    audio_root.mkdir()
    (audio_root / f"{record['blob_ref']}.wav").write_bytes(data)
    prepared = store.revise(job_id, 1, {
        "audio_library": [record], "naskah": {"text": "Cakra."},
    })
    store.set_stage(job_id, prepared["rev"], "naskah_approved")
    written = store.write_composition(job_id, prepared["rev"], _composition(record["blob_ref"]))
    return job_id, written["rev"], record, data


def _run_for(job_id, rev, ordinal):
    return {
        "job_id": job_id,
        "rev": rev,
        "target": "preview",
        "snapshot_path": f"jobs/{job_id}/revisions/{rev}/input.json",
        "run_id": f"recovery-run-{ordinal:02d}",
    }


class RenderCacheKeyTests(unittest.TestCase):
    def test_keys_bind_effective_content_for_each_stage(self):
        alignment = {
            "voice_sha256": "a" * 64,
            "transcript_sha256": "b" * 64,
            "provider_model_versions": {"whisperx": "m1", "faster-whisper": "m2"},
            "settings": {"language": "id", "device": "cpu"},
        }
        self.assertNotEqual(stage_key("alignment", alignment), stage_key(
            "alignment", {**alignment, "voice_sha256": "c" * 64}))
        self.assertNotEqual(stage_key("alignment", alignment), stage_key(
            "alignment", {**alignment, "transcript_sha256": "d" * 64}))
        self.assertNotEqual(stage_key("alignment", alignment), stage_key(
            "alignment", {**alignment, "provider_model_versions": {"whisperx": "m9"}}))

        sidecar = {"timeline_sha256": "e" * 64, "style": {"font_size": 56}}
        self.assertNotEqual(stage_key("sidecar", sidecar), stage_key(
            "sidecar", {**sidecar, "style": {"font_size": 60}}))
        self.assertNotEqual(stage_key("sidecar", sidecar), stage_key(
            "sidecar", {**sidecar, "timeline_sha256": "f" * 64}))

        self.assertNotEqual(stage_key("capture", CAPTURE_INPUTS), stage_key(
            "capture", {**CAPTURE_INPUTS, "source_sha256": "3" * 64}))
        self.assertNotEqual(stage_key("capture", CAPTURE_INPUTS), stage_key(
            "capture", {**CAPTURE_INPUTS, "frame_range": [0, 29]}))
        mux = {"video_sha256": "4" * 64, "audio_sha256": ["5" * 64],
               "subtitle_sha256": "6" * 64, "encoder_settings": {"crf": 18}}
        self.assertNotEqual(stage_key("mux", mux), stage_key(
            "mux", {**mux, "audio_sha256": ["7" * 64]}))
        self.assertNotEqual(stage_key("mux", mux), stage_key(
            "mux", {**mux, "encoder_settings": {"crf": 20}}))

    def test_source_path_and_filename_are_not_cache_identity(self):
        first = stage_key("capture", CAPTURE_INPUTS)
        second = stage_key("capture", dict(reversed(list(CAPTURE_INPUTS.items()))))
        self.assertEqual(first, second)
        changed = {**CAPTURE_INPUTS, "source_sha256": hashlib.sha256(b"source B").hexdigest()}
        self.assertNotEqual(first, stage_key("capture", changed))


class RenderCacheUnitTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.key = stage_key("capture", CAPTURE_INPUTS)

    def test_custom_capture_runtime_uses_recorder_resolved_playwright_version(self):
        scripts = self.root / "scripts"
        package = scripts / "node_modules" / "playwright-core" / "package.json"
        package.parent.mkdir(parents=True)
        package.write_text(json.dumps({"version": "1.65.1"}), encoding="utf-8")
        recorder = scripts / "record_custom.mjs"
        recorder.write_text("import { chromium } from 'playwright-core';", encoding="utf-8")
        with mock.patch.object(worker, "CODE_ROOT", self.root), \
                mock.patch.object(worker.subprocess, "run", return_value=mock.Mock(
                    returncode=0, stdout="Chrome 156.0.8078.47", stderr="")):
            runtime = worker._capture_runtime(recorder, legacy=False)
            self.assertIsNotNone(runtime)
            self.assertEqual(runtime["playwright_version"], "1.65.1")
            package.unlink()
            self.assertIsNone(worker._capture_runtime(recorder, legacy=False))

    def test_live_custom_capture_runtime_and_missing_package_fallback(self):
        recorder = worker.CODE_ROOT / "scripts" / "record_custom.mjs"
        package = worker.CODE_ROOT / "scripts" / "node_modules" / "playwright-core" / "package.json"
        self.assertTrue(package.is_file())
        expected = json.loads(package.read_text(encoding="utf-8"))["version"]
        runtime = worker._capture_runtime(recorder, legacy=False)
        self.assertIsNotNone(runtime)
        self.assertEqual(runtime["playwright_version"], expected)
        with mock.patch.object(worker, "CODE_ROOT", self.root):
            self.assertIsNone(worker._capture_runtime(recorder, legacy=False))

    def test_web_sidecar_miss_uses_writer_and_rejects_corrupt_json(self):
        workspace = self.root / "workspace"
        workspace.mkdir()
        directory = self.root / "run"
        directory.mkdir()
        timeline = {"words": [{"id": "w1", "text": "Cakra", "start_ms": 100, "end_ms": 200}]}
        voice = {"sha256": "a" * 64, "duration_ms": 1000}

        def corrupt_writer(target, _timeline, _voice):
            path = target / "subtitles.json"
            path.write_bytes(b"{broken")
            return path, b"{broken"

        with mock.patch("ump_core.subtitles.render_karaoke_subtitles", return_value=b'{"canonical":true}'), \
                mock.patch.object(worker, "_write_custom_karaoke", side_effect=corrupt_writer) as writer:
            with self.assertRaisesRegex(worker.DomainError, "invalid karaoke subtitle json"):
                worker._cached_custom_sidecar(
                    None, {"stages": {}}, directory, workspace, timeline, voice, mode="web",
                )
        writer.assert_called_once()

    def test_complete_ordered_capture_unit_validates(self):
        unit = _capture_unit(self.root / "unit", self.key)
        self.assertTrue(validate_unit(unit, self.key))

    def test_worker_reuses_verified_full_range_with_decoded_equivalence(self):
        project = self.root / "project"
        project.mkdir(mode=0o700)
        (project / "index.html").write_text("<!doctype html><title>deterministic</title>", encoding="utf-8")
        (project / "meta.json").write_text(json.dumps({
            "width": 32, "height": 32, "fps": 30, "duration_s": 1,
        }), encoding="utf-8")
        baseline_frames = self.root / "baseline-frames"
        baseline_frames.mkdir(mode=0o700)
        for index in range(30):
            (baseline_frames / f"f{index:05d}.png").write_bytes(
                _png((index, 255 - index, (index * 7) % 256)),
            )
        baseline_video = self.root / "uninterrupted.mp4"
        _encode_png_sequence(baseline_frames, baseline_video)

        store = JobStore(self.root / "store")
        job_id = store.create("legacy cache fixture")["job_id"]
        captures = []

        def command(argv, cwd=None):
            if "record_frames.mjs" in " ".join(map(str, argv)):
                captures.append("capture")
                raw = Path(argv[-1])
                raw.mkdir(mode=0o700)
                for index in range(30):
                    (raw / f"f{index:05d}.png").write_bytes(
                        _png((index, 255 - index, (index * 7) % 256)),
                    )
                return
            subprocess.run(argv, cwd=cwd, check=True, capture_output=True, timeout=30)

        first_dir = self.root / "run-first"
        first_dir.mkdir(mode=0o700)
        worker._record_frames(first_dir, project, command, store=store, job_id=job_id)
        first_report = json.loads((first_dir / "render-cache.json").read_text())
        self.assertEqual(first_report["stages"]["capture"]["decision"], "miss")
        self.assertEqual(first_report["partial_resume"]["decision"], "disabled")
        self.assertEqual(
            first_report["partial_resume"]["reason"],
            "partial capture-unit reuse is not implemented; only a verified full-range unit may be reused",
        )

        second_dir = self.root / "run-cache-hit"
        second_dir.mkdir(mode=0o700)
        worker._record_frames(second_dir, project, command, store=store, job_id=job_id)
        second_report = json.loads((second_dir / "render-cache.json").read_text())
        self.assertEqual(second_report["stages"]["capture"]["decision"], "hit")
        self.assertEqual(captures, ["capture"])

        baseline_pts, baseline_hashes = _video_signature(baseline_video)
        first_pts, first_hashes = _video_signature(first_dir / "frames.mp4")
        cached_pts, cached_hashes = _video_signature(second_dir / "frames.mp4")
        for pts in (baseline_pts, first_pts, cached_pts):
            self.assertEqual(len(pts), 30)
            for index, value in enumerate(pts):
                self.assertAlmostEqual(value, index / 30, places=6)
        for hashes in (first_hashes, cached_hashes):
            self.assertEqual(len(hashes), 30)
            self.assertEqual(hashes, baseline_hashes)
        self.assertEqual(first_pts, cached_pts)
        self.assertEqual(first_hashes[14:16], cached_hashes[14:16])

    def test_stateless_partial_capture_units_reencode_to_uninterrupted_full_video(self):
        full_frames = self.root / "full-capture"
        resumed_frames = self.root / "resumed-capture"
        baseline_video = self.root / "full-capture.mp4"
        resumed_video = self.root / "resumed-capture.mp4"
        full_frames.mkdir(mode=0o700)
        resumed_frames.mkdir(mode=0o700)
        for index in range(30):
            (full_frames / f"f{index:05d}.png").write_bytes(_png((index % 256, 0, 0)))

        first_unit = _capture_unit(self.root / "unit-0-15", self.key, total=30, start=0, end=15)
        self.assertFalse(validate_unit(first_unit, self.key))
        self.assertFalse(resumed_video.exists())

        second_unit = _capture_unit(self.root / "unit-15-30", self.key, total=30, start=15, end=30)
        self.assertFalse(validate_unit(second_unit, self.key))
        for index in range(30):
            unit = first_unit if index < 15 else second_unit
            name = f"f{index:05d}.png"
            resumed_path = resumed_frames / name
            shutil.copyfile(unit / name, resumed_path)
            self.assertEqual(resumed_path.read_bytes(), (full_frames / name).read_bytes())

        _encode_png_sequence(full_frames, baseline_video)
        _encode_png_sequence(resumed_frames, resumed_video)
        full_pts, full_hashes = _video_signature(baseline_video)
        resumed_pts, resumed_hashes = _video_signature(resumed_video)
        self.assertEqual(len(full_pts), 30)
        self.assertEqual(len(full_hashes), 30)
        self.assertEqual(resumed_pts, full_pts)
        self.assertEqual(resumed_hashes, full_hashes)
        self.assertEqual(resumed_pts[14:16], full_pts[14:16])
        self.assertEqual(resumed_hashes[14:16], full_hashes[14:16])

    def test_missing_duplicate_extra_swapped_and_corrupt_frames_fail_closed(self):
        cases = ("missing", "duplicate", "extra", "swapped", "corrupt")
        for case in cases:
            with self.subTest(case=case):
                unit = _capture_unit(self.root / case, self.key)
                manifest_path = unit / "manifest.json"
                manifest = json.loads(manifest_path.read_text())
                if case == "missing":
                    (unit / "f00012.png").unlink()
                elif case == "duplicate":
                    manifest["frames"].append(dict(manifest["frames"][0]))
                    manifest_path.write_text(json.dumps(manifest))
                elif case == "extra":
                    (unit / "f00030.png").write_bytes(_png((1, 2, 3)))
                elif case == "swapped":
                    manifest["frames"][14]["name"], manifest["frames"][15]["name"] = (
                        manifest["frames"][15]["name"], manifest["frames"][14]["name"])
                    manifest_path.write_text(json.dumps(manifest))
                else:
                    with (unit / "f00014.png").open("ab") as stream:
                        stream.write(b"corrupt")
                self.assertFalse(validate_unit(unit, self.key))

    def test_wrong_key_partial_range_and_old_or_partial_manifest_are_misses(self):
        wrong_key_unit = _capture_unit(self.root / "wrong-key", self.key)
        self.assertFalse(validate_unit(wrong_key_unit, "0" * 64))

        partial = _capture_unit(self.root / "partial-range", self.key, end=15)
        self.assertFalse(validate_unit(partial, self.key))

        old = _capture_unit(self.root / "old-manifest", self.key)
        manifest_path = old / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["schema_version"] = 0
        manifest_path.write_text(json.dumps(manifest))
        self.assertFalse(validate_unit(old, self.key))

        partial_manifest = _capture_unit(self.root / "partial-manifest", self.key)
        (partial_manifest / "manifest.json").unlink()
        (partial_manifest / "manifest.json.tmp").write_text("{}")
        self.assertFalse(validate_unit(partial_manifest, self.key))

    def test_malformed_and_symlinked_units_are_misses(self):
        malformed = self.root / "malformed"
        malformed.mkdir()
        (malformed / "manifest.json").write_text("{")
        self.assertFalse(validate_unit(malformed, self.key))

        real = _capture_unit(self.root / "real", self.key)
        link = self.root / "linked"
        link.symlink_to(real, target_is_directory=True)
        self.assertFalse(validate_unit(link, self.key))

    def test_worker_alignment_cache_reuses_font_only_and_misses_changed_voice_bytes(self):
        store = JobStore(self.root / "store")
        job_id, rev, record, _ = _custom_voice_job(store)
        workspace_root = self.root / "workspaces"
        workspace_root.mkdir()
        calls = []
        workspace_number = [0]

        def create_workspace(_prefix):
            workspace_number[0] += 1
            path = workspace_root / str(workspace_number[0])
            path.mkdir(mode=0o700)
            return path

        def align(_workspace, _audio, _transcript, *, audio_sha256, duration_ms, approved_text, deadline):
            calls.append(audio_sha256)
            return audio.normalize_provider_timeline(
                approved_text,
                [{"text": "Cakra.", "start_ms": 100, "end_ms": 700}],
                audio_sha256=audio_sha256, duration_ms=duration_ms, provider="whisperx",
                model_id="cahya/wav2vec2-large-xlsr-indonesian",
                model_revision="ce6da00000a403c7cf3857197f4132439667e8f0",
                timing_status="aligned",
            )

        def invoke(current_rev, ordinal):
            run_dir = self.root / f"invocation-{ordinal}"
            run_dir.mkdir()
            with mock.patch.object(worker.sandbox, "create_workspace", side_effect=create_workspace), \
                    mock.patch.object(worker.sandbox, "cleanup_workspace", side_effect=shutil.rmtree), \
                    mock.patch.object(worker, "_align_voice_timeline", side_effect=align), \
                    mock.patch.object(audio, "resolve_speech_anchors", side_effect=RuntimeError("stop after alignment")):
                with self.assertRaisesRegex(RuntimeError, "stop after alignment"):
                    worker._render_custom(store, _run_for(job_id, current_rev, ordinal),
                                          run_dir, 10**12)
            return json.loads((run_dir / "render-cache.json").read_text())

        first_report = invoke(rev, 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(first_report["stages"]["alignment"]["decision"], "miss")

        changed_style = _composition(record["blob_ref"], font_size=60)
        rev = store.write_composition(job_id, rev, changed_style)["rev"]
        second_report = invoke(rev, 2)
        self.assertEqual(len(calls), 1, "font-only revision called ASR despite reusable alignment")
        self.assertEqual(second_report["stages"]["alignment"]["decision"], "hit")
        changed_voice = _voice_bytes(1)
        new_record = _voice_record(changed_voice, "QRSTUVWXYZabcdef")
        (store._job_dir(job_id) / "audio_blobs" / f"{new_record['blob_ref']}.wav").write_bytes(changed_voice)
        revised = store.revise(job_id, rev, {"audio_library": [new_record]})
        changed_composition = _composition(new_record["blob_ref"], font_size=60)
        rev = store.write_composition(job_id, revised["rev"], changed_composition)["rev"]
        third_report = invoke(rev, 3)
        self.assertEqual(len(calls), 2, "voice-byte change did not invoke alignment again")
        self.assertEqual(third_report["stages"]["alignment"]["decision"], "miss")
        self.assertNotEqual(calls[0], calls[1])

    def test_interrupted_capture_and_encode_retry_same_snapshot_without_false_artifacts(self):
        store = JobStore(self.root / "retries")
        job_id, rev, _record, _data = _custom_voice_job(store)
        first = store.claim(job_id, rev, "preview")
        first_snapshot = first["snapshot_path"]
        snapshot_bytes = (store.root / first_snapshot).read_bytes()
        capture_attempts = []

        def renderer(mode):
            def render(current_store, current_run, directory, _deadline, budget_check=None):
                project = directory / "legacy-fixture"
                project.mkdir(mode=0o700)
                (project / "index.html").write_text("<!doctype html><title>same snapshot</title>")
                (project / "meta.json").write_text(json.dumps({
                    "width": 32, "height": 32, "fps": 30, "duration_s": 1,
                }))

                def command(argv, cwd=None):
                    if "record_frames.mjs" in " ".join(map(str, argv)):
                        raw = Path(argv[-1])
                        raw.mkdir(mode=0o700)
                        count = 15 if mode == "capture_interrupt" else 30
                        capture_attempts.append(list(range(count)))
                        for index in range(count):
                            (raw / f"f{index:05d}.png").write_bytes(_png((index, 255 - index, index * 7)))
                        if mode == "capture_interrupt":
                            raise subprocess.TimeoutExpired(argv, 1)
                        return
                    if mode == "encode_interrupt":
                        Path(argv[-1]).write_bytes(b"incomplete mp4")
                        return
                    subprocess.run(argv, cwd=cwd, check=True, capture_output=True, timeout=30)

                worker._record_frames(
                    directory, project, command, store=current_store, job_id=current_run["job_id"],
                    run_id=current_run["run_id"],
                )
                return directory / "frames.mp4", {}
            return render

        common_patches = (
            mock.patch.object(worker.sandbox, "render_lease", return_value=mock.MagicMock(
                __enter__=lambda _s: None, __exit__=lambda *_a: False,
            )),
            mock.patch.object(worker.resources, "probe_resources", return_value={
                "root_free_bytes": 10**12, "data_root_free_bytes": 10**12,
                "data_root_used_bytes": 0, "temp_free_bytes": 10**12,
                "projected_temp_bytes": 0, "memory_headroom_bytes": 10**12,
                "memory_headroom_complete": True,
            }),
            mock.patch.object(worker.resources, "worker_peak_memory_bytes", return_value=1),
            mock.patch.object(worker, "_run_budget_check", return_value=None),
        )

        with common_patches[0], common_patches[1], common_patches[2], common_patches[3], \
                mock.patch.object(worker, "_render_custom", side_effect=renderer("capture_interrupt")):
            failed_capture = worker.execute_run(store, first["run_id"])
        self.assertEqual(failed_capture["status"], "failed")
        failed_capture_dir = self.root / "retries" / "runs" / first["run_id"]
        self.assertFalse((failed_capture_dir / "preview.mp4").exists())
        self.assertFalse((failed_capture_dir / "frames.mp4").exists())
        first_report = json.loads((failed_capture_dir / "render-cache.json").read_text())
        self.assertEqual(first_report["run_id"], first["run_id"])
        self.assertEqual(first_report["stages"]["capture"]["decision"], "miss")
        self.assertEqual(first_report["partial_resume"]["decision"], "disabled")
        self.assertFalse(list(failed_capture_dir.glob(".framesraw-*.tmp-*")))
        self.assertEqual((store.root / first_snapshot).read_bytes(), snapshot_bytes)
        with store._db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM artifacts WHERE job_id=?", (job_id,)).fetchone()[0], 0)
        self.assertEqual(capture_attempts, [list(range(15))])

        second = store.claim(job_id, rev, "preview")
        self.assertEqual(second["snapshot_path"], first_snapshot)
        second_dir = self.root / "retries" / "runs" / second["run_id"]
        with common_patches[0], common_patches[1], common_patches[2], common_patches[3], \
                mock.patch.object(worker, "_render_custom", side_effect=renderer("encode_interrupt")):
            failed_encode = worker.execute_run(store, second["run_id"])
        self.assertEqual(failed_encode["status"], "failed")
        self.assertFalse((second_dir / "preview.mp4").exists())
        self.assertFalse((second_dir / "frames.mp4").exists())
        self.assertFalse((second_dir / "frames.partial.mp4").exists())
        second_report = json.loads((second_dir / "render-cache.json").read_text())
        self.assertEqual(second_report["stages"]["capture"]["decision"], "miss")
        self.assertIn("atomically published", second_report["stages"]["capture"]["reason"])
        self.assertEqual(capture_attempts[-1], list(range(30)))
        with store._db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM artifacts WHERE job_id=?", (job_id,)).fetchone()[0], 0)

        third = store.claim(job_id, rev, "preview")
        self.assertEqual(third["snapshot_path"], first_snapshot)
        third_dir = self.root / "retries" / "runs" / third["run_id"]
        with common_patches[0], common_patches[1], common_patches[2], common_patches[3], \
                mock.patch.object(worker, "_render_custom", side_effect=renderer("complete")):
            finished = worker.execute_run(store, third["run_id"])
        self.assertEqual(finished["status"], "finished")
        self.assertEqual(store.get_run(first["run_id"])["status"], "failed")
        self.assertEqual(store.get_run(second["run_id"])["status"], "failed")
        self.assertEqual(store.get_run(third["run_id"])["status"], "finished")
        self.assertEqual(capture_attempts, [list(range(15)), list(range(30))])
        self.assertEqual(json.loads((third_dir / "render-cache.json").read_text())[
            "stages"]["capture"]["decision"], "hit")
        self.assertTrue((third_dir / "preview.mp4").is_file())
        self.assertEqual(len(store.get(job_id)["artifacts"]), 1)


if __name__ == "__main__":
    unittest.main()
