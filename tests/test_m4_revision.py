import copy
import hashlib
import importlib
import io
import json
import sqlite3
import sys
import tempfile
import types
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ump_core import tools
from ump_core.assets import AssetStore
from ump_core.composition import validate_composition
from ump_core.jobs import DomainError, JobStore
from ump_core.subtitles import MOBILE_ASS_STYLE
from ump_core.worker import _write_custom_subtitle


JOB_ID = "AbCdEfGhIjKlMnOp"
VOICE_SHA_A = "a" * 64
VOICE_SHA_B = "b" * 64


def _revision_module():
    try:
        return importlib.import_module("ump_core.revision")
    except ModuleNotFoundError as exc:
        if exc.name == "ump_core.revision":
            return None
        raise


def _silent_composition(html="<!doctype html><body>scene</body>"):
    return {
        "html": html,
        "width": 1080,
        "height": 1920,
        "fps": 30,
        "duration_frames": 30,
        "audio_enabled": False,
        "subtitles_enabled": False,
    }


def _fixture_voice_bytes():
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\0\0" * 4000)
    return output.getvalue()


def _custom_voice_job(store, *, font_size=56):
    job = store.create("pinned custom composition", mode="autonomous")
    job_id = job["job_id"]
    audio_bytes = _fixture_voice_bytes()
    blob_ref = "ABCDEFGHIJKLMNOP"
    audio_record = {
        "track_id": "voice", "blob_ref": blob_ref, "kind": "voice",
        "sha256": hashlib.sha256(audio_bytes).hexdigest(), "size_bytes": len(audio_bytes),
        "extension": ".wav", "source_format": "wav", "media_type": "audio/wav",
        "codec": "pcm_s16le", "duration_ms": 250, "sample_rate": 16000, "channels": 1,
        "provenance": "test fixture", "rights_status": "unverified",
    }
    audio_root = store._job_dir(job_id) / "audio_blobs"
    audio_root.mkdir()
    blob_path = audio_root / f"{blob_ref}.wav"
    blob_path.write_bytes(audio_bytes)
    blob_path.chmod(0o600)
    prepared = store.revise(job_id, 1, {
        "audio_library": [audio_record], "naskah": {"text": "fixture narration"},
    })
    store.set_stage(job_id, prepared["rev"], "naskah_approved")

    asset = AssetStore(store.root).add_svg(
        "wing", '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><path d="M1 1h8v8H1z"/></svg>',
        source="test", provenance="fixture asset", license="MIT",
    )
    composition = {
        "html": "<!doctype html><body><img src=\"assets/wing-v1.svg\">scene</body>",
        "width": 1080, "height": 1920, "fps": 30, "duration_frames": 30,
        "audio_enabled": True, "subtitles_enabled": True, "subtitle_mode": "ass",
        "ass_style": {**MOBILE_ASS_STYLE, "font_size": font_size},
        "audio_tracks": [{
            "track_id": "voice", "blob_ref": blob_ref, "kind": "voice", "offset_ms": 0,
            "trim_start_ms": 0, "trim_end_ms": 250, "gain_db": 0.0,
            "fade_in_ms": 0, "fade_out_ms": 0,
        }],
        "assets": [{"id": "wing", "version": asset["version"]}],
    }
    custom = store.write_composition(job_id, prepared["rev"], composition)
    return job_id, custom["rev"], composition, audio_bytes, asset


def _preview_job(store):
    job = store.create("revision approval", mode="collaborative")
    custom = store.write_composition(job["job_id"], job["rev"], _silent_composition())
    run = store.claim(job["job_id"], custom["rev"], "preview")
    output = store.root / "runs" / run["run_id"] / "preview.mp4"
    output.write_bytes(b"approved-preview-v1")
    artifact = store.finish(run["run_id"], "preview.mp4", None)["artifact"]
    store.approve(
        job["job_id"], custom["rev"], "preview", artifact["id"],
        evidence={"message_id": "approval-message", "chat_id": "test-chat"},
    )
    return job["job_id"], custom["rev"], artifact


class RevisionImpactTests(unittest.TestCase):
    def test_ass_font_change_invalidates_subtitle_outputs_but_preserves_alignment(self):
        revision = _revision_module()
        self.assertIsNotNone(revision, "ump_core.revision.impact is not implemented")
        before = {
            "subtitle_mode": "ass",
            "ass_style": {**MOBILE_ASS_STYLE, "font_size": 56},
            "audio_tracks": [{"track_id": "voice", "kind": "voice", "sha256": VOICE_SHA_A}],
            "speech_anchors": [{"id": "hit", "word_id": "w00001", "action_id": "blink", "offset_ms": 0}],
        }
        after = copy.deepcopy(before)
        after["ass_style"]["font_size"] = 60

        decision = revision.impact(before, after)

        self.assertIn("ass_style.font_size", decision["changed"])
        self.assertNotIn("alignment", decision["invalidate"])
        self.assertTrue({"sidecar", "mux", "subtitle_qa"} <= set(decision["invalidate"]))
        self.assertTrue({"voice_bytes", "alignment", "anchor"} <= set(decision["preserve"]))

    def test_new_voice_bytes_invalidate_timing_caption_anchor_mux_and_review(self):
        revision = _revision_module()
        self.assertIsNotNone(revision, "ump_core.revision.impact is not implemented")
        before = {
            "subtitle_mode": "ass",
            "ass_style": copy.deepcopy(MOBILE_ASS_STYLE),
            "audio_tracks": [{"track_id": "voice", "kind": "voice", "sha256": VOICE_SHA_A}],
            "speech_anchors": [{"id": "hit", "word_id": "w00001", "action_id": "blink", "offset_ms": 0}],
        }
        after = copy.deepcopy(before)
        after["audio_tracks"][0]["sha256"] = VOICE_SHA_B

        decision = revision.impact(before, after)

        self.assertTrue(
            {"alignment", "anchor", "caption", "sidecar", "mux", "review"}
            <= set(decision["invalidate"])
        )
        self.assertNotIn("alignment", decision["preserve"])

    def test_unknown_input_change_invalidates_conservatively(self):
        revision = _revision_module()
        self.assertIsNotNone(revision, "ump_core.revision.impact is not implemented")

        decision = revision.impact({"future_dependency": 1}, {"future_dependency": 2})

        self.assertIn("future_dependency", decision["changed"])
        self.assertTrue({"alignment", "anchor", "caption", "sidecar", "mux", "review"} <= set(decision["invalidate"]))
        self.assertEqual(decision["preserve"], [])

    def test_nested_custom_style_ignores_derived_project_digest(self):
        revision = _revision_module()
        self.assertIsNotNone(revision, "ump_core.revision.impact is not implemented")
        before = {
            "composition": {
                "html_path": "composition/index.html", "html_sha256": "a" * 64,
                "project_path": "project.json", "project_sha256": "b" * 64,
                "metadata": {
                    "subtitle_mode": "ass",
                    "ass_style": {**MOBILE_ASS_STYLE, "font_size": 56},
                    "audio_enabled": True,
                    "audio_tracks": [{"kind": "voice", "sha256": "c" * 64}],
                },
            },
        }
        after = copy.deepcopy(before)
        after["composition"]["metadata"]["ass_style"]["font_size"] = 60
        after["composition"]["project_sha256"] = "d" * 64

        decision = revision.impact(before, after)

        self.assertEqual(decision["changed"], ["composition.metadata.ass_style.font_size"])
        self.assertTrue({"sidecar", "mux", "subtitle_qa"} <= set(decision["invalidate"]))
        self.assertNotIn("alignment", decision["invalidate"])
        self.assertTrue({"voice_bytes", "alignment", "anchor"} <= set(decision["preserve"]))

    def test_stored_custom_style_edit_preserves_voice_alignment_and_anchor(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp))
            job_id, rev, composition, _audio_bytes, _asset = _custom_voice_job(store)
            changed = copy.deepcopy(composition)
            changed["ass_style"]["font_size"] = 60

            updated = store.write_composition(job_id, rev, changed)
            decision = store.get_revision_impact(job_id, updated["rev"])

        self.assertIn("alignment", decision["preserve"])
        self.assertIn("anchor", decision["preserve"])
        self.assertIn("voice_bytes", decision["preserve"])
        self.assertNotIn("alignment", decision["invalidate"])
        self.assertTrue({"sidecar", "mux", "subtitle_qa"} <= set(decision["invalidate"]))


class RevisionLockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = JobStore(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_locked_revise_fails_before_revision_or_artifact_mutation(self):
        job = self.store.create("protected brief")
        lock_scope = getattr(self.store, "lock_scope", None)
        self.assertTrue(callable(lock_scope), "JobStore.lock_scope is not implemented")
        lock_scope(job["job_id"], job["rev"], "brief")

        with self.assertRaisesRegex(DomainError, "LOCK_CONFLICT"):
            self.store.revise(job["job_id"], job["rev"], {"brief": "changed"})

        self.assertEqual(self.store.get(job["job_id"])["rev"], job["rev"])
        self.assertFalse((self.root / "jobs" / job["job_id"] / "revisions" / "2").exists())
        self.assertEqual(self.store.get(job["job_id"])["artifacts"], [])

    def test_locked_write_composition_fails_before_revision_or_artifact_mutation(self):
        job = self.store.create("protected composition", mode="autonomous")
        custom = self.store.write_composition(job["job_id"], job["rev"], _silent_composition())
        lock_scope = getattr(self.store, "lock_scope", None)
        self.assertTrue(callable(lock_scope), "JobStore.lock_scope is not implemented")
        lock_scope(job["job_id"], custom["rev"], "composition")

        with self.assertRaisesRegex(DomainError, "LOCK_CONFLICT"):
            self.store.write_composition(
                job["job_id"], custom["rev"], _silent_composition("<!doctype html><body>changed</body>")
            )

        self.assertEqual(self.store.get(job["job_id"])["rev"], custom["rev"])
        self.assertFalse((self.root / "jobs" / job["job_id"] / "revisions" / "3").exists())
        self.assertEqual(self.store.get(job["job_id"])["artifacts"], [])

    def test_unlock_with_wrong_scope_is_rejected_and_lock_remains(self):
        job = self.store.create("protected brief")
        lock_scope = getattr(self.store, "lock_scope", None)
        unlock_scope = getattr(self.store, "unlock_scope", None)
        self.assertTrue(callable(lock_scope) and callable(unlock_scope), "JobStore lock APIs are not implemented")
        lock_scope(job["job_id"], job["rev"], "brief")
        evidence = {"job_id": job["job_id"], "rev": job["rev"], "scope": "voice",
                    "message_id": "unlock-message", "chat_id": "test-chat"}

        with self.assertRaises(DomainError):
            unlock_scope(job["job_id"], job["rev"], "voice", evidence=evidence)

        with self.store._db() as db:
            locks = db.execute("SELECT scope, lock_rev FROM revision_locks WHERE job_id=?", (job["job_id"],)).fetchall()
        self.assertEqual([(row["scope"], row["lock_rev"]) for row in locks], [("brief", job["rev"])])

    def test_active_lock_survives_a_revision_and_unlock_is_bound_to_original_scope(self):
        job = self.store.create("protected brief")
        lock_scope = getattr(self.store, "lock_scope", None)
        unlock_scope = getattr(self.store, "unlock_scope", None)
        self.assertTrue(callable(lock_scope) and callable(unlock_scope), "JobStore lock APIs are not implemented")
        lock_scope(job["job_id"], job["rev"], "brief")
        revised = self.store.revise(job["job_id"], job["rev"], {"title": "unrelated"})
        self.assertEqual(revised["rev"], job["rev"] + 1)

        with self.assertRaisesRegex(DomainError, "LOCK_CONFLICT"):
            self.store.revise(job["job_id"], revised["rev"], {"brief": "changed"})

        wrong_evidence = {"job_id": job["job_id"], "rev": revised["rev"], "scope": "brief",
                          "message_id": "wrong-rev", "chat_id": "test-chat"}
        with self.assertRaises(DomainError):
            unlock_scope(job["job_id"], revised["rev"], "brief", evidence=wrong_evidence)
        correct_evidence = {"job_id": job["job_id"], "rev": job["rev"], "scope": "brief",
                            "message_id": "right-rev", "chat_id": "test-chat"}
        unlock_scope(job["job_id"], job["rev"], "brief", evidence=correct_evidence)
        updated = self.store.revise(job["job_id"], revised["rev"], {"brief": "changed"})
        self.assertEqual(updated["rev"], revised["rev"] + 1)


class RevisionDispatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = JobStore(self.root / "data")
        self._old_events = dict(tools._EVENTS)
        with tools._EVENT_LOCK:
            tools._EVENTS.clear()

    def tearDown(self):
        with tools._EVENT_LOCK:
            tools._EVENTS.clear()
            tools._EVENTS.update(self._old_events)
        self.tmp.cleanup()

    def test_lock_unlock_and_rollback_actions_are_routed_through_dispatch(self):
        job = self.store.create("dispatch original")
        job_id = job["job_id"]
        with patch.object(tools, "_store", return_value=self.store):
            locked = json.loads(tools.dispatch("ump_job", {
                "action": "lock", "payload": {"job_id": job_id, "rev": 1, "scope": "brief"},
            }))
        self.assertTrue(locked["ok"], locked)

        event_module = types.ModuleType("gateway.platforms.event")
        event_module.MessageType = SimpleNamespace(TEXT="text")
        modules = {
            "gateway": types.ModuleType("gateway"),
            "gateway.platforms": types.ModuleType("gateway.platforms"),
            "gateway.platforms.event": event_module,
        }
        modules["gateway"].__path__ = []
        modules["gateway.platforms"].__path__ = []
        source = SimpleNamespace(
            platform=SimpleNamespace(value="telegram"), chat_type="dm",
            user_id="42", chat_id="42", is_bot=False,
        )
        profile = self.root / "profile"
        profile.mkdir()
        gateway = SimpleNamespace(
            _is_user_authorized_for_source=lambda _source, allow_adapter_delegation=False: True,
        )
        message_id = "unlock-message"
        message = SimpleNamespace(
            source=source, raw_message=SimpleNamespace(), message_id=message_id,
            text=f"UMP UNLOCK {job_id} 1 brief", message_type="text", media_urls=[], media_types=[],
        )
        event_key = (self.root / "profile", "42", "42", message_id)
        with (
            patch.dict(sys.modules, modules),
            patch.object(tools, "_home", return_value=self.root / "profile"),
            patch.object(tools, "_event_key", return_value=event_key),
            patch.object(tools, "_store", return_value=self.store),
        ):
            tools.inbound(message, gateway)
            unlocked = json.loads(tools.dispatch("ump_job", {
                "action": "unlock", "payload": {"job_id": job_id, "rev": 1, "scope": "brief"},
            }))
        self.assertTrue(unlocked["ok"], unlocked)

        revised = self.store.revise(job_id, 1, {"brief": "dispatch revised"})
        self.assertEqual(revised["rev"], 2)

        with patch.object(tools, "_store", return_value=self.store):
            rollback = json.loads(tools.dispatch("ump_job", {
                "action": "rollback", "payload": {"job_id": job_id, "rev": 2, "source_rev": 1},
            }))
        self.assertTrue(rollback["ok"], rollback)
        self.assertEqual(rollback["data"]["rev"], 3)


class OperatorCommandBindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.profile = self.root / "profile"
        self.profile.mkdir()
        self.store = JobStore(self.root / "data")
        self.job_id = self.store.create("operator authorization")["job_id"]

    def tearDown(self):
        self.tmp.cleanup()

    def test_legacy_approval_command_still_binds_stage_job_revision_and_one_use(self):
        key = (self.profile, "42", "42", "legacy-approval")
        with (
            patch.object(tools, "_home", return_value=self.profile),
            patch.object(tools, "_event_key", return_value=key),
            patch.object(tools, "_EVENTS", {}),
            patch.object(tools, "_turn_operator_text", return_value=""),
        ):
            tools._remember_command(
                key, f"UMP APPROVE {JOB_ID} 7 brief AbCdEfGhIjKlMnOp", tools.time.monotonic(),
            )
            self.assertEqual(tools._authorization("brief", JOB_ID, 7), "ok")
            with self.assertRaises(DomainError):
                tools._authorization("brief", JOB_ID, 7)

    def test_wrong_scope_command_cannot_unlock_a_locked_scope(self):
        self.store.lock_scope(self.job_id, 1, "brief")
        key = (self.profile, "42", "42", "wrong-unlock-scope")
        with (
            patch.object(tools, "_home", return_value=self.profile),
            patch.object(tools, "_event_key", return_value=key),
            patch.object(tools, "_EVENTS", {}),
            patch.object(tools, "_store", return_value=self.store),
        ):
            tools._remember_command(
                key, f"UMP UNLOCK {self.job_id} 1 voice", tools.time.monotonic(),
            )
            result = json.loads(tools.dispatch("ump_job", {
                "action": "unlock", "payload": {"job_id": self.job_id, "rev": 1, "scope": "brief"},
            }))
        self.assertFalse(result["ok"])
        with self.store._db() as db:
            remaining = db.execute(
                "SELECT scope FROM revision_locks WHERE job_id=?", (self.job_id,),
            ).fetchall()
        self.assertEqual([row["scope"] for row in remaining], ["brief"])

    def test_ordinary_approval_prose_cannot_unlock(self):
        self.store.lock_scope(self.job_id, 1, "brief")
        key = (self.profile, "42", "42", "prose-unlock")
        with (
            patch.object(tools, "_home", return_value=self.profile),
            patch.object(tools, "_event_key", return_value=key),
            patch.object(tools, "_EVENTS", {"__operator_text__": "setuju"}),
            patch.object(tools, "_store", return_value=self.store),
        ):
            result = json.loads(tools.dispatch("ump_job", {
                "action": "unlock", "payload": {"job_id": self.job_id, "rev": 1, "scope": "brief"},
            }))
        self.assertFalse(result["ok"])
        with self.store._db() as db:
            remaining = db.execute(
                "SELECT scope FROM revision_locks WHERE job_id=?", (self.job_id,),
            ).fetchall()
        self.assertEqual([row["scope"] for row in remaining], ["brief"])

    def test_generic_approval_cannot_unlock_and_explicit_unlock_is_one_use(self):
        self.store.lock_scope(self.job_id, 1, "brief")
        key = (self.profile, "42", "42", "generic-unlock")
        with (
            patch.object(tools, "_home", return_value=self.profile),
            patch.object(tools, "_event_key", return_value=key),
            patch.object(tools, "_EVENTS", {"__operator_text__": "gas"}),
            patch.object(tools, "_store", return_value=self.store),
        ):
            generic = json.loads(tools.dispatch("ump_job", {
                "action": "unlock", "payload": {"job_id": self.job_id, "rev": 1, "scope": "brief"},
            }))
        self.assertFalse(generic["ok"])

        exact_key = (self.profile, "42", "42", "exact-unlock")
        with (
            patch.object(tools, "_home", return_value=self.profile),
            patch.object(tools, "_event_key", return_value=exact_key),
            patch.object(tools, "_EVENTS", {}),
            patch.object(tools, "_store", return_value=self.store),
        ):
            tools._remember_command(
                exact_key, f"UMP UNLOCK {self.job_id} 1 brief", tools.time.monotonic(),
            )
            first = json.loads(tools.dispatch("ump_job", {
                "action": "unlock", "payload": {"job_id": self.job_id, "rev": 1, "scope": "brief"},
            }))
            second = json.loads(tools.dispatch("ump_job", {
                "action": "unlock", "payload": {"job_id": self.job_id, "rev": 1, "scope": "brief"},
            }))
        self.assertTrue(first["ok"], first)
        self.assertFalse(second["ok"])


class ApprovalHashTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = JobStore(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_approval_is_rejected_after_approved_artifact_bytes_change(self):
        job_id, rev, artifact = _preview_job(self.store)
        (self.store.root / artifact["path"]).write_bytes(b"different-preview-bytes")

        with self.assertRaisesRegex(DomainError, "APPROVAL_REQUIRED"):
            self.store.claim(job_id, rev, "final")

    def test_unhashed_legacy_approval_cannot_authorize_consumption(self):
        job_id, rev, artifact = _preview_job(self.store)
        with self.store._db() as db:
            columns = {row["name"] for row in db.execute("PRAGMA table_info(approvals)")}
            if "artifact_sha256" in columns:
                db.execute(
                    "UPDATE approvals SET artifact_sha256=NULL WHERE job_id=? AND rev=? AND stage='preview'",
                    (job_id, rev),
                )

        with self.assertRaisesRegex(DomainError, "APPROVAL_REQUIRED"):
            self.store.claim(job_id, rev, "final")

    def test_approval_does_not_transfer_to_a_new_revision(self):
        job_id, rev, _artifact = _preview_job(self.store)
        revised = self.store.revise(job_id, rev, {"brief": "new brief"})
        new_run = self.store.claim(job_id, revised["rev"], "preview")
        (self.store.root / "runs" / new_run["run_id"] / "preview.mp4").write_bytes(b"preview-revision-two")
        self.store.finish(new_run["run_id"], "preview.mp4", None)

        with self.assertRaisesRegex(DomainError, "APPROVAL_REQUIRED"):
            self.store.claim(job_id, revised["rev"], "final")
        with self.store._db() as db:
            count = db.execute(
                "SELECT COUNT(*) FROM approvals WHERE job_id=? AND rev=?", (job_id, revised["rev"]),
            ).fetchone()[0]
        self.assertEqual(count, 0)


class RollbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = JobStore(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_rev3_rollback_to_rev1_creates_rev4_without_approval_or_history_mutation(self):
        job = self.store.create("rev1 content")
        job_id = job["job_id"]
        self.store.revise(job_id, 1, {"brief": "rev2 content"})
        self.store.revise(job_id, 2, {"brief": "rev3 content"})
        artifact = self.store.add_artifact(
            job_id, 3, "brief", f"jobs/{job_id}/revisions/3/brief.json", create_empty=True,
        )
        self.store.approve(
            job_id, 3, "brief", artifact["id"],
            evidence={"message_id": "rev3-approval", "chat_id": "test-chat"},
        )
        history_before = self.store.get(job_id)["stage_history"]
        snapshots_before = {
            rev: (self.root / "jobs" / job_id / "revisions" / str(rev) / "input.json").read_bytes()
            for rev in (1, 2, 3)
        }
        rollback = getattr(self.store, "rollback", None)
        self.assertTrue(callable(rollback), "JobStore.rollback is not implemented")

        restored = rollback(job_id, 3, 1)

        self.assertEqual(restored["rev"], 4)
        self.assertEqual(self.store.get_revision_payload(job_id, 4)["brief"], "rev1 content")
        self.assertEqual(restored["stage"], "brief_draft")
        self.assertEqual(self.store.get(job_id)["stage_history"][:-1], history_before)
        self.assertEqual(
            (self.root / "jobs" / job_id / "revisions" / "4" / "input.json").read_bytes(),
            (self.root / "jobs" / job_id / "revisions" / "1" / "input.json").read_bytes(),
        )
        self.assertEqual(
            {rev: (self.root / "jobs" / job_id / "revisions" / str(rev) / "input.json").read_bytes()
             for rev in (1, 2, 3)},
            snapshots_before,
        )
        with self.store._db() as db:
            approvals = db.execute("SELECT COUNT(*) FROM approvals WHERE job_id=? AND rev=4", (job_id,)).fetchone()[0]
        self.assertEqual(approvals, 0)

    def test_rollback_rejects_tampered_historic_snapshot_without_revision(self):
        job = self.store.create("verified history")
        self.store.revise(job["job_id"], 1, {"brief": "current"})
        snapshot = self.root / "jobs" / job["job_id"] / "revisions" / "1" / "input.json"
        snapshot.write_bytes(b"{}")
        rollback = getattr(self.store, "rollback", None)
        self.assertTrue(callable(rollback), "JobStore.rollback is not implemented")

        with self.assertRaises(DomainError):
            rollback(job["job_id"], 2, 1)

        self.assertEqual(self.store.get(job["job_id"])["rev"], 2)
        self.assertFalse((self.root / "jobs" / job["job_id"] / "revisions" / "3").exists())

    def test_custom_rollback_copies_pinned_content_preserves_locks_and_drops_approvals(self):
        job_id, source_rev, composition, audio_bytes, _asset = _custom_voice_job(self.store)
        metadata = self.store.get_revision_payload(job_id, source_rev)["composition"]["metadata"]
        asset_path = metadata["assets"][0]["path"]
        audio_path = metadata["audio_tracks"][0]["path"]
        self.store.lock_scope(job_id, source_rev, "composition.metadata.assets")

        def approve_preview(rev, message_id):
            self.store.set_stage(job_id, rev, "preview")
            artifact = self.store.add_artifact(
                job_id, rev, "preview", f"jobs/{job_id}/revisions/{rev}/preview.mp4", create_empty=True,
            )
            self.store.approve(
                job_id, rev, "preview", artifact["id"],
                evidence={"message_id": message_id, "chat_id": "test-chat"},
            )

        approve_preview(source_rev, "source-preview")
        changed = copy.deepcopy(composition)
        changed["ass_style"]["font_size"] = 60
        current = self.store.write_composition(job_id, source_rev, changed)["rev"]
        approve_preview(current, "current-preview")

        def historic_files(rev):
            directory = self.root / "jobs" / job_id / "revisions" / str(rev)
            relative_paths = [
                "input.json", "project.json", "composition/index.html",
                f"composition/{asset_path}", f"composition/{audio_path}",
            ]
            return {path: (directory / path).read_bytes() for path in relative_paths}

        old_snapshots = {rev: historic_files(rev) for rev in (source_rev, current)}
        with self.store._db() as db:
            lock = db.execute(
                "SELECT lock_rev, scope, protected_sha256 FROM revision_locks WHERE job_id=?",
                (job_id,),
            ).fetchone()
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM approvals WHERE job_id=? AND rev=?", (job_id, current),
            ).fetchone()[0], 1)

        restored = self.store.rollback(job_id, current, source_rev)
        self.assertEqual(restored["rev"], current + 1)
        self.assertEqual(
            self.store.get_revision_payload(job_id, restored["rev"]),
            self.store.get_revision_payload(job_id, source_rev),
        )
        restored_dir = self.root / "jobs" / job_id / "revisions" / str(restored["rev"])
        for relative_path, expected in historic_files(source_rev).items():
            self.assertEqual((restored_dir / relative_path).read_bytes(), expected)
        self.assertEqual((restored_dir / f"composition/{audio_path}").read_bytes(), audio_bytes)
        self.assertEqual({rev: historic_files(rev) for rev in (source_rev, current)}, old_snapshots)
        with self.store._db() as db:
            approvals = db.execute(
                "SELECT COUNT(*) FROM approvals WHERE job_id=? AND rev=?", (job_id, restored["rev"]),
            ).fetchone()[0]
            remaining_lock = db.execute(
                "SELECT lock_rev, scope, protected_sha256 FROM revision_locks WHERE job_id=?",
                (job_id,),
            ).fetchone()
        self.assertEqual(approvals, 0)
        self.assertEqual(tuple(remaining_lock), tuple(lock))

    def test_custom_rollback_rejects_tampered_historic_project_and_asset(self):
        for relative_path in ("project.json", "composition/assets/wing-v1.svg"):
            with self.subTest(relative_path=relative_path), tempfile.TemporaryDirectory() as tmp:
                store = JobStore(Path(tmp))
                job_id, source_rev, composition, _audio_bytes, _asset = _custom_voice_job(store)
                changed = copy.deepcopy(composition)
                changed["ass_style"]["font_size"] = 60
                current = store.write_composition(job_id, source_rev, changed)["rev"]
                historic = Path(tmp) / "jobs" / job_id / "revisions" / str(source_rev) / relative_path
                historic.write_bytes(historic.read_bytes() + b"tamper")

                with self.assertRaises(DomainError):
                    store.rollback(job_id, current, source_rev)

                self.assertEqual(store.get(job_id)["rev"], current)
                self.assertFalse((Path(tmp) / "jobs" / job_id / "revisions" / str(current + 1)).exists())


class CompatibilityAndSubtitleStyleTests(unittest.TestCase):
    def test_pre_m4_database_remains_readable_and_adds_nullable_hash_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = json.dumps({"brief": "legacy", "cues": {}, "mode": "collaborative", "requirements": {}})
            with sqlite3.connect(root / "jobs.sqlite3") as db:
                db.executescript("""
                    CREATE TABLE jobs (
                        job_id TEXT PRIMARY KEY, brief TEXT NOT NULL, rev INTEGER NOT NULL,
                        status TEXT NOT NULL, error TEXT, payload TEXT NOT NULL
                    );
                    CREATE TABLE revisions (
                        job_id TEXT NOT NULL, rev INTEGER NOT NULL, payload TEXT NOT NULL,
                        PRIMARY KEY(job_id, rev)
                    );
                    CREATE TABLE approvals (
                        job_id TEXT NOT NULL, rev INTEGER NOT NULL, stage TEXT NOT NULL,
                        artifact_id TEXT NOT NULL, PRIMARY KEY(job_id, rev, stage)
                    );
                """)
                db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?)", (JOB_ID, "legacy", 1, "draft", None, payload))
                db.execute("INSERT INTO revisions VALUES (?,?,?)", (JOB_ID, 1, payload))
                db.execute("INSERT INTO approvals VALUES (?,?,?,?)", (JOB_ID, 1, "preview", "old-artifact"))

            store = JobStore(root)
            self.assertEqual(store.get(JOB_ID)["rev"], 1)
            self.assertEqual(store.get_revision_payload(JOB_ID, 1)["brief"], "legacy")
            with store._db() as db:
                columns = {row["name"] for row in db.execute("PRAGMA table_info(approvals)")}
            self.assertIn("artifact_sha256", columns)
            self.assertTrue((root / "jobs.sqlite3").is_file())

    def test_optional_ass_style_is_validated_and_default_style_is_used_when_absent(self):
        value = {
            "html": "<!doctype html><body>scene</body>",
            "width": 1080,
            "height": 1920,
            "fps": 30,
            "duration_frames": 30,
            "audio_enabled": True,
            "audio_tracks": [{
                "track_id": "voice", "blob_ref": "A" * 16, "kind": "voice",
                "offset_ms": 0, "trim_start_ms": 0, "trim_end_ms": 1000,
                "gain_db": 0.0, "fade_in_ms": 0, "fade_out_ms": 0,
            }],
            "subtitles_enabled": True,
            "subtitle_mode": "ass",
            "ass_style": {**MOBILE_ASS_STYLE, "font_size": 60},
        }
        normalized = validate_composition(value)
        self.assertEqual(normalized["ass_style"]["font_size"], 60)
        with self.assertRaises(DomainError):
            validate_composition({**value, "ass_style": {**MOBILE_ASS_STYLE, "font_size": 113}})

        with tempfile.TemporaryDirectory() as tmp:
            received = {}

            def render(mode, timeline, style, **kwargs):
                received.update(mode=mode, style=copy.deepcopy(style), kwargs=kwargs)
                return b"[Script Info]\n"

            with patch("ump_core.subtitles.render_subtitles", side_effect=render):
                _write_custom_subtitle(
                    Path(tmp), {"words": []}, {"sha256": hashlib.sha256(b"voice").hexdigest(), "duration_ms": 1000}
                )

        self.assertEqual(received["style"], MOBILE_ASS_STYLE)


def suite():
    return unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])


if __name__ == "__main__":
    unittest.main()
