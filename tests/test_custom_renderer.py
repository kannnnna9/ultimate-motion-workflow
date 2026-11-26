import hashlib
import io
import json
import math
import os
import struct
import subprocess
import sys
import tempfile
import unittest
import uuid
import wave
import zipfile
from pathlib import Path
from unittest.mock import patch

from ump_core import sandbox, worker
from ump_core.assets import AssetStore
from ump_core.jobs import JobStore
from ump_core.subtitles import render_karaoke_subtitles
from ump_core.tools import _artifact


ENABLED = os.environ.get("UMP_BROWSER") == "1" and os.environ.get("UMP_SANDBOX_TESTS") == "1"
ROOT = Path(__file__).resolve().parents[1]


def _scene(*, duration_frames=30, fail_after=None, external_url=None):
    bad_seek = (
        f"if (t >= {fail_after}) throw new Error('intentional seek failure');"
        if fail_after is not None else ""
    )
    external = (
        f"fetch('{external_url}');"
        if external_url is not None else ""
    )
    return f"""<!doctype html>
<meta charset="utf-8">
<style>html,body{{margin:0;width:100%;height:100%;overflow:hidden;background:#f8f5ef}}</style>
<canvas id="scene" width="1080" height="1920"></canvas>
<script>
const canvas=document.querySelector('#scene'), ctx=canvas.getContext('2d');
function draw(t) {{
  ctx.fillStyle='#f8f5ef'; ctx.fillRect(0,0,1080,1920);
  ctx.fillStyle='#092f58';
  ctx.fillRect(120 + 650 * t, 850, 120, 120);
}}
window.UMP={{
  width:1080,height:1920,fps:30,durationFrames:{duration_frames},
  async ready() {{ {external} await document.fonts.ready; }},
  seek(t) {{ {bad_seek} draw(t); }}
}};
draw(0);
</script>"""


def _speech_scene(*, duration_frames=30, with_setter=True):
    setter = """
  setTimeline(data) {
    if (!this.readyCalled || this.timelineCalls) throw new Error('setTimeline order/count mismatch');
    this.timelineCalls += 1;
    this.timeline = data;
  },""" if with_setter else ""
    return f"""<!doctype html><meta charset='utf-8'>
<style>html,body{{margin:0;width:100%;height:100%;overflow:hidden;background:#f8f5ef}}</style>
<canvas id='scene' width='1080' height='1920'></canvas><script>
const canvas=document.querySelector('#scene'),ctx=canvas.getContext('2d');
function draw(active){{ctx.fillStyle='#f8f5ef';ctx.fillRect(0,0,1080,1920);ctx.fillStyle=active?'#00a64f':'#092f58';ctx.fillRect(active?750:120,850,120,120);}}
window.UMP={{
 width:1080,height:1920,fps:30,durationFrames:{duration_frames},readyCalled:false,timelineCalls:0,timeline:null,
 async ready(){{this.readyCalled=true;await document.fonts.ready;}},
 {setter}
 seek(t){{if({str(with_setter).lower()}&&!this.timelineCalls)throw new Error('timeline missing before seek');const frame=Math.round(t*30);const active=!!this.timeline?.anchors.some(a=>a.action_id==='train-arrives'&&frame>=a.frame);draw(active);}}
}};
</script>"""


def _scene_with_asset():
    return """<!doctype html>
<meta charset="utf-8">
<style>html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#f8f5ef}#tile{display:none}</style>
<img id="tile" src="assets/leaf-v1.svg">
<canvas id="scene" width="1080" height="1920"></canvas>
<script>
const image=document.querySelector('#tile'), canvas=document.querySelector('#scene'), ctx=canvas.getContext('2d');
window.UMP={width:1080,height:1920,fps:30,durationFrames:30,
 async ready(){await image.decode();},
 seek(t){ctx.fillStyle='#f8f5ef';ctx.fillRect(0,0,1080,1920);ctx.drawImage(image,220+120*t,700,640,480);}};
window.UMP.seek(0);
</script>"""


def _project(duration_frames=30):
    return {
        "width": 1080,
        "height": 1920,
        "fps": 30,
        "duration_frames": duration_frames,
        "audio_enabled": False,
        "subtitles_enabled": False,
        "subtitle_mode": "none",
        "subtitle_effect": None,
        "draws_speech_text": False,
        "speech_anchors": [],
    }


def _wav_bytes(duration_ms=1000, sample_rate=16000):
    frames = int(sample_rate * duration_ms / 1000)
    raw = b"".join(
        struct.pack("<h", int(5000 * math.sin(2 * math.pi * 440 * index / sample_rate)))
        for index in range(frames)
    )
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(sample_rate)
        stream.writeframes(raw)
    return output.getvalue()


def _store_audio_blob(store, job_id, track_id, kind, data, duration_ms=1000, sample_rate=16000):
    blob_ref = ("V" if kind == "voice" else "M") * 16
    audio_dir = store._job_dir(job_id) / "audio_blobs"
    audio_dir.mkdir(mode=0o700, exist_ok=True)
    blob_path = audio_dir / f"{blob_ref}.wav"
    blob_path.write_bytes(data)
    os.chmod(blob_path, 0o600)
    record = {
        "track_id": track_id,
        "blob_ref": blob_ref,
        "kind": kind,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
        "extension": ".wav",
        "source_format": "wav",
        "media_type": "audio/wav",
        "codec": "pcm_s16le",
        "duration_ms": duration_ms,
        "sample_rate": sample_rate,
        "channels": 1,
        "provenance": "test-imported audio",
        "rights_status": "unverified",
    }
    current = store.get_revision_payload(job_id, store.get(job_id)["rev"])
    revised = store.revise(job_id, store.get(job_id)["rev"], {
        "audio_library": [*current.get("audio_library", []), record],
    })
    return record, revised["rev"]


def _speech_naskah():
    return {"beats": [{"id": "b1", "narration": "Cakra Selaras Wahana."}]}


def _new_approved_naskah_job(store, brief="speech render", naskah=None):
    job = store.create(brief, mode="autonomous")
    revised = store.revise(job["job_id"], job["rev"], {"naskah": naskah or _speech_naskah()})
    store.set_stage(job["job_id"], revised["rev"], "naskah_approved")
    return job["job_id"], revised["rev"]


def _speech_timeline(audio_sha256, duration_ms=1000):
    words = [
        {"id": "w00001", "text": "Cakra", "start_ms": 200, "end_ms": 300, "speaker_id": None, "timing_status": "aligned"},
        {"id": "w00002", "text": "Selaras", "start_ms": 350, "end_ms": 450, "speaker_id": None, "timing_status": "aligned"},
        {"id": "w00003", "text": "Wahana.", "start_ms": 500, "end_ms": 800, "speaker_id": None, "timing_status": "aligned"},
    ]
    return {
        "schema_version": 1, "language": "id", "audio_sha256": audio_sha256,
        "provider": "whisperx", "model_id": "cahya/wav2vec2-large-xlsr-indonesian",
        "model_revision": "ce6da00000a403c7cf3857197f4132439667e8f0",
        "status": "aligned", "duration_ms": duration_ms,
        "words": words, "approved_transcript": "Cakra Selaras Wahana.",
        "recognized_text": "Cakra Selaras Wahana.",
    }


def _karaoke_timeline(audio_sha256, duration_ms, words):
    recognized = " ".join(word["text"] for word in words)
    timeline = _speech_timeline(audio_sha256, duration_ms)
    timeline.update({
        "duration_ms": duration_ms,
        "words": words,
        "approved_transcript": recognized,
        "recognized_text": recognized,
    })
    return timeline


def _karaoke_project(audio_sha256, duration_ms, duration_frames):
    return {
        **_project(duration_frames),
        "audio_enabled": True,
        "subtitles_enabled": True,
        "subtitle_mode": "web",
        "subtitle_effect": "karaoke",
        "audio_tracks": [{
            "track_id": "voice", "kind": "voice", "sha256": audio_sha256,
            "duration_ms": duration_ms,
        }],
    }


def _voice_composition(html, record, *, mode="ass", draws_speech_text=False, anchors=None):
    return {
        "html": html,
        **_project(),
        "audio_enabled": True,
        "audio_tracks": [{
            "track_id": record["track_id"], "blob_ref": record["blob_ref"], "kind": record["kind"],
            "offset_ms": 0, "trim_start_ms": 0, "trim_end_ms": record["duration_ms"],
            "gain_db": 0.0, "fade_in_ms": 0, "fade_out_ms": 0,
        }],
        "subtitle_mode": mode,
        "subtitle_effect": "karaoke" if mode == "web" else None,
        "subtitles_enabled": mode != "none",
        "draws_speech_text": draws_speech_text,
        "speech_anchors": anchors or [],
    }


def _frame_pixel(video, frame_index, x, y):
    selected = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(video),
         "-vf", f"select=eq(n\\,{frame_index})", "-vsync", "0", "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True, capture_output=True, timeout=30,
    ).stdout
    offset = (y * 1080 + x) * 3
    return tuple(selected[offset:offset + 3])


def _png_rgb_bytes(png):
    return subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(png),
         "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
        check=True, capture_output=True, timeout=30,
    ).stdout


def _frame_rgb_bytes(video, frame_index):
    return subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(video),
         "-vf", f"select=eq(n\\,{frame_index})", "-vsync", "0", "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
        check=True, capture_output=True, timeout=30,
    ).stdout


def _orange_bounds(rgb):
    points = []
    for offset in range(0, len(rgb), 3):
        red, green, blue = rgb[offset:offset + 3]
        if red > 180 and 50 < green < 180 and blue < 145 and red > green + 50:
            pixel = offset // 3
            points.append((pixel % 1080, pixel // 1080))
    if not points:
        return None
    return (
        min(point[0] for point in points), max(point[0] for point in points),
        min(point[1] for point in points), max(point[1] for point in points),
    )


@unittest.skipUnless(ENABLED, "set UMP_BROWSER=1 UMP_SANDBOX_TESTS=1 for browser renderer integration tests")
class CustomRendererTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.store = JobStore(self.root)

    def _new_run(self, html, *, duration_frames=30):
        job = self.store.create("custom scene", mode="autonomous")
        composition = {
            "html": html,
            **_project(duration_frames),
        }
        created = self.store.write_composition(job["job_id"], job["rev"], composition)
        run = self.store.claim(job["job_id"], created["rev"], "preview")
        return job["job_id"], run

    def _probe_seek_order(self, html):
        workspace = sandbox.create_workspace("s" + uuid.uuid4().hex[:8])
        self.addCleanup(lambda: sandbox.cleanup_workspace(workspace) if workspace.exists() else None)
        source = workspace / "composition" / "index.html"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(html, encoding="utf-8")
        project = workspace / "project.json"
        project.write_text(json.dumps(_project()), encoding="utf-8")
        output = workspace / "probes"
        command = [
            "node", str(ROOT / "scripts" / "record_custom.mjs"), "--probe-seeks",
            str(source), str(project), str(output), "0.5,0,0.9,0.5",
        ]
        process, unit = sandbox.start_isolated(
            command, workspace=workspace, private_pid_namespace=True, timeout_s=60,
        )
        status = sandbox.wait_isolated(process, unit, timeout_s=30)
        stdout, stderr = process.communicate()
        self.assertEqual(status, 0, stderr.decode("utf-8", "replace"))
        self.assertEqual(json.loads(stdout)["frames"], 4)
        pngs = sorted(output.glob("seek-*.png"))
        self.assertEqual(len(pngs), 4)
        decoded = []
        for png in pngs:
            result = subprocess.run(
                ["ffmpeg", "-v", "error", "-i", str(png), "-f", "rawvideo", "-pix_fmt", "rgba", "pipe:1"],
                check=True, capture_output=True,
            )
            decoded.append(result.stdout)
        self.assertEqual(decoded[0], decoded[3], "rewinding to 0.5s must restore identical pixels")
        self.assertNotEqual(decoded[0], decoded[1], "the scene must visibly change between 0.5s and 0s")

    def _probe_karaoke(self, timeline, subtitle_bytes, frame_numbers, *, subtitle_location="inside", html=None):
        workspace = sandbox.create_workspace("k" + uuid.uuid4().hex[:8])
        self.addCleanup(lambda: sandbox.cleanup_workspace(workspace) if workspace.exists() else None)
        composition = workspace / "composition"
        composition.mkdir(mode=0o700)
        source = composition / "index.html"
        duration_frames = max(max(frame_numbers) + 1, math.ceil(timeline["duration_ms"] * 30 / 1000))
        source.write_text(html or _speech_scene(duration_frames=duration_frames), encoding="utf-8")
        project = workspace / "project.json"
        project.write_text(json.dumps(_karaoke_project(
            timeline["audio_sha256"], timeline["duration_ms"], duration_frames,
        )), encoding="utf-8")
        timing = workspace / "timing.json"
        timing.write_text(json.dumps({
            "audio_sha256": timeline["audio_sha256"],
            "words": timeline["words"],
            "anchors": [],
        }), encoding="utf-8")
        subtitle_arg = None
        if subtitle_location == "inside":
            subtitle = workspace / "subtitles.json"
            if subtitle_bytes is not None:
                subtitle.write_bytes(subtitle_bytes)
                subtitle_arg = str(subtitle)
        elif subtitle_location == "outside":
            subtitle = self.root / "outside-subtitles.json"
            subtitle.write_bytes(subtitle_bytes or b"")
            subtitle_arg = str(subtitle)
        output = workspace / "probes"
        times = ",".join(str(frame / 30) for frame in frame_numbers)
        command = [
            "node", str(ROOT / "scripts" / "record_custom.mjs"), "--probe-seeks",
            str(source), str(project), str(output), times, str(timing),
        ]
        if subtitle_arg is not None:
            command.append(subtitle_arg)
        process, unit = sandbox.start_isolated(
            command, workspace=workspace, private_pid_namespace=True, timeout_s=60,
        )
        status = sandbox.wait_isolated(process, unit, timeout_s=30)
        stdout, stderr = process.communicate()
        return status, stdout.decode("utf-8", "replace"), stderr.decode("utf-8", "replace"), output

    def test_web_karaoke_tracks_word_color_and_is_deterministic_under_reverse_seeks(self):
        audio_sha256 = "a" * 64
        words = [
            {"id": "w00001", "text": "Cakra", "start_ms": 100, "end_ms": 200, "speaker_id": None, "timing_status": "aligned"},
            {"id": "w00002", "text": "Selaras", "start_ms": 250, "end_ms": 350, "speaker_id": None, "timing_status": "aligned"},
            {"id": "w00003", "text": "Wahana", "start_ms": 400, "end_ms": 500, "speaker_id": None, "timing_status": "aligned"},
            {"id": "w00004", "text": "Pagi", "start_ms": 600, "end_ms": 800, "speaker_id": None, "timing_status": "aligned"},
        ]
        timeline = _karaoke_timeline(audio_sha256, 1000, words)
        subtitle_bytes = render_karaoke_subtitles(
            timeline, audio_sha256=audio_sha256, track_duration_ms=1000,
        )
        frames = [2, 3, 4, 6, 8, 10, 11, 12, 13, 15, 18, 23, 24, 8, 3]
        status, stdout, stderr, output = self._probe_karaoke(timeline, subtitle_bytes, frames)
        self.assertEqual(status, 0, stderr)
        report = json.loads(stdout)
        self.assertEqual(report["frames"], len(frames))
        self.assertEqual(report["karaoke"], {"overlay_count": 1, "closed_shadow_root": True})
        pngs = sorted(output.glob("seek-*.png"))
        self.assertEqual(len(pngs), len(frames))
        rgb = [_png_rgb_bytes(png) for png in pngs]
        bounds = [_orange_bounds(frame) for frame in rgb]
        for index in (0, 3, 6, 9, 12):
            self.assertIsNone(bounds[index], f"frame {frames[index]} should have no active word")
        active = [bounds[index] for index in (1, 4, 7, 10)]
        self.assertTrue(all(item is not None for item in active))
        centers = [(item[0] + item[1]) / 2 for item in active if item is not None]
        self.assertEqual(len(centers), 4)
        self.assertTrue(all(left < right for left, right in zip(centers, centers[1:])),
                        "highlight must move strictly forward through the phrase")
        self.assertEqual(rgb[4], rgb[13], "seeking back to word two must restore identical pixels")
        self.assertEqual(rgb[1], rgb[14], "seeking back to word one must restore identical pixels")

    def test_web_karaoke_does_not_open_external_network_routes(self):
        audio_sha256 = "d" * 64
        words = [{"id": "w00001", "text": "Cakra", "start_ms": 100, "end_ms": 500,
                  "speaker_id": None, "timing_status": "aligned"}]
        timeline = _karaoke_timeline(audio_sha256, 1000, words)
        subtitle_bytes = render_karaoke_subtitles(timeline, audio_sha256=audio_sha256, track_duration_ms=1000)
        html = _speech_scene().replace(
            "async ready(){this.readyCalled=true;",
            "async ready(){fetch('https://example.invalid/karaoke.png');this.readyCalled=true;",
        )
        status, _stdout, stderr, output = self._probe_karaoke(
            timeline, subtitle_bytes, [3], html=html,
        )
        self.assertNotEqual(status, 0)
        self.assertIn("external request blocked", stderr.lower())
        self.assertFalse(list(output.glob("seek-*.png")))

    def test_web_karaoke_holds_previous_word_for_subframe_gap(self):
        audio_sha256 = "b" * 64
        words = [
            {"id": "w00001", "text": "Kejaksaan", "start_ms": 13700, "end_ms": 13900, "speaker_id": None, "timing_status": "aligned"},
            {"id": "w00002", "text": "Agung.", "start_ms": 14000, "end_ms": 14049, "speaker_id": None, "timing_status": "aligned"},
            {"id": "w00003", "text": "Di", "start_ms": 14069, "end_ms": 14180, "speaker_id": None, "timing_status": "aligned"},
        ]
        timeline = _karaoke_timeline(audio_sha256, 15000, words)
        subtitle_bytes = render_karaoke_subtitles(
            timeline, audio_sha256=audio_sha256, track_duration_ms=15000,
        )
        status, stdout, stderr, output = self._probe_karaoke(
            timeline, subtitle_bytes, [421, 422, 423, 422, 421],
        )
        self.assertEqual(status, 0, stderr)
        self.assertEqual(json.loads(stdout)["frames"], 5)
        pngs = sorted(output.glob("seek-*.png"))
        rgb = [_png_rgb_bytes(png) for png in pngs]
        self.assertEqual(rgb[0], rgb[1], "frame 422 must hold Agung through the 20 ms gap")
        self.assertNotEqual(rgb[1], rgb[2], "frame 423 is the first sampled frame for Di")
        self.assertEqual(rgb[1], rgb[3], "reverse seek to frame 422 must preserve the held word")
        self.assertEqual(rgb[0], rgb[4], "repeat seek to frame 421 must be pixel-identical")

    def test_web_karaoke_rejects_missing_corrupt_and_outside_subtitle_json(self):
        audio_sha256 = "c" * 64
        words = [{"id": "w00001", "text": "Cakra", "start_ms": 100, "end_ms": 500,
                  "speaker_id": None, "timing_status": "aligned"}]
        timeline = _karaoke_timeline(audio_sha256, 1000, words)
        valid = render_karaoke_subtitles(timeline, audio_sha256=audio_sha256, track_duration_ms=1000)
        cases = [
            ("missing", None, "inside", "karaoke subtitle json is required"),
            ("corrupt", b"{broken", "inside", "invalid karaoke subtitle json"),
            ("outside", valid, "outside", "private workspace"),
        ]
        for name, payload, location, expected in cases:
            with self.subTest(name=name):
                status, _stdout, stderr, output = self._probe_karaoke(
                    timeline, payload, [3], subtitle_location=location,
                )
                self.assertNotEqual(status, 0)
                self.assertIn(expected, stderr.lower())
                self.assertFalse(output.exists() or list(output.glob("seek-*.png")))

    def test_sandbox_stdin_pipe_preserves_binary_bytes(self):
        workspace = sandbox.create_workspace("p" + uuid.uuid4().hex[:8])
        self.addCleanup(lambda: sandbox.cleanup_workspace(workspace) if workspace.exists() else None)
        payload = bytes(range(256)) * 16384
        process, unit = sandbox.start_isolated(
            [sys.executable, "-c", "import hashlib,json,sys,time; time.sleep(0.2); digest=hashlib.sha256(); size=0\nwhile True:\n data=sys.stdin.buffer.read(16384)\n if not data: break\n digest.update(data); size+=len(data); time.sleep(0.002)\nprint(json.dumps({'size':size,'sha256':digest.hexdigest()}))"],
            workspace=workspace, stdin=subprocess.PIPE, timeout_s=10,
        )
        self.assertIsNotNone(process.stdin)
        process.stdin.write(payload)
        process.stdin.close()
        process.stdin = None
        status = sandbox.wait_isolated(process, unit, timeout_s=10)
        stdout, stderr = process.communicate()
        self.assertEqual(status, 0, stderr.decode("utf-8", "replace"))
        self.assertEqual(json.loads(stdout), {
            "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest(),
        })

    def test_preview_run_holds_global_render_lease(self):
        job_id, run = self._new_run(_scene())
        output = self.root / "runs" / run["run_id"] / "encoded.mp4"
        output.write_bytes(b"encoded")
        with patch.object(worker, "_render_custom", return_value=(output, {})), \
             patch.object(sandbox, "render_lease", wraps=sandbox.render_lease) as lease:
            result = worker.execute_run(self.store, run["run_id"])
        self.assertEqual(result["status"], "finished")
        lease.assert_called_once()

    def test_private_pid_namespace_hides_host_process_metadata(self):
        sentinel = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        def stop_sentinel():
            if sentinel.poll() is None:
                sentinel.terminate()
            sentinel.wait(timeout=5)
        self.addCleanup(stop_sentinel)
        workspace = sandbox.create_workspace("v" + uuid.uuid4().hex[:8])
        self.addCleanup(lambda: sandbox.cleanup_workspace(workspace) if workspace.exists() else None)
        script = (
            "import json,os,sys; pid=int(sys.argv[1]); hidden=False; "
            "fd_count=len(os.listdir('/proc/self/fd')); "
            "proc_status=open('/proc/self/status').read(); "
            "\ntry: open(f'/proc/{pid}/cmdline','rb').read()\n"
            "except OSError: hidden=True\n"
            "print(json.dumps({'pid':os.getpid(),'fds':fd_count,'status':'Pid:' in proc_status,'hidden':hidden})); "
            "sys.exit(0 if os.getpid()==1 and fd_count>0 and 'Pid:' in proc_status and hidden else 1)"
        )
        process, unit = sandbox.start_isolated(
            [sys.executable, "-c", script, str(sentinel.pid)],
            workspace=workspace, private_pid_namespace=True, timeout_s=15,
        )
        status = sandbox.wait_isolated(process, unit, timeout_s=15)
        stdout, stderr = process.communicate()
        self.assertEqual(status, 0, stderr.decode("utf-8", "replace"))
        result = json.loads(stdout)
        self.assertEqual(result["pid"], 1)
        self.assertTrue(result["hidden"])

    def test_private_pid_timeout_kills_uncooperative_child(self):
        workspace = sandbox.create_workspace("w" + uuid.uuid4().hex[:8])
        self.addCleanup(lambda: sandbox.cleanup_workspace(workspace) if workspace.exists() else None)
        process, unit = sandbox.start_isolated(
            [sys.executable, "-c", "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); print('running',flush=True); time.sleep(60)"],
            workspace=workspace, private_pid_namespace=True, timeout_s=60,
        )
        self.addCleanup(lambda: subprocess.run(
            ["sudo", "-n", "systemctl", "--system", "kill", "--kill-who=all", "--signal=KILL", unit],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
        ))
        self.assertIsNotNone(process.stdout)
        self.assertEqual(process.stdout.readline().strip(), b"running")
        status = sandbox.wait_isolated(process, unit, timeout_s=0.2)
        stdout, stderr = process.communicate(timeout=10)
        self.assertEqual(status, 124, stderr.decode("utf-8", "replace"))
        self.assertIsNotNone(process.poll())
        unit_state = subprocess.run(
            ["sudo", "-n", "systemctl", "--system", "is-active", unit],
            capture_output=True, text=True, timeout=10,
        )
        self.assertIn(unit_state.stdout.strip(), {"inactive", "failed", "unknown", "not-found"})

    def test_custom_scene_rewind_restores_identical_pixels(self):
        self._probe_seek_order(_scene())

    def test_custom_scene_renders_exactly_30_frames(self):
        html = _scene()
        job_id, run = self._new_run(html)
        with patch.object(worker, "_stage", side_effect=AssertionError("custom path entered legacy stage")) as legacy_stage, \
             patch.object(worker, "prepare_audio", side_effect=AssertionError("custom path invoked legacy ASR/captions")) as captions, \
             patch.object(worker, "_run_alignment", side_effect=AssertionError("silent path invoked ASR")) as alignment, \
             patch.object(worker, "_write_custom_subtitle", side_effect=AssertionError("silent path created subtitles")) as subtitle, \
             patch.object(worker, "_mux", side_effect=AssertionError("custom path invoked legacy mux")) as legacy_mux:
            result = worker.execute_run(self.store, run["run_id"])
        self.assertEqual(result["status"], "finished", result.get("error"))
        legacy_stage.assert_not_called()
        captions.assert_not_called()
        alignment.assert_not_called()
        subtitle.assert_not_called()
        legacy_mux.assert_not_called()

        artifacts = self.store.get(job_id)["artifacts"]
        self.assertEqual({item["kind"] for item in artifacts}, {"preview", "source", "web_preview"})
        resolved = {item["kind"]: _artifact(self.store, job_id, item["id"])["path"] for item in artifacts}
        preview = Path(resolved["preview"])
        source_zip = Path(resolved["source"])
        player = Path(resolved["web_preview"])
        info = json.loads(subprocess.check_output([
            "ffprobe", "-v", "error", "-count_frames", "-show_streams", "-show_format", "-of", "json", str(preview)
        ]))
        video = next(stream for stream in info["streams"] if stream["codec_type"] == "video")
        self.assertEqual((video["codec_name"], video["width"], video["height"], video["avg_frame_rate"]),
                         ("h264", 1080, 1920, "30/1"))
        self.assertEqual(video["nb_read_frames"], "30")
        self.assertFalse(any(stream["codec_type"] == "audio" for stream in info["streams"]))
        self.assertAlmostEqual(float(info["format"]["duration"]), 1.0, places=2)
        self.assertLessEqual(preview.stat().st_size, 512 * 1024 * 1024)

        html_bytes = html.encode("utf-8")
        with zipfile.ZipFile(source_zip) as archive:
            self.assertEqual(set(archive.namelist()), {"index.html", "project.json", "manifest.json"})
            self.assertEqual(archive.read("index.html"), html_bytes)
            project_bytes = archive.read("project.json")
            manifest = json.loads(archive.read("manifest.json"))
        self.assertEqual(manifest["files"]["index.html"]["sha256"], hashlib.sha256(html_bytes).hexdigest())
        self.assertEqual(manifest["files"]["project.json"]["sha256"], hashlib.sha256(project_bytes).hexdigest())
        self.assertIn("preview.mp4", player.read_text(encoding="utf-8"))
        self._verify_player_in_chrome(player)

    def test_music_only_custom_render_skips_asr_and_subtitles(self):
        job = self.store.create("music-only custom scene", mode="autonomous")
        record, rev = _store_audio_blob(self.store, job["job_id"], "bed", "music", _wav_bytes())
        composition = _voice_composition(_speech_scene(with_setter=False), record, mode="none")
        composition["speech_anchors"] = []
        created = self.store.write_composition(job["job_id"], rev, composition)
        run = self.store.claim(job["job_id"], created["rev"], "preview")
        with patch.object(worker, "_run_alignment", side_effect=AssertionError("music-only path invoked ASR")) as alignment, \
             patch.object(worker, "_write_custom_subtitle", side_effect=AssertionError("music-only path wrote subtitles")) as subtitle:
            result = worker.execute_run(self.store, run["run_id"])
        self.assertEqual(result["status"], "finished", result.get("error"))
        alignment.assert_not_called()
        subtitle.assert_not_called()
        kinds = {item["kind"] for item in self.store.get(job["job_id"])["artifacts"]}
        self.assertEqual(kinds, {"preview", "source", "web_preview"})
        preview = Path(_artifact(self.store, job["job_id"], result["artifact"]["id"])["path"])
        info = json.loads(subprocess.check_output([
            "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(preview)
        ]))
        self.assertEqual({stream["codec_type"] for stream in info["streams"]}, {"video", "audio"})
        audio = next(stream for stream in info["streams"] if stream["codec_type"] == "audio")
        self.assertEqual((audio["codec_name"], audio["sample_rate"]), ("aac", "48000"))

    def test_voice_ass_alignment_sets_one_visible_action_and_publishes_one_sidecar(self):
        job_id, rev = _new_approved_naskah_job(self.store)
        record, rev = _store_audio_blob(self.store, job_id, "voice", "voice", _wav_bytes())
        bed_record, rev = _store_audio_blob(self.store, job_id, "bed", "music", _wav_bytes())
        anchor = {"id": "arrival", "word_id": "w00002", "action_id": "train-arrives", "offset_ms": 0}
        composition = _voice_composition(_speech_scene(), record, anchors=[anchor])
        composition["audio_tracks"].append({
            "track_id": bed_record["track_id"], "blob_ref": bed_record["blob_ref"], "kind": bed_record["kind"],
            "offset_ms": 0, "trim_start_ms": 0, "trim_end_ms": bed_record["duration_ms"],
            "gain_db": -20.0, "fade_in_ms": 0, "fade_out_ms": 0,
        })
        created = self.store.write_composition(job_id, rev, composition)
        run = self.store.claim(job_id, created["rev"], "preview")
        timeline = _speech_timeline(record["sha256"], record["duration_ms"])
        with patch.object(worker, "_run_alignment", return_value=timeline) as alignment:
            result = worker.execute_run(self.store, run["run_id"])
        self.assertEqual(result["status"], "finished", result.get("error"))
        alignment.assert_called_once()
        kinds = [item["kind"] for item in result["additional_artifacts"]]
        self.assertEqual(kinds.count("subtitles"), 1)
        self.assertEqual(kinds.count("speech_timeline"), 1)
        preview = Path(_artifact(self.store, job_id, result["artifact"]["id"])["path"])
        info = json.loads(subprocess.check_output([
            "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(preview)
        ]))
        self.assertEqual({stream["codec_type"] for stream in info["streams"]}, {"video", "audio"})
        audio = next(stream for stream in info["streams"] if stream["codec_type"] == "audio")
        self.assertEqual((audio["codec_name"], audio["sample_rate"]), ("aac", "48000"))
        before = _frame_pixel(preview, 5, 180, 910)
        before_anchor = _frame_pixel(preview, 10, 180, 910)
        background = _frame_pixel(preview, 10, 810, 910)
        after = _frame_pixel(preview, 11, 810, 910)
        self.assertLess(max(abs(left - right) for left, right in zip(before, before_anchor)), 12)
        self.assertGreater(before[2], before[0] + 30)
        self.assertGreater(background[0], 200)
        self.assertGreater(after[1], after[0] + 80)
        self.assertGreater(after[1], after[2] + 40)
        source_id = next(item["id"] for item in result["additional_artifacts"] if item["kind"] == "source")
        source = Path(_artifact(self.store, job_id, source_id)["path"])
        subtitle_id = next(item["id"] for item in result["additional_artifacts"] if item["kind"] == "subtitles")
        subtitle = Path(_artifact(self.store, job_id, subtitle_id)["path"])
        timeline_id = next(item["id"] for item in result["additional_artifacts"] if item["kind"] == "speech_timeline")
        speech_timeline = Path(_artifact(self.store, job_id, timeline_id)["path"])
        self.assertTrue(subtitle.is_file())
        self.assertEqual(json.loads(speech_timeline.read_text(encoding="utf-8"))["audio_sha256"], record["sha256"])
        with zipfile.ZipFile(source) as archive:
            self.assertIn("audio/voice.wav", archive.namelist())
            self.assertIn("audio/bed.wav", archive.namelist())
            self.assertIn("speech_timeline.json", archive.namelist())
            self.assertIn("subtitles.ass", archive.namelist())

    def test_voice_web_karaoke_registers_hashed_json_without_ass_burn(self):
        naskah = {"beats": [{"id": "b1", "narration": "Cakra Selaras Wahana Pagi."}]}
        job_id, rev = _new_approved_naskah_job(self.store, naskah=naskah)
        voice_bytes = _wav_bytes()
        record, rev = _store_audio_blob(self.store, job_id, "voice", "voice", voice_bytes)
        composition = _voice_composition(_speech_scene(), record, mode="web")
        created = self.store.write_composition(job_id, rev, composition)
        run = self.store.claim(job_id, created["rev"], "preview")
        words = [
            {"id": "w00001", "text": "Cakra", "start_ms": 100, "end_ms": 200, "speaker_id": None, "timing_status": "aligned"},
            {"id": "w00002", "text": "Selaras", "start_ms": 250, "end_ms": 350, "speaker_id": None, "timing_status": "aligned"},
            {"id": "w00003", "text": "Wahana", "start_ms": 400, "end_ms": 500, "speaker_id": None, "timing_status": "aligned"},
            {"id": "w00004", "text": "Pagi.", "start_ms": 600, "end_ms": 800, "speaker_id": None, "timing_status": "aligned"},
        ]
        timeline = _karaoke_timeline(record["sha256"], record["duration_ms"], words)
        expected_sidecar = render_karaoke_subtitles(
            timeline, audio_sha256=record["sha256"], track_duration_ms=record["duration_ms"],
        )
        with (
            patch.object(worker, "_run_alignment", return_value=timeline) as alignment,
            patch.object(worker, "_build_custom_mux_argv", wraps=worker._build_custom_mux_argv) as mux_builder,
        ):
            result = worker.execute_run(self.store, run["run_id"])
        self.assertEqual(result["status"], "finished", result.get("error"))
        alignment.assert_called_once()
        mux_builder.assert_called_once()
        mux_call = mux_builder.call_args
        if mux_call is None:
            self.fail("audio mux builder was not called")
        self.assertIsNone(mux_call.kwargs.get("subtitle_filename"))
        artifacts = self.store.get(job_id)["artifacts"]
        self.assertEqual([item["kind"] for item in artifacts].count("subtitles"), 1)
        resolved = {item["kind"]: Path(_artifact(self.store, job_id, item["id"])["path"])
                    for item in artifacts}
        subtitle_path = resolved["subtitles"]
        source_path = resolved["source"]
        self.assertEqual(subtitle_path.name, "subtitles.json")
        self.assertEqual(subtitle_path.read_bytes(), expected_sidecar)
        self.assertFalse((self.root / "runs" / run["run_id"] / "subtitles.ass").exists())
        info = json.loads(subprocess.check_output([
            "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(resolved["preview"])
        ]))
        self.assertEqual({stream["codec_type"] for stream in info["streams"]}, {"video", "audio"})
        audio = next(stream for stream in info["streams"] if stream["codec_type"] == "audio")
        self.assertEqual((audio["codec_name"], audio["sample_rate"]), ("aac", "48000"))
        self.assertEqual(record["sha256"], hashlib.sha256(voice_bytes).hexdigest())
        with zipfile.ZipFile(source_path) as archive:
            self.assertIn("subtitles.json", archive.namelist())
            self.assertNotIn("subtitles.ass", archive.namelist())
            self.assertEqual(archive.read("subtitles.json"), expected_sidecar)
            self.assertEqual(archive.read("audio/voice.wav"), voice_bytes)
            manifest = json.loads(archive.read("manifest.json"))
        self.assertEqual(
            manifest["files"]["subtitles.json"]["sha256"],
            hashlib.sha256(expected_sidecar).hexdigest(),
        )
        self.assertEqual(manifest["files"]["subtitles.json"]["size_bytes"], len(expected_sidecar))

    def test_web_karaoke_corrupt_sidecar_fails_without_publishing_artifacts(self):
        naskah = {"beats": [{"id": "b1", "narration": "Cakra Selaras Wahana Pagi."}]}
        job_id, rev = _new_approved_naskah_job(self.store, naskah=naskah)
        record, rev = _store_audio_blob(self.store, job_id, "voice", "voice", _wav_bytes())
        composition = _voice_composition(_speech_scene(), record, mode="web")
        created = self.store.write_composition(job_id, rev, composition)
        run = self.store.claim(job_id, created["rev"], "preview")
        words = [
            {"id": "w00001", "text": "Cakra", "start_ms": 100, "end_ms": 200, "speaker_id": None, "timing_status": "aligned"},
            {"id": "w00002", "text": "Selaras", "start_ms": 250, "end_ms": 350, "speaker_id": None, "timing_status": "aligned"},
            {"id": "w00003", "text": "Wahana", "start_ms": 400, "end_ms": 500, "speaker_id": None, "timing_status": "aligned"},
            {"id": "w00004", "text": "Pagi.", "start_ms": 600, "end_ms": 800, "speaker_id": None, "timing_status": "aligned"},
        ]
        timeline = _karaoke_timeline(record["sha256"], record["duration_ms"], words)

        def corrupt_sidecar(workspace, _timeline, _voice_track):
            path = workspace / "subtitles.json"
            path.write_bytes(b"{broken")
            return path, b"{broken"

        with (
            patch.object(worker, "_run_alignment", return_value=timeline),
            patch.object(worker, "_write_custom_karaoke", side_effect=corrupt_sidecar),
        ):
            result = worker.execute_run(self.store, run["run_id"])
        if result is None:
            self.fail("worker returned no result")
        self.assertEqual(result["status"], "failed")
        self.assertIn("invalid karaoke subtitle json", result.get("error", "").lower())
        self.assertFalse(self.store.get(job_id)["artifacts"])
        run_dir = self.root / "runs" / run["run_id"]
        for name in ("preview.mp4", "preview.partial.mp4", "source.zip", "web-preview.html",
                     "subtitles.json", "speech_timeline.json"):
            self.assertFalse((run_dir / name).exists(), name)

    def test_timing_required_scene_without_set_timeline_fails_cleanly(self):
        job_id, rev = _new_approved_naskah_job(self.store)
        record, rev = _store_audio_blob(self.store, job_id, "voice", "voice", _wav_bytes())
        anchor = {"id": "arrival", "word_id": "w00002", "action_id": "train-arrives", "offset_ms": 0}
        created = self.store.write_composition(
            job_id, rev, _voice_composition(_speech_scene(with_setter=False), record, anchors=[anchor]),
        )
        run = self.store.claim(job_id, created["rev"], "preview")
        timeline = _speech_timeline(record["sha256"], record["duration_ms"])
        with patch.object(worker, "_run_alignment", return_value=timeline):
            result = worker.execute_run(self.store, run["run_id"])
        self.assertEqual(result["status"], "failed")
        self.assertIn("setTimeline", result.get("error", ""))
        self.assertFalse(self.store.get(job_id)["artifacts"])
        run_dir = self.root / "runs" / run["run_id"]
        for name in ("preview.mp4", "preview.partial.mp4", "source.zip", "subtitles.ass", "speech_timeline.json"):
            self.assertFalse((run_dir / name).exists(), name)

    def test_asr_failure_publishes_no_partial_audio_or_subtitle_artifacts(self):
        job_id, rev = _new_approved_naskah_job(self.store)
        record, rev = _store_audio_blob(self.store, job_id, "voice", "voice", _wav_bytes())
        anchor = {"id": "arrival", "word_id": "w00002", "action_id": "train-arrives", "offset_ms": 0}
        created = self.store.write_composition(job_id, rev, _voice_composition(_speech_scene(), record, anchors=[anchor]))
        run = self.store.claim(job_id, created["rev"], "preview")
        with patch.object(worker, "_run_alignment", side_effect=worker.DomainError("ASR_FAILED", "forced failure")):
            result = worker.execute_run(self.store, run["run_id"])
        self.assertEqual(result["status"], "failed")
        self.assertFalse(self.store.get(job_id)["artifacts"])
        run_dir = self.root / "runs" / run["run_id"]
        for name in ("preview.mp4", "preview.partial.mp4", "source.zip", "web-preview.html", "subtitles.ass", "speech_timeline.json"):
            self.assertFalse((run_dir / name).exists(), name)

    def test_mux_failure_publishes_no_partial_audio_or_subtitle_artifacts(self):
        job_id, rev = _new_approved_naskah_job(self.store)
        record, rev = _store_audio_blob(self.store, job_id, "voice", "voice", _wav_bytes())
        anchor = {"id": "arrival", "word_id": "w00002", "action_id": "train-arrives", "offset_ms": 0}
        created = self.store.write_composition(job_id, rev, _voice_composition(_speech_scene(), record, anchors=[anchor]))
        run = self.store.claim(job_id, created["rev"], "preview")
        timeline = _speech_timeline(record["sha256"], record["duration_ms"])
        with (
            patch.object(worker, "_run_alignment", return_value=timeline),
            patch.object(worker, "_build_custom_mux_argv", return_value=[sys.executable, "-c", "raise SystemExit(7)"]),
        ):
            result = worker.execute_run(self.store, run["run_id"])
        self.assertEqual(result["status"], "failed")
        self.assertFalse(self.store.get(job_id)["artifacts"])
        run_dir = self.root / "runs" / run["run_id"]
        for name in ("preview.mp4", "preview.partial.mp4", "source.zip", "web-preview.html", "subtitles.ass", "speech_timeline.json"):
            self.assertFalse((run_dir / name).exists(), name)

    def test_custom_worker_rejects_tampered_revision_html_before_sandbox_start(self):
        job_id, run = self._new_run(_scene())
        source = self.root / "jobs" / job_id / "revisions" / str(run["rev"]) / "composition" / "index.html"
        source.write_text(_scene(fail_after=0.5), encoding="utf-8")
        directory = self.root / "runs" / run["run_id"]
        with patch.object(worker.sandbox, "create_workspace", side_effect=AssertionError("sandbox must not start")):
            with self.assertRaises(worker.DomainError) as raised:
                worker._render_custom(self.store, run, directory, 0.0)
        self.assertEqual(raised.exception.code, "INVALID_COMPOSITION")

    def test_custom_execution_bounds_snapshot_before_json_read(self):
        job_id, run = self._new_run(_scene())
        with patch("ump_core.composition.MAX_REVISION_BYTES", 64):
            with patch.object(worker, "_read", side_effect=AssertionError("unbounded snapshot read")) as read:
                result = worker.execute_run(self.store, run["run_id"])
        read.assert_not_called()
        if result is None:
            self.fail("worker did not return a run result")
        self.assertEqual(result["status"], "failed")
        self.assertIn("bounded user-owned regular file", result.get("error", ""))
        self.assertFalse(self.store.get(job_id)["artifacts"])

    def test_custom_worker_rejects_tampered_snapshot_before_sandbox_start(self):
        job_id, run = self._new_run(_scene())
        snapshot = self.root / run["snapshot_path"]
        payload = json.loads(snapshot.read_text(encoding="utf-8"))
        payload["brief"] = "forged after revision approval"
        snapshot.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        directory = self.root / "runs" / run["run_id"]
        with patch.object(worker.sandbox, "create_workspace", side_effect=AssertionError("sandbox must not start")):
            with self.assertRaises(worker.DomainError) as raised:
                worker._render_custom(self.store, run, directory, 0.0)
        self.assertEqual(raised.exception.code, "INVALID_COMPOSITION")

    def test_custom_worker_rejects_tampered_pinned_asset_before_sandbox_start(self):
        svg = '<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>'
        AssetStore(self.root).add_svg("leaf", svg, source="test", provenance="test", license="MIT")
        job = self.store.create("tampered worker input", mode="autonomous")
        created = self.store.write_composition(job["job_id"], job["rev"], {
            "html": '<!doctype html><img src="assets/leaf-v1.svg">',
            **_project(),
            "assets": [{"id": "leaf", "version": 1}],
        })
        run = self.store.claim(job["job_id"], created["rev"], "preview")
        revision = self.root / "jobs" / job["job_id"] / "revisions" / str(created["rev"])
        pinned = json.loads((revision / "project.json").read_text(encoding="utf-8"))["assets"][0]
        (revision / "composition" / pinned["path"]).write_bytes(b"tampered")
        directory = self.root / "runs" / run["run_id"]
        with patch.object(worker.sandbox, "create_workspace", side_effect=AssertionError("sandbox must not start")):
            with self.assertRaises(worker.DomainError) as raised:
                worker._render_custom(self.store, run, directory, 0.0)
        self.assertEqual(raised.exception.code, "ASSET_INTEGRITY")

    def test_revision_asset_loads_in_browser_and_is_in_offline_source_bundle(self):
        svg = '<svg xmlns="http://www.w3.org/2000/svg" width="8" height="6" viewBox="0 0 8 6"><rect width="8" height="6" fill="#13579b"/></svg>'
        asset = AssetStore(self.root).add_svg(
            "leaf", svg, source="hermes-procedural", provenance="M2 pinned browser asset", license="MIT",
        )
        job = self.store.create("browser load pinned asset", mode="autonomous")
        created = self.store.write_composition(job["job_id"], job["rev"], {
            "html": _scene_with_asset(),
            **_project(),
            "assets": [{"id": "leaf", "version": asset["version"]}],
        })
        run = self.store.claim(job["job_id"], created["rev"], "preview")
        result = worker.execute_run(self.store, run["run_id"])
        self.assertEqual(result["status"], "finished", result.get("error"))
        artifacts = self.store.get(job["job_id"])["artifacts"]
        resolved = {item["kind"]: _artifact(self.store, job["job_id"], item["id"])["path"]
                    for item in artifacts}
        with zipfile.ZipFile(resolved["source"]) as archive:
            asset_path = "assets/leaf-v1.svg"
            self.assertEqual(archive.read(asset_path), svg.encode("utf-8"))
            self.assertEqual(archive.namelist().count(asset_path), 1)
            project_bytes = archive.read("project.json")
            manifest = json.loads(archive.read("manifest.json"))
        self.assertEqual(manifest["files"][asset_path]["sha256"], asset["sha256"])
        self.assertEqual(manifest["files"][asset_path]["size_bytes"], len(svg.encode("utf-8")))
        self.assertEqual(json.loads(project_bytes)["assets"][0]["sha256"], asset["sha256"])

    def test_partial_custom_render_publishes_no_artifacts(self):
        before = {entry.name for entry in sandbox.WORKSPACE_ROOT.iterdir() if entry.name != ".render.lock"}
        job_id, run = self._new_run(_scene(fail_after=0.5))
        result = worker.execute_run(self.store, run["run_id"])
        self.assertEqual(result["status"], "failed")
        self.assertIn("intentional seek failure", result.get("error", "").lower())
        self.assertFalse(self.store.get(job_id)["artifacts"])
        run_dir = self.root / "runs" / run["run_id"]
        for name in ("preview.mp4", "preview.partial.mp4", "source.zip", "web-preview.html"):
            self.assertFalse((run_dir / name).exists(), name)
        after = {entry.name for entry in sandbox.WORKSPACE_ROOT.iterdir() if entry.name != ".render.lock"}
        self.assertEqual(after, before, "failed render must clean its private tmpfs workspace")

    def test_worker_timeout_reaps_units_and_cleans_workspace(self):
        before = {entry.name for entry in sandbox.WORKSPACE_ROOT.iterdir() if entry.name != ".render.lock"}
        job_id, run = self._new_run(_scene(duration_frames=1800), duration_frames=1800)
        start_isolated = sandbox.start_isolated
        units = []

        def track_unit(argv, **kwargs):
            process, unit = start_isolated(argv, **kwargs)
            units.append(unit)
            return process, unit

        with patch.object(worker, "RUN_TIMEOUT", 0.2):
            with patch.object(sandbox, "start_isolated", side_effect=track_unit):
                result = worker.execute_run(self.store, run["run_id"])

        self.assertEqual(result["status"], "failed")
        self.assertFalse(self.store.get(job_id)["artifacts"])
        self.assertEqual(len(units), 2)
        for unit in units:
            unit_state = subprocess.run(
                ["sudo", "-n", "systemctl", "--system", "is-active", unit],
                capture_output=True, text=True, timeout=10,
            )
            self.assertIn(unit_state.stdout.strip(), {"inactive", "failed", "unknown", "not-found"})
        after = {entry.name for entry in sandbox.WORKSPACE_ROOT.iterdir() if entry.name != ".render.lock"}
        self.assertEqual(after, before, "timed-out render must clean its private workspace")

    def test_staged_composition_assets_remain_available(self):
        workspace = sandbox.create_workspace("a" + uuid.uuid4().hex[:8])
        self.addCleanup(lambda: sandbox.cleanup_workspace(workspace) if workspace.exists() else None)
        composition = workspace / "composition"
        composition.mkdir(mode=0o700)
        source = composition / "index.html"
        asset = composition / "tile.svg"
        asset.write_text("<svg xmlns='http://www.w3.org/2000/svg' width='8' height='8'><rect width='8' height='8' fill='red'/></svg>", encoding="utf-8")
        html = _scene().replace(
            "</canvas>",
            f"</canvas><img id='asset' src='{asset.as_uri()}' style='position:absolute;left:0;top:0;width:8px;height:8px'>",
        ).replace(
            "async ready() {",
            "async ready() { const asset=document.querySelector('#asset'); await asset.decode(); "
            "if(asset.naturalWidth!==8) throw new Error('workspace asset did not load');",
        )
        source.write_text(html, encoding="utf-8")
        project = workspace / "project.json"
        project.write_text(json.dumps(_project()), encoding="utf-8")
        output = workspace / "probes"
        process, unit = sandbox.start_isolated(
            ["node", str(ROOT / "scripts" / "record_custom.mjs"), "--probe-seeks",
             str(source), str(project), str(output), "0"],
            workspace=workspace, private_pid_namespace=True, timeout_s=60,
        )
        status = sandbox.wait_isolated(process, unit, timeout_s=30)
        stdout, stderr = process.communicate()
        self.assertEqual(status, 0, stderr.decode("utf-8", "replace"))
        self.assertEqual(json.loads(stdout)["frames"], 1)
        self.assertTrue((output / "seek-00.png").is_file())

    def test_external_file_requests_fail_closed_without_artifacts(self):
        html = _scene().replace(
            "</canvas>",
            "</canvas><iframe src='file:///etc/hostname' "
            "style='position:fixed;inset:0;width:1080px;height:1920px;border:0'></iframe>",
        )
        job_id, run = self._new_run(html)
        result = worker.execute_run(self.store, run["run_id"])
        self.assertEqual(result["status"], "failed")
        self.assertIn("local file", result.get("error", "").lower())
        self.assertFalse(self.store.get(job_id)["artifacts"])

    def test_external_requests_fail_closed_without_artifacts(self):
        job_id, run = self._new_run(_scene(external_url="https://example.invalid/asset.png"))
        result = worker.execute_run(self.store, run["run_id"])
        self.assertEqual(result["status"], "failed")
        self.assertIn("external request", result.get("error", "").lower())
        self.assertFalse(self.store.get(job_id)["artifacts"])

    def test_ffmpeg_early_exit_cleans_workspace_and_publishes_no_artifacts(self):
        before = {entry.name for entry in sandbox.WORKSPACE_ROOT.iterdir() if entry.name != ".render.lock"}
        job_id, run = self._new_run(_scene())
        start_isolated = sandbox.start_isolated
        units = []

        def fail_ffmpeg_after_input(argv, **kwargs):
            if Path(argv[0]).name == "ffmpeg":
                argv = [sys.executable, "-c", "import sys; data=sys.stdin.buffer.read(16384); print(f'consumed {len(data)} bytes',file=sys.stderr); raise SystemExit(7)"]
            process, unit = start_isolated(argv, **kwargs)
            units.append(unit)
            return process, unit

        with patch.object(sandbox, "start_isolated", side_effect=fail_ffmpeg_after_input):
            result = worker.execute_run(self.store, run["run_id"])

        self.assertEqual(result["status"], "failed")
        self.assertFalse(self.store.get(job_id)["artifacts"])
        run_dir = self.root / "runs" / run["run_id"]
        for name in ("preview.mp4", "preview.partial.mp4", "source.zip", "web-preview.html"):
            self.assertFalse((run_dir / name).exists(), name)
        self.assertEqual(len(units), 2)
        for unit in units:
            unit_state = subprocess.run(
                ["sudo", "-n", "systemctl", "--system", "is-active", unit],
                capture_output=True, text=True, timeout=10,
            )
            self.assertIn(unit_state.stdout.strip(), {"inactive", "failed", "unknown", "not-found"})
        after = {entry.name for entry in sandbox.WORKSPACE_ROOT.iterdir() if entry.name != ".render.lock"}
        self.assertEqual(after, before, "failed encode must reap units and clean the private workspace")

    def _verify_player_in_chrome(self, player):
        script = r"""
import { chromium } from 'playwright-core';
import { pathToFileURL } from 'node:url';
const browser = await chromium.launch({executablePath:'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
try {
  const page = await browser.newPage();
  await page.goto(pathToFileURL(process.argv[1]).href);
  await page.waitForFunction(() => document.querySelector('video')?.readyState >= 1);
  const video = page.locator('video');
  const duration = await video.evaluate(v => v.duration);
  await video.evaluate(v => { v.currentTime = 0.5; });
  await page.waitForFunction(() => Math.abs(document.querySelector('video').currentTime - 0.5) < 0.05);
  console.log(JSON.stringify({duration, currentTime: await video.evaluate(v => v.currentTime)}));
} finally { await browser.close(); }
"""
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script, str(player)],
            cwd=ROOT / "scripts", check=True, capture_output=True, text=True, timeout=45,
        )
        values = json.loads(result.stdout)
        self.assertAlmostEqual(values["duration"], 1.0, places=2)
        self.assertAlmostEqual(values["currentTime"], 0.5, places=1)
