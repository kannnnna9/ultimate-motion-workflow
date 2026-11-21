import tempfile
import unittest
import sqlite3
import json
from pathlib import Path

from ump_core.jobs import STAGES, DomainError, JobStore


class StageMachineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = JobStore(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_stage_order_is_linear(self):
        self.assertEqual(STAGES[0], "intake")
        self.assertEqual(STAGES[-1], "final_ready")
        # style sits between research and the script, not after the script
        self.assertEqual(STAGES.index("style_approved"), STAGES.index("research_approved") + 1)
        self.assertEqual(STAGES.index("naskah_draft"), STAGES.index("style_approved") + 1)

    def test_new_job_starts_at_intake(self):
        job = self.store.create("topik")
        self.assertEqual(job["stage"], "intake")
        self.assertEqual(job["origin"], "generated")
        self.assertEqual(self.store.stage_of(job["job_id"]), "intake")

    def test_advance_moves_forward_and_records_history(self):
        job_id = self.store.create("topik")["job_id"]
        result = self.store.advance(job_id, 1, "set_brief", expect="intake", to="brief_draft")
        self.assertEqual(result["stage"], "brief_draft")
        history = self.store.get(job_id)["stage_history"]
        self.assertEqual(history[-1]["from_stage"], "intake")
        self.assertEqual(history[-1]["to_stage"], "brief_draft")
        self.assertEqual(history[-1]["action"], "set_brief")

    def test_repeated_advance_rejected(self):
        job_id = self.store.create("topik")["job_id"]
        self.store.advance(job_id, 1, "set_brief", expect="intake", to="brief_draft")
        with self.assertRaisesRegex(DomainError, "STAGE_ORDER"):
            self.store.advance(job_id, 1, "set_brief", expect="intake", to="brief_draft")
        self.assertEqual(self.store.stage_of(job_id), "brief_draft")

    def test_backward_advance_rejected(self):
        job_id = self.store.create("topik")["job_id"]
        with self.assertRaisesRegex(DomainError, "STAGE_ORDER"):
            self.store.advance(job_id, 1, "select_style", expect="naskah_approved", to="style_approved")
        self.assertEqual(self.store.stage_of(job_id), "intake")

    def test_legacy_database_is_migrated_idempotently(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            db = sqlite3.connect(root / "jobs.sqlite3")
            db.executescript("""
                CREATE TABLE jobs (job_id TEXT PRIMARY KEY, brief TEXT NOT NULL, rev INTEGER NOT NULL,
                                   status TEXT NOT NULL, error TEXT, payload TEXT NOT NULL);
            """)
            with_style = {"brief": "x", "style_id": "dark-editorial", "cues": {}}
            without_style = {"brief": "y", "cues": {}}
            db.execute("INSERT INTO jobs VALUES ('aaaaaaaaaaaaaaaa', 'x', 1, 'draft', NULL, ?)",
                       (json.dumps(with_style),))
            db.execute("INSERT INTO jobs VALUES ('bbbbbbbbbbbbbbbb', 'y', 1, 'draft', NULL, ?)",
                       (json.dumps(without_style),))
            db.commit()
            db.close()
            store = JobStore(root)
            self.assertEqual(store.stage_of("aaaaaaaaaaaaaaaa"), "style_approved")
            self.assertEqual(store.stage_of("bbbbbbbbbbbbbbbb"), "intake")
            JobStore(root)
            self.assertEqual(store.stage_of("aaaaaaaaaaaaaaaa"), "style_approved")


if __name__ == "__main__":
    unittest.main()
