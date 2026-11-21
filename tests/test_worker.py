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

    def _advance_to_style_approved(self):
        chain = [
            ('set_brief', 'intake', 'brief_draft'),
            ('approve', 'brief_draft', 'brief_approved'),
            ('research', 'brief_approved', 'research_draft'),
            ('approve', 'research_draft', 'research_approved'),
            ('write_naskah', 'research_approved', 'naskah_draft'),
            ('approve', 'naskah_draft', 'naskah_approved'),
            ('select_style', 'naskah_approved', 'style_approved'),
        ]
        for action, expect, target in chain:
            if self.store.stage_of(self.job) == expect:
                self.store.advance(self.job, self.store.get(self.job)['rev'], action,
                                   expect=expect, to=target)
        self.assertEqual(self.store.stage_of(self.job), 'style_approved')
        return self.store.get(self.job)['rev']

    def test_worker_failure(self):
        rev = self._advance_to_style_approved()
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
            rev = self._advance_to_style_approved()
            run = worker.submit(self.store, self.job, rev, 'style_frame')
            self.assertIn('run_id', run)
            self.assertIsNone(children[0].poll())
            with self.assertRaisesRegex(DomainError, 'ALREADY_RUNNING'):
                worker.submit(self.store, self.job, rev, 'style_frame')

    def test_document_targets_publish_json_without_render(self):
        job = self.store.create('documents')['job_id']
        self.store.revise(job, 1, {'brief': 'Topik CSW untuk penonton komuter'})
        self.store.advance(job, 2, 'set_brief', expect='intake', to='brief_draft')
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
        self.store.advance(job, 2, 'set_brief', expect='intake', to='brief_draft')
        self.store.claim(job, 2, 'brief')

    def test_abandoned_worker(self):
        rev = self._advance_to_style_approved()
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
