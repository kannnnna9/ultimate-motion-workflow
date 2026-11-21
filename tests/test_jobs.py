import concurrent.futures
import json
import tempfile
import unittest
from pathlib import Path

from ump_core.jobs import DomainError, JobStore


class JobStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = JobStore(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def _advance_to(self, job_id, stage):
        """Walk the documented pipeline until `job_id` reaches `stage`."""
        chain = [
            ("set_brief", "intake", "brief_draft"),
            ("approve", "brief_draft", "brief_approved"),
            ("research", "brief_approved", "research_draft"),
            ("approve", "research_draft", "research_approved"),
            ("write_naskah", "research_approved", "naskah_draft"),
            ("approve", "naskah_draft", "naskah_approved"),
            ("select_style", "naskah_approved", "style_approved"),
            ("approve", "style_approved", "style_frame"),
        ]
        for action, expect, target in chain:
            if self.store.stage_of(job_id) == stage:
                return
            if self.store.stage_of(job_id) == expect:
                self.store.advance(job_id, self.store.get(job_id)["rev"], action, expect=expect, to=target)
        self.assertEqual(self.store.stage_of(job_id), stage)

    def test_revision_requires_new_approval(self):
        job = self.store.create("Explain the five o'clock coffee ritual")
        job_id = job["job_id"]
        self._advance_to(job_id, "style_frame")
        rev = self.store.get(job_id)["rev"]
        with self.assertRaisesRegex(DomainError, "APPROVAL_REQUIRED"):
            self.store.claim(job_id, rev, "preview")

        style = self._artifact(job_id, rev, "style_frame", "style.png")
        self.store.approve(job_id, rev, "script_style", style["id"])
        preview = self._artifact(job_id, rev, "preview", "preview.mp4")
        self.store.approve(job_id, rev, "preview", preview["id"])
        run = self.store.claim(job_id, rev, "final")
        self.store.finish(run["run_id"], None, "renderer failed")

        revised = self.store.revise(job_id, rev, {"cues": {"c007": {"t_ms": 9200}}})
        self.assertEqual(revised["rev"], rev + 1)
        self.assertEqual(self.store.get(job_id)["rev"], rev + 1)
        with self.assertRaisesRegex(DomainError, "APPROVAL_REQUIRED"):
            self.store.claim(job_id, rev + 1, "final")
        self.assertEqual(self.store.get(job_id)["artifacts"][0]["rev"], rev)

    def test_job_reopen_and_distinct_roots(self):
        job = self.store.create("persist me")
        reopened = JobStore(self.root).get(job["job_id"])
        self.assertEqual(reopened["job_id"], job["job_id"])
        self.assertTrue((self.root / "jobs" / job["job_id"] / "revisions" / "1" / "input.json").exists())

        other = tempfile.TemporaryDirectory()
        try:
            with self.assertRaisesRegex(DomainError, "NOT_FOUND"):
                JobStore(Path(other.name)).get(job["job_id"])
        finally:
            other.cleanup()

    def test_note_timestamp_and_containment(self):
        job = self.store.create("notes")
        self.store.revise(job["job_id"], 1, {"cues": {"c007": {"t_ms": 8560}}})
        note = self.store.annotate(job["job_id"], 2, 8560, "c007", "move the accent")
        self.assertEqual(note["t_ms"], 8560)
        self.assertEqual(note["cue_id"], "c007")
        self.assertEqual(self.store.get(job["job_id"])["rev"], 2)
        with self.assertRaisesRegex(DomainError, "INVALID_TIMESTAMP"):
            self.store.annotate(job["job_id"], 2, -1, "c007", "bad")
        with self.assertRaisesRegex(DomainError, "UNKNOWN_CUE"):
            self.store.annotate(job["job_id"], 2, 1, "missing", "bad")
        with self.assertRaisesRegex(DomainError, "PATH_OUTSIDE_ROOT"):
            self.store.add_artifact(job["job_id"], 2, "preview", "../escape.mp4")

    def test_inputs_are_immutable_and_artifacts_scoped(self):
        job = self.store.create("immutable")
        snapshot = self.root / "jobs" / job["job_id"] / "revisions" / "1" / "input.json"
        before = snapshot.read_bytes()
        self.store.revise(job["job_id"], 1, {"title": "new"})
        self.assertEqual(snapshot.read_bytes(), before)
        self.assertEqual(json.loads(snapshot.read_text())["brief"], "immutable")

        outside = self.root.parent / "outside-artifact.mp4"
        outside.write_bytes(b"x")
        try:
            with self.assertRaisesRegex(DomainError, "PATH_OUTSIDE_ROOT"):
                self.store.add_artifact(job["job_id"], 2, "preview", outside)
        finally:
            outside.unlink()

    def test_concurrent_claims_have_one_winner(self):
        job = self.store.create("race")
        self._advance_to(job["job_id"], "style_approved")
        rev = self.store.get(job["job_id"])["rev"]

        def claim():
            try:
                return self.store.claim(job["job_id"], rev, "style_frame")["status"]
            except DomainError as exc:
                return exc.code

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: claim(), range(2)))
        self.assertEqual(sorted(results), ["ALREADY_RUNNING", "running"])

    def test_finish_only_accepts_run_workspace_files(self):
        job = self.store.create("finish")
        self._advance_to(job["job_id"], "style_approved")
        run = self.store.claim(job["job_id"], self.store.get(job["job_id"])["rev"], "style_frame")
        run_dir = self.root / "runs" / run["run_id"]
        output = run_dir / "frame.png"
        output.write_bytes(b"png")
        finished = self.store.finish(run["run_id"], "frame.png", None)
        self.assertEqual(finished["status"], "finished")
        self.assertEqual(finished["artifact"]["path"], f"runs/{run['run_id']}/frame.png")
        with self.assertRaisesRegex(DomainError, "INVALID_STATE"):
            self.store.finish(run["run_id"], "frame.png", None)

    def test_failed_run_does_not_publish_partial_file(self):
        job = self.store.create("partial")["job_id"]
        self._advance_to(job, "style_approved")
        run = self.store.claim(job, self.store.get(job)["rev"], "style_frame")["run_id"]
        (self.root / "runs" / run / "partial.png").write_bytes(b"partial")
        self.assertEqual(self.store.finish(run, "partial.png", "failed")["status"], "failed")
        self.assertEqual(self.store.get(job)["artifacts"], [])

    def test_old_run_does_not_overwrite_current_status(self):
        job = self.store.create("old")["job_id"]
        self._advance_to(job, "style_approved")
        run = self.store.claim(job, self.store.get(job)["rev"], "style_frame")["run_id"]
        self.store.revise(job, 1, {"title": "new"})
        self.store.finish(run, None, "old error")
        self.assertEqual(self.store.get(job)["status"], "draft")
        self.assertIsNone(self.store.get(job)["error"])

    def test_approval_requires_existing_artifact(self):
        job = self.store.create("approval")["job_id"]
        self._advance_to(job, "style_frame")
        rev = self.store.get(job)["rev"]
        style = self._artifact(job, rev, "style_frame", "style.png")
        self.store.approve(job, rev, "script_style", style["id"])
        (self.root / style["path"]).unlink()
        with self.assertRaisesRegex(DomainError, "APPROVAL_REQUIRED"):
            self.store.claim(job, rev, "preview")

    def test_missing_snapshot_is_rejected(self):
        job = self.store.create("snapshot")["job_id"]
        self._advance_to(job, "style_approved")
        rev = self.store.get(job)["rev"]
        run = self.store.claim(job, rev, "style_frame")
        (self.root / run["snapshot_path"]).unlink()
        with self.assertRaisesRegex(DomainError, "INVALID_SNAPSHOT"):
            self.store.get_run(run["run_id"])
        self.store.finish(run["run_id"], None, "snapshot missing")
        with self.assertRaisesRegex(DomainError, "INVALID_SNAPSHOT"):
            self.store.claim(job, rev, "style_frame")

    def test_invalid_revision_keeps_current_snapshot(self):
        job = self.store.create("payload")["job_id"]
        with self.assertRaisesRegex(DomainError, "INVALID_PAYLOAD"):
            self.store.revise(job, 1, {"cues": []})
        self.assertEqual(self.store.get(job)["rev"], 1)
        self.assertFalse((self.root / "jobs" / job / "revisions" / "2").exists())

    def _artifact(self, job_id, rev, kind, name):
        path = self.root / "jobs" / job_id / "revisions" / str(rev) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"artifact")
        return self.store.add_artifact(job_id, rev, kind, path)


if __name__ == "__main__":
    unittest.main()
