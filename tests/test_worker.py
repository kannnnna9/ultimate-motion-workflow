import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ump_core import worker
from ump_core.jobs import DomainError, JobStore


class WorkerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.store = JobStore(self.root)
        self.job = self.store.create('test worker')['job_id']

    def test_copy_regular_output_copies_bounded_workspace_file(self):
        workspace = self.root / "sandbox"
        workspace.mkdir()
        source = workspace / "render.mp4"
        source.write_bytes(b"video")
        destination = self.root / "preview.partial.mp4"

        worker._copy_regular_output(source, destination, workspace=workspace, max_bytes=8)

        self.assertEqual(destination.read_bytes(), b"video")

    def test_copy_regular_output_rejects_symlink(self):
        workspace = self.root / "sandbox"
        workspace.mkdir()
        secret = self.root / "secret.bin"
        secret.write_bytes(b"not-render-output")
        source = workspace / "render.mp4"
        source.symlink_to(secret)

        with self.assertRaises(DomainError) as raised:
            worker._copy_regular_output(source, self.root / "preview.partial.mp4",
                                        workspace=workspace, max_bytes=64)

        self.assertEqual(raised.exception.code, "INVALID_ARTIFACT")
        self.assertFalse((self.root / "preview.partial.mp4").exists())

    def test_copy_regular_output_rejects_hardlink(self):
        workspace = self.root / "sandbox"
        workspace.mkdir()
        secret = self.root / "secret.bin"
        secret.write_bytes(b"not-render-output")
        source = workspace / "render.mp4"
        os.link(secret, source)

        with self.assertRaises(DomainError) as raised:
            worker._copy_regular_output(source, self.root / "preview.partial.mp4",
                                        workspace=workspace, max_bytes=64)

        self.assertEqual(raised.exception.code, "INVALID_ARTIFACT")
        self.assertFalse((self.root / "preview.partial.mp4").exists())

    def _advance_to_naskah_approved(self):
        chain = [
            ('set_brief', 'intake', 'brief_draft'),
            ('approve', 'brief_draft', 'brief_approved'),
            ('research', 'brief_approved', 'research_draft'),
            ('approve', 'research_draft', 'research_approved'),
            ('select_style', 'research_approved', 'style_approved'),
            ('write_naskah', 'style_approved', 'naskah_draft'),
            ('approve', 'naskah_draft', 'naskah_approved'),
            ('advance', 'naskah_approved', 'storyboard'),
        ]
        for action, expect, target in chain:
            if self.store.stage_of(self.job) == expect:
                self.store.advance(self.job, self.store.get(self.job)['rev'], action,
                                   expect=expect, to=target)
        self.assertEqual(self.store.stage_of(self.job), 'storyboard')
        return self.store.get(self.job)['rev']

    def test_finish_registers_additional_artifacts_atomically(self):
        composition = {
            "html": "<!doctype html><body>artifact test</body>",
            "width": 1080, "height": 1920, "fps": 30, "duration_frames": 30,
            "audio_enabled": False, "subtitles_enabled": False,
        }
        job = self.store.create("artifact bundle", mode="autonomous")
        custom = self.store.write_composition(job["job_id"], job["rev"], composition)
        run = self.store.claim(job["job_id"], custom["rev"], "preview")
        directory = self.root / "runs" / run["run_id"]
        (directory / "preview.mp4").write_bytes(b"video")
        (directory / "source.zip").write_bytes(b"source")
        (directory / "web-preview.html").write_text("<video controls>", encoding="utf-8")

        finished = self.store.finish(
            run["run_id"], "preview.mp4", None,
            additional_artifacts={"source": "source.zip", "web_preview": "web-preview.html"},
        )
        self.assertEqual(finished["status"], "finished")
        self.assertEqual(finished["artifact"]["kind"], "preview")
        self.assertEqual({item["kind"] for item in self.store.get(job["job_id"])["artifacts"]},
                         {"preview", "source", "web_preview"})

        second = self.store.create("artifact rollback", mode="autonomous")
        custom2 = self.store.write_composition(second["job_id"], second["rev"], composition)
        run2 = self.store.claim(second["job_id"], custom2["rev"], "preview")
        (self.root / "runs" / run2["run_id"] / "preview.mp4").write_bytes(b"video")
        with self.assertRaises(DomainError) as raised:
            self.store.finish(run2["run_id"], "preview.mp4", None,
                              additional_artifacts={"source": "../outside.zip"})
        self.assertEqual(raised.exception.code, "INVALID_ARTIFACT")
        self.assertEqual(self.store.get_run(run2["run_id"])["status"], "running")
        self.assertFalse(self.store.get(second["job_id"])["artifacts"])
        self.store.finish(run2["run_id"], None, "test abort")

    def test_worker_failure(self):
        rev = self._advance_to_naskah_approved()
        run = self.store.claim(self.job, rev, 'style_frame')['run_id']
        worker.execute_run(self.store, run, [sys.executable, '-c', 'raise SystemExit(7)'])
        status = JobStore(self.root).get(self.job)
        self.assertEqual(status['status'], 'failed')
        self.assertIn('exit 7', status['error'])
        self.assertFalse(status['artifacts'])
        self.assertFalse(list(self.root.rglob('*.mp4')))

    def test_submit_returns_id(self):
        # Spawn a real child whose lifetime is bounded by the parent's pipe.
        children = []
        def spawn(*args, **kwargs):
            p = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.read()'], stdin=subprocess.PIPE)
            children.append(p)
            self.addCleanup(p.wait, 5)
            assert p.stdin is not None
            self.addCleanup(p.stdin.close)
            return p
        with patch.object(worker, '_spawn', spawn, create=True):
            rev = self._advance_to_naskah_approved()
            run = worker.submit(self.store, self.job, rev, 'style_frame')
            self.assertIn('run_id', run)
            self.assertIsNone(children[0].poll())
            with self.assertRaisesRegex(DomainError, 'ALREADY_RUNNING'):
                worker.submit(self.store, self.job, rev, 'style_frame')

    def test_document_targets_publish_json_without_render(self):
        job = self.store.create('documents')['job_id']
        self.store.revise(job, 1, {'brief': 'Topik CSW untuk penonton komuter'})
        rev = self.store.get(job)['rev']
        run = self.store.claim(job, rev, 'brief')['run_id']
        finished = worker.execute_run(self.store, run)
        self.assertEqual(finished['status'], 'finished')
        self.assertEqual(finished['artifact']['kind'], 'brief')
        path = self.root / finished['artifact']['path']
        self.assertTrue(path.is_file())
        self.assertEqual(path.name, 'brief.json')
        import json as _json
        self.assertIn('CSW', _json.loads(path.read_text())['brief'])

    def test_document_target_requires_its_stage(self):
        with self.assertRaisesRegex(DomainError, 'STAGE_ORDER'):
            self.store.claim(self.job, 1, 'brief')
        job = self.store.create('documents')['job_id']
        self.store.revise(job, 1, {'brief': 'x'})
        self.store.claim(job, 2, 'brief')

    def test_abandoned_worker(self):
        rev = self._advance_to_naskah_approved()
        run = self.store.claim(self.job, rev, 'style_frame')['run_id']
        run_dir = self.root / 'runs' / run
        # Real OS lock, then SIGKILL without finish.
        child = subprocess.Popen([sys.executable, '-c',
            'import fcntl,sys; f=open(sys.argv[1],"w"); fcntl.flock(f,2); print("ready",flush=True); sys.stdin.read()',
            str(run_dir / 'worker.lock')], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        try:
            assert child.stdout is not None
            self.assertEqual(child.stdout.readline(), b'ready\n')
            (run_dir / 'started').touch()
            child.kill()
            child.wait(5)
        finally:
            assert child.stdin is not None and child.stdout is not None
            child.stdin.close()
            child.stdout.close()
        worker.reconcile(self.store, self.job)
        self.assertEqual(JobStore(self.root).get_run(run)['status'], 'failed')
        self.assertNotEqual(self.store.claim(self.job, 1, 'style_frame')['run_id'], run)

    def test_import_audio(self):
        audio = self.root / 'assets' / 'audio'
        audio.mkdir(parents=True)
        (audio / 'vo.mp3').write_bytes(b'fixture')
        work = self.root / 'work'
        work.mkdir()
        (work / 'captions.json').write_text('{"words":[{"text":"guess","start_ms":40,"end_ms":10}]}')
        calls = []
        def runner(argv):
            calls.append(argv)
            raise DomainError('RENDER_FAILED', 'transcriber not installed')
        with self.assertRaisesRegex(DomainError, 'transcriber not installed'):
            worker.prepare_audio(self.root, runner)
        self.assertIn('external', calls[0])
        self.assertFalse((work / 'captions.json').exists())
