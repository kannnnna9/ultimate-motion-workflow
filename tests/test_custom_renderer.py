import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
import zipfile
from pathlib import Path
from unittest.mock import patch

from ump_core import sandbox, worker
from ump_core.jobs import JobStore
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


def _project(duration_frames=30):
    return {
        "width": 1080,
        "height": 1920,
        "fps": 30,
        "duration_frames": duration_frames,
        "audio_enabled": False,
        "subtitles_enabled": False,
    }


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
             patch.object(worker, "prepare_audio", side_effect=AssertionError("custom path invoked ASR/captions")) as captions, \
             patch.object(worker, "_mux", side_effect=AssertionError("custom path invoked legacy mux")) as legacy_mux:
            result = worker.execute_run(self.store, run["run_id"])
        self.assertEqual(result["status"], "finished", result.get("error"))
        legacy_stage.assert_not_called()
        captions.assert_not_called()
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
