import tempfile
import unittest
from pathlib import Path

from ump_core.jobs import DomainError, JobStore
from ump_core import tools


def _payload(action, **extra):
    return {"action": action, **extra}


class StageGateTests(unittest.TestCase):
    """Every (stage, action) pair must be explicitly allowed or refused."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = JobStore(Path(self.tmp.name))
        self._orig_store = tools._store
        tools._store = lambda: self.store

    def tearDown(self):
        tools._store = self._orig_store
        self.tmp.cleanup()

    def _call(self, action, **payload):
        return tools._job(self.store, action, dict(payload))

    def _job_at(self, stage):
        job_id = self.store.create("topik")["job_id"]
        chain = tools._STAGE_ROUTES
        for name, (_allowed, target) in chain.items():
            if self.store.stage_of(job_id) == stage:
                break
            if self.store.stage_of(job_id) in _allowed:
                self.store.advance(job_id, 1, name, expect=self.store.stage_of(job_id), to=target)
        return job_id, self.store.get(job_id)["rev"]

    def test_set_brief_allowed_only_at_intake(self):
        job_id = self.store.create("topik")["job_id"]
        self._call("set_brief", job_id=job_id, rev=1, patch={"brief": "topik baru"})
        self.assertEqual(self.store.stage_of(job_id), "brief_draft")
        with self.assertRaisesRegex(DomainError, "STAGE_ORDER"):
            self._call("set_brief", job_id=job_id, rev=self.store.get(job_id)["rev"], patch={"brief": "lagi"})

    def test_research_requires_brief_approved(self):
        job_id = self.store.create("topik")["job_id"]
        with self.assertRaisesRegex(DomainError, "STAGE_ORDER"):
            self._call("research", job_id=job_id, rev=1, sources=[{"id": "s1", "url": "https://example.com"}])
        self.store.advance(job_id, 1, "set_brief", expect="intake", to="brief_draft")
        self.store.advance(job_id, 1, "approve", expect="brief_draft", to="brief_approved")
        rev = self.store.get(job_id)["rev"]
        self._call("research", job_id=job_id, rev=rev, sources=[{"id": "s1", "url": "https://example.com"}])
        self.assertEqual(self.store.stage_of(job_id), "research_draft")

    def test_write_naskah_requires_research_approved(self):
        job_id = self.store.create("topik")["job_id"]
        with self.assertRaisesRegex(DomainError, "STAGE_ORDER"):
            self._call("write_naskah", job_id=job_id, rev=1, naskah={"beats": []}, cue={"cues": []})

    def test_select_style_requires_naskah_approved(self):
        job_id = self.store.create("topik")["job_id"]
        with self.assertRaisesRegex(DomainError, "STAGE_ORDER"):
            self._call("select_style", job_id=job_id, rev=1, style_id="dark-editorial")

    def test_unknown_action_is_invalid(self):
        job_id = self.store.create("topik")["job_id"]
        with self.assertRaisesRegex(DomainError, "INVALID_ACTION"):
            self._call("teleport", job_id=job_id, rev=1)

    def test_every_action_is_routed(self):
        self.assertEqual(
            set(tools._STAGE_ROUTES),
            {"set_brief", "research", "write_naskah", "import_naskah", "select_style"},
        )
        for name, (allowed, target) in tools._STAGE_ROUTES.items():
            self.assertTrue(allowed, name)
            self.assertIsInstance(target, str)
            self.assertNotEqual(target, "naskah_approved", name)

    def test_revise_of_episode_documents_blocked_before_research(self):
        job_id = self.store.create("topik")["job_id"]
        with self.assertRaisesRegex(DomainError, "EPISODE_REQUIRED"):
            self._call("revise", job_id=job_id, rev=1, patch={"naskah": {"beats": []}})
        self.store.advance(job_id, 1, "set_brief", expect="intake", to="brief_draft")
        with self.assertRaisesRegex(DomainError, "EPISODE_REQUIRED"):
            self._call("revise", job_id=job_id, rev=self.store.get(job_id)["rev"],
                       patch={"cue": {"cues": []}})

    def test_revising_naskah_after_approval_drops_stage(self):
        job_id = self.store.create("topik")["job_id"]
        self.store.advance(job_id, 1, "set_brief", expect="intake", to="brief_draft")
        self.store.advance(job_id, 1, "approve", expect="brief_draft", to="brief_approved")
        self.store.advance(job_id, 1, "research", expect="brief_approved", to="research_draft")
        self.store.advance(job_id, 1, "approve", expect="research_draft", to="research_approved")
        self.assertEqual(self.store.stage_of(job_id), "research_approved")

    def test_claim_requires_matching_stage(self):
        job_id = self.store.create("topik")["job_id"]
        for target, needed in (("style_frame", "naskah_approved"), ("preview", "style_frame"), ("final", "final_ready")):
            with self.assertRaisesRegex(DomainError, "STAGE_ORDER"):
                self.store.claim(job_id, 1, target)
        self.assertEqual(self.store.stage_of(job_id), "intake")

    def test_research_requires_at_least_one_source(self):
        job_id = self.store.create("topik")["job_id"]
        self.store.advance(job_id, 1, "set_brief", expect="intake", to="brief_draft")
        self.store.advance(job_id, 1, "approve", expect="brief_draft", to="brief_approved")
        rev = self.store.get(job_id)["rev"]
        with self.assertRaisesRegex(DomainError, "RESEARCH_REQUIRED"):
            self._call("research", job_id=job_id, rev=rev, sources=[])
        self._call("research", job_id=job_id, rev=rev,
                   sources=[{"id": "s1", "url": "https://example.com/a"}])
        self.assertEqual(self.store.stage_of(job_id), "research_draft")

    def test_import_naskah_lands_in_draft_with_imported_origin(self):
        job_id = self.store.create("topik")["job_id"]
        self.store.advance(job_id, 1, "set_brief", expect="intake", to="brief_draft")
        self.store.advance(job_id, 1, "approve", expect="brief_draft", to="brief_approved")
        self.store.advance(job_id, 1, "research", expect="brief_approved", to="research_draft")
        self.store.revise(job_id, 1, {"sources": [{"id": "s1", "url": "https://example.com/a"}]})
        rev = self.store.get(job_id)["rev"]
        self.store.advance(job_id, rev, "approve", expect="research_draft", to="research_approved")
        self.store.advance(job_id, rev, "select_style", expect="research_approved", to="style_approved")
        rev = self.store.get(job_id)["rev"]
        self._call("import_naskah", job_id=job_id, rev=rev,
                   naskah={"beats": [{"id": "b1", "fact_refs": ["s1"]}]},
                   cue={"cues": [{"id": "c001", "beat": "b1", "t_ms": 0}]})
        self.assertEqual(self.store.stage_of(job_id), "naskah_draft")
        self.assertEqual(self.store.get(job_id)["origin"], "imported")
        history = self.store.get(job_id)["stage_history"]
        self.assertEqual(history[-1]["note"], "imported")

    def test_import_cannot_reach_approved_stage_directly(self):
        job_id = self.store.create("topik")["job_id"]
        with self.assertRaisesRegex(DomainError, "INVALID_ACTION"):
            self._call("import_naskah_approved", job_id=job_id, rev=1, naskah={}, cue={})
        self.assertIn("naskah_draft", tools._STAGE_ROUTES["import_naskah"])

    def test_import_naskah_rejected_once_research_done_by_upstream_flow(self):
        # Import is only allowed before the upstream generated path has run.
        job_id = self.store.create("topik")["job_id"]
        self.store.advance(job_id, 1, "set_brief", expect="intake", to="brief_draft")
        self.store.advance(job_id, 1, "approve", expect="brief_draft", to="brief_approved")
        self.store.advance(job_id, 1, "research", expect="brief_approved", to="research_draft")
        self.store.advance(job_id, 1, "approve", expect="research_draft", to="research_approved")
        self.store.advance(job_id, 1, "select_style", expect="research_approved", to="style_approved")
        self.store.advance(job_id, 1, "write_naskah", expect="style_approved", to="naskah_draft")
        self.store.advance(job_id, 1, "approve", expect="naskah_draft", to="naskah_approved")
        rev = self.store.get(job_id)["rev"]
        with self.assertRaisesRegex(DomainError, "STAGE_ORDER"):
            self._call("import_naskah", job_id=job_id, rev=rev, naskah={"beats": []}, cue={"cues": []})

    def test_write_naskah_requires_fact_refs_pointing_at_known_sources(self):
        job_id = self.store.create("topik")["job_id"]
        self.store.advance(job_id, 1, "set_brief", expect="intake", to="brief_draft")
        self.store.advance(job_id, 1, "approve", expect="brief_draft", to="brief_approved")
        rev = self.store.get(job_id)["rev"]
        self._call("research", job_id=job_id, rev=rev,
                   sources=[{"id": "s1", "url": "https://example.com/a"}])
        self.store.advance(job_id, self.store.get(job_id)["rev"], "approve",
                           expect="research_draft", to="research_approved")
        # the style is approved before the script is written
        self.store.advance(job_id, self.store.get(job_id)["rev"], "select_style",
                           expect="research_approved", to="style_approved")
        rev = self.store.get(job_id)["rev"]
        bad = {"beats": [{"id": "b1", "on_screen_text": "x", "narration": "y", "fact_refs": ["nope"]}]}
        with self.assertRaisesRegex(DomainError, "FACT_REFS"):
            self._call("write_naskah", job_id=job_id, rev=rev, naskah=bad,
                       cue={"cues": [{"id": "c001", "beat": "b1", "t_ms": 0}]})

    def test_document_approval_requires_matching_artifact_kind(self):
        job_id = self.store.create("topik")["job_id"]
        self.store.advance(job_id, 1, "set_brief", expect="intake", to="brief_draft")
        rev = self.store.get(job_id)["rev"]
        wrong = self.store.add_artifact(job_id, rev, "preview", "brief.json", create_empty=True)
        with self.assertRaisesRegex(DomainError, "INVALID_APPROVAL"):
            self.store.approve(job_id, rev, "brief", wrong["id"], evidence={"message_id":"m8","chat_id":"c8"})
        right = self.store.add_artifact(job_id, rev, "brief", "brief2.json", create_empty=True)
        result = self.store.approve(job_id, rev, "brief", right["id"], evidence={"message_id":"m9","chat_id":"c9"})
        self.assertEqual(result["job_stage"], "brief_approved")

    def test_skill_documents_every_stage_route(self):
        skill = (Path(__file__).resolve().parents[1] / "skills" / "motion-pipeline" / "SKILL.md").read_text(encoding="utf-8")
        for action in tools._STAGE_ROUTES:
            self.assertIn(action, skill, f"skill does not mention {action}")
        for token in ("STAGE_ORDER", "intake", "brief_approved", "research_approved",
                      "naskah_approved", "style_approved"):
            self.assertIn(token, skill, f"skill does not mention {token}")


if __name__ == "__main__":
    unittest.main()
