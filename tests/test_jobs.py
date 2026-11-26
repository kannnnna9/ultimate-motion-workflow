import concurrent.futures
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ump_core.assets import AssetStore
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
            ("select_style", "research_approved", "style_approved"),
            ("write_naskah", "style_approved", "naskah_draft"),
            ("approve", "naskah_draft", "naskah_approved"),
            ("advance", "naskah_approved", "storyboard"),
        ]
        # naskah_approved is where the script is signed off; the pipeline then
        # waits at `storyboard` for the reference image, and frames render from
        # there. A test asking for naskah_approved therefore stops one step later.
        stop_at = "storyboard" if stage == "naskah_approved" else stage
        for action, expect, target in chain:
            if self.store.stage_of(job_id) == stop_at:
                return
            if self.store.stage_of(job_id) == expect:
                self.store.advance(job_id, self.store.get(job_id)["rev"], action, expect=expect, to=target)
        self.assertEqual(self.store.stage_of(job_id), stop_at)

    def _pass_storyboard(self, job_id):
        """Cross the storyboard stage: the pipeline requires it before frames."""
        if self.store.stage_of(job_id) == "naskah_approved":
            self.store.advance(job_id, self.store.get(job_id)["rev"], "advance",
                               expect="naskah_approved", to="storyboard")

    def _render_style_frame(self, job_id):
        rev = self.store.get(job_id)["rev"]
        run = self.store.claim(job_id, rev, "style_frame")
        output = self.root / "runs" / run["run_id"] / "style-frame.png"
        output.write_bytes(b"frame")
        return self.store.finish(run["run_id"], "style-frame.png", None)

    def test_naskah_revision_rolls_back_stage(self):
        job = self.store.create("revision rollback")
        self.store.advance(job["job_id"], 1, "set_brief", expect="intake", to="naskah_approved")
        revised = self.store.revise(job["job_id"], 1, {"naskah": {"beats": []}})
        self.assertEqual(revised["rev"], 2)
        self.assertEqual(revised["stage"], "naskah_draft")

    def test_approval_requires_expected_source_stage(self):
        job = self.store.create("approval source")
        self.store.advance(job["job_id"], 1, "set_brief", expect="intake", to="intake")
        artifact = self._artifact(job["job_id"], 1, "naskah", "naskah.json")
        with self.assertRaisesRegex(DomainError, "APPROVAL_STAGE"):
            self.store.approve(job["job_id"], 1, "naskah", artifact["id"], evidence={"message_id":"m7","chat_id":"c7"})

    def test_preview_requires_style_frame_approval_and_final_ready(self):
        job = self.store.create("approval sequence")
        self._advance_to(job["job_id"], "naskah_approved")
        self._render_style_frame(job["job_id"])
        rev = self.store.get(job["job_id"])["rev"]
        with self.assertRaisesRegex(DomainError, "APPROVAL_REQUIRED"):
            self.store.claim(job["job_id"], rev, "preview")
        style = self._artifact(job["job_id"], rev, "style_frame", "style.png")
        self.store.approve(job["job_id"], rev, "script_style", style["id"], evidence={"message_id": "m1", "chat_id": "c1"})
        preview_run = self.store.claim(job["job_id"], rev, "preview")
        (self.root / "runs" / preview_run["run_id"] / "preview.mp4").write_bytes(b"preview")
        preview_artifact = self.store.finish(preview_run["run_id"], "preview.mp4", None)["artifact"]
        self.store.approve(job["job_id"], rev, "preview", preview_artifact["id"], evidence={"message_id": "m3", "chat_id": "c3"})
        self.assertEqual(self.store.stage_of(job["job_id"]), "final_ready")
        self.store.claim(job["job_id"], rev, "final")

    def test_new_cue_document_drops_stale_overrides(self):
        """A replacement cue document wins over the older override map.

        Regression: `revise` with a new `cue` kept the previous `cues` override
        map, so `episode_docs` re-applied the old timestamps and the rendered
        cards drifted ~1.5 s behind the narration while the script looked fixed.
        """
        job = self.store.create("Resep cola")
        job_id = job["job_id"]
        self.store.advance(job_id, 1, "set_brief", expect="intake", to="brief_draft")
        rev = self.store.get(job_id)["rev"]
        self.store.revise(job_id, rev, {"cues": {"c001": {"t_ms": 4200}}})
        rev = self.store.get(job_id)["rev"]
        self.assertIn("cues", json.loads((self.root / "jobs" / job_id / "revisions" / str(rev) / "input.json").read_text()))

        self.store.revise(job_id, rev, {"cue": {"version": 1, "episode": "e", "fps": 30,
                                                "cues": [{"id": "c001", "beat": "b1", "t_ms": 2567}]}})
        latest = json.loads((self.root / "jobs" / job_id / "revisions" /
                             str(self.store.get(job_id)["rev"]) / "input.json").read_text())
        self.assertNotIn("cues", latest, "stale override map survived the new cue document")
        self.assertEqual(latest["cue"]["cues"][0]["t_ms"], 2567)

    def test_revision_requires_new_approval(self):
        job = self.store.create("Explain the five o'clock coffee ritual")
        job_id = job["job_id"]
        self._advance_to(job_id, "naskah_approved")
        self._render_style_frame(job_id)
        rev = self.store.get(job_id)["rev"]
        with self.assertRaisesRegex(DomainError, "APPROVAL_REQUIRED"):
            self.store.claim(job_id, rev, "preview")

        style = self._artifact(job_id, rev, "style_frame", "style.png")
        self.store.approve(job_id, rev, "script_style", style["id"], evidence={"message_id": "m2", "chat_id": "c2"})
        preview_run = self.store.claim(job_id, rev, "preview")
        (self.root / "runs" / preview_run["run_id"] / "preview.mp4").write_bytes(b"preview")
        preview = self.store.finish(preview_run["run_id"], "preview.mp4", None)["artifact"]
        self.store.approve(job_id, rev, "preview", preview["id"], evidence={"message_id": "m4", "chat_id": "c4"})
        self.assertEqual(self.store.stage_of(job_id), "final_ready")
        run = self.store.claim(job_id, rev, "final")
        self.store.finish(run["run_id"], None, "renderer failed")

        revised = self.store.revise(job_id, rev, {"cues": {"c007": {"t_ms": 9200}}})
        self.assertEqual(revised["rev"], rev + 1)
        self.assertEqual(self.store.get(job_id)["rev"], rev + 1)
        with self.assertRaisesRegex(DomainError, "STAGE_ORDER"):
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
        self._advance_to(job["job_id"], "naskah_approved")
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
        self._advance_to(job["job_id"], "naskah_approved")
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
        self._advance_to(job, "naskah_approved")
        run = self.store.claim(job, self.store.get(job)["rev"], "style_frame")["run_id"]
        (self.root / "runs" / run / "partial.png").write_bytes(b"partial")
        self.assertEqual(self.store.finish(run, "partial.png", "failed")["status"], "failed")
        self.assertEqual(self.store.get(job)["artifacts"], [])

    def test_old_run_does_not_overwrite_current_status(self):
        job = self.store.create("old")["job_id"]
        self._advance_to(job, "naskah_approved")
        run = self.store.claim(job, self.store.get(job)["rev"], "style_frame")["run_id"]
        self.store.revise(job, 1, {"title": "new"})
        self.store.finish(run, None, "old error")
        self.assertEqual(self.store.get(job)["status"], "draft")
        self.assertIsNone(self.store.get(job)["error"])

    def test_approval_requires_existing_artifact(self):
        job = self.store.create("approval")["job_id"]
        self._advance_to(job, "naskah_approved")
        self._render_style_frame(job)
        rev = self.store.get(job)["rev"]
        style = self._artifact(job, rev, "style_frame", "style.png")
        self.store.approve(job, rev, "script_style", style["id"], evidence={"message_id": "m5", "chat_id": "c5"})
        (self.root / style["path"]).unlink()
        with self.assertRaisesRegex(DomainError, "APPROVAL_REQUIRED"):
            self.store.claim(job, rev, "preview")

    def test_missing_snapshot_is_rejected(self):
        job = self.store.create("snapshot")["job_id"]
        self._advance_to(job, "naskah_approved")
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

    def test_create_persists_mode_and_requirements(self):
        with self.assertRaises(DomainError) as raised:
            self.store.create("bad mode", mode="creative")
        self.assertEqual(raised.exception.code, "INVALID_MODE")

        default_job = self.store.create("default mode")
        self.assertEqual(default_job["mode"], "collaborative")

        requirements = {"checkpoints": [], "hard": {"subtitle": False}}
        job = self.store.create("directed mode", mode="directed", requirements=requirements)
        self.assertEqual(job["mode"], "directed")
        self.assertEqual(job["requirements"], requirements)
        reopened = JobStore(self.root).get(job["job_id"])
        self.assertEqual(reopened["mode"], "directed")
        self.assertEqual(reopened["requirements"], requirements)

    def test_autonomous_composition_proceeds_without_operator_approval(self):
        job = self.store.create("autonomous render", mode="autonomous")
        custom = self.store.write_composition(job["job_id"], job["rev"], {
            "html": "<!doctype html><body>scene</body>",
            "width": 1080, "height": 1920, "fps": 30, "duration_frames": 30,
            "audio_enabled": False, "subtitles_enabled": False,
        })
        run = self.store.claim(custom["job_id"], custom["rev"], "preview")
        (self.root / "runs" / run["run_id"] / "preview.mp4").write_bytes(b"preview")
        self.store.finish(run["run_id"], "preview.mp4", None)
        self.assertEqual(self.store.stage_of(custom["job_id"]), "final_ready")
        final = self.store.claim(custom["job_id"], custom["rev"], "final")
        self.assertEqual(final["target"], "final")
        with self.store._db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM approvals WHERE job_id=?", (custom["job_id"],)).fetchone()[0], 0)

    def test_directed_defaults_to_preview_checkpoint(self):
        job = self.store.create("directed default checkpoint", mode="directed")
        custom = self.store.write_composition(job["job_id"], job["rev"], {
            "html": "<!doctype html><body>scene</body>",
            "width": 1080, "height": 1920, "fps": 30, "duration_frames": 30,
            "audio_enabled": False, "subtitles_enabled": False,
        })
        run = self.store.claim(custom["job_id"], custom["rev"], "preview")
        (self.root / "runs" / run["run_id"] / "preview.mp4").write_bytes(b"preview")
        self.store.finish(run["run_id"], "preview.mp4", None)
        self.assertEqual(self.store.stage_of(custom["job_id"]), "preview")
        with self.assertRaises(DomainError) as raised:
            self.store.claim(custom["job_id"], custom["rev"], "final")
        self.assertEqual(raised.exception.code, "APPROVAL_REQUIRED")

    def test_directed_empty_checkpoints_skip_preview_approval(self):
        job = self.store.create("directed without approvals", mode="directed", requirements={"checkpoints": []})
        custom = self.store.write_composition(job["job_id"], job["rev"], {
            "html": "<!doctype html><body>scene</body>",
            "width": 1080, "height": 1920, "fps": 30, "duration_frames": 30,
            "audio_enabled": False, "subtitles_enabled": False,
        })
        run = self.store.claim(custom["job_id"], custom["rev"], "preview")
        (self.root / "runs" / run["run_id"] / "preview.mp4").write_bytes(b"preview")
        self.store.finish(run["run_id"], "preview.mp4", None)
        self.assertEqual(self.store.stage_of(custom["job_id"]), "final_ready")
        final = self.store.claim(custom["job_id"], custom["rev"], "final")
        self.assertEqual(final["target"], "final")
        with self.store._db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM approvals WHERE job_id=?", (custom["job_id"],)).fetchone()[0], 0)

    def test_collaborative_composition_requires_preview_approval_before_final(self):
        job = self.store.create("collaborative render", mode="collaborative")
        custom = self.store.write_composition(job["job_id"], job["rev"], {
            "html": "<!doctype html><body>scene</body>",
            "width": 1080, "height": 1920, "fps": 30, "duration_frames": 30,
            "audio_enabled": False, "subtitles_enabled": False,
        })
        run = self.store.claim(custom["job_id"], custom["rev"], "preview")
        (self.root / "runs" / run["run_id"] / "preview.mp4").write_bytes(b"preview")
        preview = self.store.finish(run["run_id"], "preview.mp4", None)["artifact"]
        self.assertEqual(self.store.stage_of(custom["job_id"]), "preview")
        with self.assertRaises(DomainError) as raised:
            self.store.claim(custom["job_id"], custom["rev"], "final")
        self.assertEqual(raised.exception.code, "APPROVAL_REQUIRED")

        self.store.approve(custom["job_id"], custom["rev"], "preview", preview["id"],
                           evidence={"message_id": "operator-preview", "chat_id": "test-chat"})
        self.assertEqual(self.store.stage_of(custom["job_id"]), "final_ready")
        final = self.store.claim(custom["job_id"], custom["rev"], "final")
        self.assertEqual(final["target"], "final")

    def test_custom_composition_can_start_preview_directly(self):
        job = self.store.create("direct preview", mode="collaborative")
        custom = self.store.write_composition(job["job_id"], job["rev"], {
            "html": "<!doctype html><body>scene</body>",
            "width": 1080, "height": 1920, "fps": 30, "duration_frames": 30,
            "audio_enabled": False, "subtitles_enabled": False,
        })
        run = self.store.claim(custom["job_id"], custom["rev"], "preview")
        self.assertEqual((run["target"], run["rev"]), ("preview", custom["rev"]))

    def test_generic_revision_cannot_replace_composition_paths(self):
        job = self.store.create("path ownership", mode="autonomous")
        custom = self.store.write_composition(job["job_id"], job["rev"], {
            "html": "<!doctype html><body>safe</body>",
            "width": 1080, "height": 1920, "fps": 30, "duration_frames": 30,
            "audio_enabled": False, "subtitles_enabled": False,
        })
        with self.assertRaises(DomainError) as raised:
            self.store.revise(custom["job_id"], custom["rev"], {
                "composition": {"html_path": "../../etc/passwd"},
            })
        self.assertEqual(raised.exception.code, "USE_WRITE_COMPOSITION")
        self.assertEqual(self.store.get(job["job_id"])["rev"], custom["rev"])

    def test_metadata_revision_preserves_composition_in_new_revision(self):
        composition = {
            "html": "<!doctype html><body>owned source</body>",
            "width": 1080, "height": 1920, "fps": 30, "duration_frames": 30,
            "audio_enabled": False, "subtitles_enabled": False,
        }
        job = self.store.create("metadata revision", mode="autonomous")
        custom = self.store.write_composition(job["job_id"], job["rev"], composition)
        revised = self.store.revise(custom["job_id"], custom["rev"], {"brief": "updated brief"})
        self.assertEqual((revised["rev"], revised["stage"]), (3, "composition_draft"))
        revision_dir = self.root / "jobs" / job["job_id"] / "revisions" / "3"
        self.assertEqual((revision_dir / "composition" / "index.html").read_text(encoding="utf-8"), composition["html"])
        self.assertEqual(json.loads((revision_dir / "project.json").read_text(encoding="utf-8")), {
            key: value for key, value in composition.items() if key != "html"
        })

    def test_metadata_revision_rejects_tampered_composition_html(self):
        job = self.store.create("tampered HTML revision", mode="autonomous")
        created = self.store.write_composition(job["job_id"], job["rev"], {
            "html": "<!doctype html><body>original</body>",
            "width": 1080, "height": 1920, "fps": 30, "duration_frames": 30,
            "audio_enabled": False, "subtitles_enabled": False,
        })
        old_dir = self.root / "jobs" / job["job_id"] / "revisions" / str(created["rev"])
        (old_dir / "composition" / "index.html").write_text("<!doctype html><body>tampered</body>", encoding="utf-8")
        with self.assertRaises(DomainError) as raised:
            self.store.revise(created["job_id"], created["rev"], {"brief": "must fail closed"})
        self.assertEqual(raised.exception.code, "INVALID_SNAPSHOT")
        self.assertEqual(self.store.get(job["job_id"])["rev"], created["rev"])

    def test_metadata_revision_rejects_project_bytes_changed_without_metadata_change(self):
        job = self.store.create("tampered project revision", mode="autonomous")
        created = self.store.write_composition(job["job_id"], job["rev"], {
            "html": "<!doctype html><body>original</body>",
            "width": 1080, "height": 1920, "fps": 30, "duration_frames": 30,
            "audio_enabled": False, "subtitles_enabled": False,
        })
        project = self.root / "jobs" / job["job_id"] / "revisions" / str(created["rev"]) / "project.json"
        project.write_bytes(project.read_bytes() + b" ")
        with self.assertRaises(DomainError) as raised:
            self.store.revise(created["job_id"], created["rev"], {"brief": "must fail closed"})
        self.assertEqual(raised.exception.code, "INVALID_SNAPSHOT")
        self.assertEqual(self.store.get(job["job_id"])["rev"], created["rev"])

    def test_metadata_revision_rejects_oversized_project_json_before_read(self):
        from ump_core.composition import MAX_PROJECT_BYTES

        job = self.store.create("oversized project revision", mode="autonomous")
        created = self.store.write_composition(job["job_id"], job["rev"], {
            "html": "<!doctype html><body>original</body>",
            "width": 1080, "height": 1920, "fps": 30, "duration_frames": 30,
            "audio_enabled": False, "subtitles_enabled": False,
        })
        project = self.root / "jobs" / job["job_id"] / "revisions" / str(created["rev"]) / "project.json"
        project.write_bytes(b"x" * (MAX_PROJECT_BYTES + 1))
        with patch.object(Path, "read_bytes", side_effect=AssertionError("oversized project must not be read")):
            with self.assertRaises(DomainError) as raised:
                self.store.revise(created["job_id"], created["rev"], {"brief": "must fail closed"})
        self.assertEqual(raised.exception.code, "INVALID_SNAPSHOT")
        self.assertEqual(self.store.get(job["job_id"])["rev"], created["rev"])

    def test_metadata_revision_rejects_oversized_composition_html_before_copy(self):
        from ump_core.composition import MAX_COMPOSITION_BYTES

        job = self.store.create("oversized HTML revision", mode="autonomous")
        created = self.store.write_composition(job["job_id"], job["rev"], {
            "html": "<!doctype html><body>original</body>",
            "width": 1080, "height": 1920, "fps": 30, "duration_frames": 30,
            "audio_enabled": False, "subtitles_enabled": False,
        })
        old_dir = self.root / "jobs" / job["job_id"] / "revisions" / str(created["rev"])
        (old_dir / "composition" / "index.html").write_bytes(b"x" * (MAX_COMPOSITION_BYTES + 1))
        with self.assertRaises(DomainError) as raised:
            self.store.revise(created["job_id"], created["rev"], {"brief": "must fail closed"})
        self.assertEqual(raised.exception.code, "INVALID_SNAPSHOT")
        self.assertEqual(self.store.get(job["job_id"])["rev"], created["rev"])

    def test_write_composition_accepts_catalog_license_limit_in_pinned_manifest(self):
        license_text = "L" * 129
        svg = '<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>'
        asset = AssetStore(self.root).add_svg(
            "long-license", svg, source="test", provenance="test", license=license_text,
        )
        job = self.store.create("long license asset", mode="autonomous")
        created = self.store.write_composition(job["job_id"], job["rev"], {
            "html": "<!doctype html><body>scene</body>",
            "width": 1080, "height": 1920, "fps": 30, "duration_frames": 30,
            "audio_enabled": False, "subtitles_enabled": False,
            "assets": [{"id": "long-license", "version": asset["version"]}],
        })
        revision = self.root / "jobs" / job["job_id"] / "revisions" / str(created["rev"])
        manifest = json.loads((revision / "project.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["assets"][0]["license"], license_text)

    def test_metadata_revision_preserves_pinned_asset_bytes_and_manifest(self):
        asset_store = AssetStore(self.root)
        svg = '<svg xmlns="http://www.w3.org/2000/svg" width="8" height="6" viewBox="0 0 8 6"><rect width="8" height="6" fill="#345678"/></svg>'
        record = asset_store.add_svg(
            "leaf", svg, source="test", provenance="original", license="MIT",
        )
        job = self.store.create("revise pinned asset", mode="autonomous")
        created = self.store.write_composition(job["job_id"], job["rev"], {
            "html": '<!doctype html><img src="assets/leaf-v1.svg">',
            "width": 1080, "height": 1920, "fps": 30, "duration_frames": 30,
            "audio_enabled": False, "subtitles_enabled": False,
            "assets": [{"id": "leaf", "version": 1}],
        })
        revised = self.store.revise(created["job_id"], created["rev"], {"brief": "metadata-only change"})
        old_dir = self.root / "jobs" / job["job_id"] / "revisions" / str(created["rev"])
        new_dir = self.root / "jobs" / job["job_id"] / "revisions" / str(revised["rev"])
        old_project = json.loads((old_dir / "project.json").read_text(encoding="utf-8"))
        pinned = old_project["assets"][0]
        self.assertEqual((new_dir / "project.json").read_bytes(), (old_dir / "project.json").read_bytes())
        self.assertEqual((new_dir / "composition" / pinned["path"]).read_bytes(), svg.encode("utf-8"))
        self.assertEqual(pinned["sha256"], record["sha256"])

    def test_metadata_revision_rejects_symlinked_pinned_asset(self):
        svg = '<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>'
        AssetStore(self.root).add_svg("leaf", svg, source="test", provenance="test", license="MIT")
        job = self.store.create("tampered pinned asset", mode="autonomous")
        created = self.store.write_composition(job["job_id"], job["rev"], {
            "html": "<!doctype html><body>scene</body>",
            "width": 1080, "height": 1920, "fps": 30, "duration_frames": 30,
            "audio_enabled": False, "subtitles_enabled": False,
            "assets": [{"id": "leaf", "version": 1}],
        })
        revision = self.root / "jobs" / job["job_id"] / "revisions" / str(created["rev"])
        pinned = json.loads((revision / "project.json").read_text(encoding="utf-8"))["assets"][0]
        asset_file = revision / "composition" / pinned["path"]
        outside = self.root / "outside.svg"
        outside.write_bytes(svg.encode("utf-8"))
        asset_file.unlink()
        asset_file.symlink_to(outside)
        with self.assertRaises(DomainError) as raised:
            self.store.revise(created["job_id"], created["rev"], {"brief": "must fail closed"})
        self.assertEqual(raised.exception.code, "ASSET_INTEGRITY")
        self.assertEqual(self.store.get(job["job_id"])["rev"], created["rev"])

    def test_custom_route_rejects_unsupported_directed_checkpoints(self):
        job = self.store.create("unsupported checkpoint", mode="directed",
                                requirements={"checkpoints": ["creative_direction", "preview"]})
        with self.assertRaises(DomainError) as raised:
            self.store.write_composition(job["job_id"], job["rev"], {
                "html": "<!doctype html><body>scene</body>",
                "width": 1080, "height": 1920, "fps": 30, "duration_frames": 30,
                "audio_enabled": False, "subtitles_enabled": False,
            })
        self.assertEqual(raised.exception.code, "UNSUPPORTED_CHECKPOINT")
        self.assertEqual(self.store.get(job["job_id"])["rev"], 1)
        self.assertFalse((self.root / "jobs" / job["job_id"] / "revisions" / "2").exists())

    def test_editing_composition_invalidates_old_preview_and_final_artifacts(self):
        job = self.store.create("invalidate prior outputs", mode="collaborative")
        composition = {
            "html": "<!doctype html><body>first</body>",
            "width": 1080, "height": 1920, "fps": 30, "duration_frames": 30,
            "audio_enabled": False, "subtitles_enabled": False,
        }
        custom = self.store.write_composition(job["job_id"], job["rev"], composition)
        preview_run = self.store.claim(custom["job_id"], custom["rev"], "preview")
        (self.root / "runs" / preview_run["run_id"] / "preview.mp4").write_bytes(b"preview-v1")
        preview = self.store.finish(preview_run["run_id"], "preview.mp4", None)["artifact"]
        self.store.approve(custom["job_id"], custom["rev"], "preview", preview["id"],
                           evidence={"message_id": "approval-v1", "chat_id": "test-chat"})
        final_run = self.store.claim(custom["job_id"], custom["rev"], "final")
        (self.root / "runs" / final_run["run_id"] / "final.mp4").write_bytes(b"final-v1")
        final = self.store.finish(final_run["run_id"], "final.mp4", None)["artifact"]

        revised = self.store.write_composition(custom["job_id"], custom["rev"], {
            **composition, "html": "<!doctype html><body>second</body>",
        })
        self.assertEqual((revised["rev"], revised["stage"]), (3, "composition_draft"))
        old_outputs = {item["id"]: item for item in revised["artifacts"]}
        self.assertEqual(old_outputs[preview["id"]]["rev"], 2)
        self.assertEqual(old_outputs[final["id"]]["rev"], 2)
        with self.assertRaises(DomainError) as raised:
            self.store.claim(revised["job_id"], revised["rev"], "final")
        self.assertEqual(raised.exception.code, "STAGE_ORDER")
        with self.assertRaises(DomainError) as raised:
            self.store.claim(revised["job_id"], custom["rev"], "final")
        self.assertEqual(raised.exception.code, "STALE_REVISION")

    def test_write_composition_pins_exact_asset_bytes_and_full_manifest(self):
        asset_store = AssetStore(self.root)
        svg = '<svg xmlns="http://www.w3.org/2000/svg" width="8" height="6" viewBox="0 0 8 6"><rect width="8" height="6" fill="#345678"/></svg>'
        first_asset = asset_store.add_svg(
            "leaf", svg, source="hermes-procedural", provenance="first geometry", license="MIT",
        )
        composition = {
            "html": '<!doctype html><img src="assets/leaf-v1.svg">',
            "width": 1080, "height": 1920, "fps": 30, "duration_frames": 30,
            "audio_enabled": False, "subtitles_enabled": False,
            "assets": [{"id": "leaf", "version": 1}],
        }
        job = self.store.create("asset-pinned composition", mode="autonomous")
        created = self.store.write_composition(job["job_id"], job["rev"], composition)
        revision = self.root / "jobs" / job["job_id"] / "revisions" / str(created["rev"])
        project = json.loads((revision / "project.json").read_text(encoding="utf-8"))
        pinned = project["assets"][0]
        self.assertEqual(pinned["id"], "leaf")
        self.assertEqual(pinned["version"], 1)
        self.assertEqual(pinned["sha256"], first_asset["sha256"])
        self.assertEqual(pinned["source"], "hermes-procedural")
        self.assertEqual(pinned["provenance"], "first geometry")
        pinned_file = revision / "composition" / pinned["path"]
        self.assertEqual(pinned_file.read_bytes(), svg.encode("utf-8"))
        self.assertEqual(hashlib.sha256(pinned_file.read_bytes()).hexdigest(), pinned["sha256"])
        snapshot = json.loads((revision / "input.json").read_text(encoding="utf-8"))
        self.assertEqual(snapshot["composition"]["metadata"], project)

        changed_svg = svg.replace("#345678", "#abcdef")
        second_asset = asset_store.add_svg(
            "leaf", changed_svg, source="hermes-procedural", provenance="revised geometry", license="MIT",
        )
        self.assertEqual(second_asset["version"], 2)
        self.assertEqual(pinned_file.read_bytes(), svg.encode("utf-8"))

    def test_write_composition_rejects_unknown_asset_version(self):
        AssetStore(self.root).add_svg(
            "leaf", '<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>',
            source="test", provenance="test", license="MIT",
        )
        job = self.store.create("unknown asset version", mode="autonomous")
        with self.assertRaises(DomainError) as raised:
            self.store.write_composition(job["job_id"], job["rev"], {
                "html": "<!doctype html><body>scene</body>",
                "width": 1080, "height": 1920, "fps": 30, "duration_frames": 30,
                "audio_enabled": False, "subtitles_enabled": False,
                "assets": [{"id": "leaf", "version": 2}],
            })
        self.assertEqual(raised.exception.code, "ASSET_NOT_FOUND")
        self.assertEqual(self.store.get(job["job_id"])["rev"], 1)

    def test_write_composition_rejects_asset_bundle_over_limit_before_revision(self):
        svg = '<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>'
        record = AssetStore(self.root).add_svg("leaf", svg, source="test", provenance="test", license="MIT")
        job = self.store.create("oversized asset bundle", mode="autonomous")
        with patch("ump_core.composition.MAX_PINNED_ASSET_BYTES", record["size_bytes"] - 1):
            with self.assertRaises(DomainError) as raised:
                self.store.write_composition(job["job_id"], job["rev"], {
                    "html": "<!doctype html><body>scene</body>",
                    "width": 1080, "height": 1920, "fps": 30, "duration_frames": 30,
                    "audio_enabled": False, "subtitles_enabled": False,
                    "assets": [{"id": "leaf", "version": 1}],
                })
        self.assertEqual(raised.exception.code, "ASSET_BUNDLE_TOO_LARGE")
        self.assertEqual(self.store.get(job["job_id"])["rev"], 1)
        self.assertFalse((self.root / "jobs" / job["job_id"] / "revisions" / "2").exists())

    def test_write_composition_creates_immutable_revision_snapshot(self):
        composition = {
            "html": "<!doctype html><title>one</title>",
            "width": 1080,
            "height": 1920,
            "fps": 30,
            "duration_frames": 30,
            "audio_enabled": False,
            "subtitles_enabled": False,
        }
        job = self.store.create("custom composition", mode="autonomous")
        first = self.store.write_composition(job["job_id"], job["rev"], composition)
        self.assertEqual((first["rev"], first["stage"]), (2, "composition_draft"))

        first_dir = self.root / "jobs" / job["job_id"] / "revisions" / "2"
        first_html = first_dir / "composition" / "index.html"
        project = {key: value for key, value in composition.items() if key != "html"}
        self.assertEqual(first_html.read_text(encoding="utf-8"), composition["html"])
        self.assertEqual(json.loads((first_dir / "project.json").read_text(encoding="utf-8")), project)
        snapshot = json.loads((first_dir / "input.json").read_text(encoding="utf-8"))
        self.assertEqual(snapshot["mode"], "autonomous")
        self.assertEqual(snapshot["composition"]["html_path"], "composition/index.html")
        self.assertEqual(snapshot["composition"]["metadata"], project)

        updated = {**composition, "html": "<!doctype html><title>two</title>"}
        second = self.store.write_composition(job["job_id"], first["rev"], updated)
        self.assertEqual((second["rev"], second["stage"]), (3, "composition_draft"))
        self.assertEqual(first_html.read_text(encoding="utf-8"), composition["html"])
        current_html = self.root / "jobs" / job["job_id"] / "revisions" / "3" / "composition" / "index.html"
        self.assertEqual(current_html.read_text(encoding="utf-8"), updated["html"])

    def _artifact(self, job_id, rev, kind, name):
        path = self.root / "jobs" / job_id / "revisions" / str(rev) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"artifact")
        return self.store.add_artifact(job_id, rev, kind, path)


if __name__ == "__main__":
    unittest.main()
