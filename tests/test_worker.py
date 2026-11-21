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

    def test_worker_failure(self):
        run = self.store.claim(self.job, 1, 'style_frame')['run_id']
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
            run = worker.submit(self.store, self.job, 1, 'style_frame')
            self.assertIn('run_id', run)
            self.assertIsNone(children[0].poll())
            with self.assertRaisesRegex(DomainError, 'ALREADY_RUNNING'):
                worker.submit(self.store, self.job, 1, 'style_frame')

    def test_abandoned_worker(self):
        run = self.store.claim(self.job, 1, 'style_frame')['run_id']
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
