from __future__ import annotations

import hashlib
import importlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

try:
    verify_m4 = importlib.import_module("scripts.verify_m4")
except ModuleNotFoundError as exc:
    if exc.name != "scripts.verify_m4":
        raise
    verify_m4 = None


REFERENCE_HTML = (
    "<!doctype html><script>const phase=t*2.48, flap=.22+.52*Math.sin(phase), "
    "cx=540+142*Math.sin(t*.63), cy=888-132*Math.sin(t*.83), scale=1.04;</script>"
)
NEGATIVE_HTML = REFERENCE_HTML.replace("flap=.22+.52*Math.sin(phase)", "flap=.22")
CALIBRATION = {
    "schema_version": 1,
    "locked": True,
    "action": {
        "before_ms": 250,
        "start_ms": 500,
        "end_ms": 2500,
        "during_ms": [750, 1000, 1250, 1500, 1750, 2000, 2250],
        "after_ms": 2750,
    },
    "wing_roi": [-330, -210, 100, 100],
    "reference_roi": [-145, 80, 42, 42],
    "movement_threshold": 0.04,
    "still_threshold": 0.025,
}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class M4ProofTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ump-m4-proof-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.calibration_sha256 = _sha(json.dumps(
            CALIBRATION, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8"))
        self.reference = self._record(
            "reference-job-0001", 2, "reference-run-0001", REFERENCE_HTML,
            b"reference media", "reference", "pass",
        )
        self.before = self._record(
            "demo-job-000001", 2, "demo-run-before1", NEGATIVE_HTML,
            b"negative media", "before", "fail",
        )
        self.after = self._record(
            "demo-job-000001", 3, "demo-run-after01", REFERENCE_HTML,
            b"repaired media", "after", "pass",
        )

    def _require_verifier(self):
        if verify_m4 is None:
            self.fail("scripts.verify_m4 proof verifier feature is missing")
        return verify_m4

    def _record(self, job_id, rev, run_id, html, media_bytes, label, motion_status):
        media_rel = f"runs/{run_id}/preview.mp4"
        media_path = self.root / media_rel
        media_path.parent.mkdir(parents=True, exist_ok=True)
        media_path.write_bytes(media_bytes)

        asset = b"<svg xmlns='http://www.w3.org/2000/svg' width='8' height='8'/>"
        html_sha = _sha(html.encode())
        asset_sha = _sha(asset)
        project_data: dict[str, object] = {"assets": ["assets/wing-v1.svg"]}
        if label != "reference":
            # Newer writer materializes neutral M3 fields absent in the M2 archive.
            project_data.update({"draws_speech_text": False, "speech_anchors": [],
                                 "subtitle_mode": "none", "subtitle_effect": None})
        project = json.dumps(project_data, sort_keys=True, separators=(",", ":")).encode()
        project_sha = _sha(project)
        files = {
            "index.html": html.encode(),
            "project.json": project,
            "assets/wing-v1.svg": asset,
        }
        manifest = {
            "schema_version": 1,
            "files": {name: {"sha256": _sha(data), "size_bytes": len(data)}
                      for name, data in files.items()},
        }
        source_rel = f"runs/{run_id}/source.zip"
        source_path = self.root / source_rel
        with zipfile.ZipFile(source_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in files.items():
                archive.writestr(name, data)
            archive.writestr("manifest.json", json.dumps(
                manifest, sort_keys=True, separators=(",", ":"),
            ).encode())

        revision_rel = f"jobs/{job_id}/revisions/{rev}/composition"
        revision_path = self.root / revision_rel
        (revision_path / "assets").mkdir(parents=True, exist_ok=True)
        (revision_path / "index.html").write_bytes(html.encode())
        (revision_path / "assets/wing-v1.svg").write_bytes(asset)
        return {
            "job_id": job_id,
            "rev": rev,
            "run_id": run_id,
            "calibration_sha256": self.calibration_sha256,
            "preview": {
                "artifact_id": f"preview-{label}-0001",
                "path": media_rel,
                "sha256": _sha(media_bytes),
                "size_bytes": len(media_bytes),
            },
            "source": {
                "artifact_id": f"source-{label}-0001",
                "path": source_rel,
                "sha256": _sha(source_path.read_bytes()),
                "revision_path": revision_rel,
                "html_sha256": html_sha,
                "project_sha256": project_sha,
                "asset_pins": {"assets/wing-v1.svg": asset_sha},
            },
            "technical_status": "pass",
            "motion_status": motion_status,
            "editorial_status": "pending_review",
            "repair_attempts": 1 if label == "after" else 0,
        }

    def _rewrite_project(self, record, updates):
        source_path = self.root / record["source"]["path"]
        with zipfile.ZipFile(source_path) as archive:
            files = {name: archive.read(name) for name in archive.namelist()
                     if name != "manifest.json"}
        project = json.loads(files["project.json"])
        project.update(updates)
        files["project.json"] = json.dumps(
            project, sort_keys=True, separators=(",", ":"),
        ).encode()
        manifest = {"schema_version": 1, "files": {
            name: {"sha256": _sha(data), "size_bytes": len(data)}
            for name, data in files.items()
        }}
        with zipfile.ZipFile(source_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in files.items():
                archive.writestr(name, data)
            archive.writestr("manifest.json", json.dumps(
                manifest, sort_keys=True, separators=(",", ":"),
            ).encode())
        record["source"]["project_sha256"] = _sha(files["project.json"])
        record["source"]["sha256"] = _sha(source_path.read_bytes())

    def test_pair_rejects_previews_from_different_jobs(self):
        verifier = self._require_verifier()
        self.after["job_id"] = "another-job-00001"
        with self.assertRaises(verifier.ProofError):
            verifier.validate_pair(self.root, self.reference, self.before, self.after, CALIBRATION)

    def test_pair_requires_consecutive_revisions(self):
        verifier = self._require_verifier()
        self.after["rev"] += 1
        with self.assertRaises(verifier.ProofError):
            verifier.validate_pair(self.root, self.reference, self.before, self.after, CALIBRATION)

    def test_pair_rejects_wrong_media_hash_and_wrong_source_pin(self):
        verifier = self._require_verifier()
        self.before["preview"]["sha256"] = "0" * 64
        with self.assertRaises(verifier.ProofError):
            verifier.validate_pair(self.root, self.reference, self.before, self.after, CALIBRATION)

        self.before["preview"]["sha256"] = _sha(b"negative media")
        self.before["source"]["asset_pins"]["assets/wing-v1.svg"] = "f" * 64
        with self.assertRaises(verifier.ProofError):
            verifier.validate_pair(self.root, self.reference, self.before, self.after, CALIBRATION)

    def test_pair_rejects_calibration_changed_after_negative(self):
        verifier = self._require_verifier()
        self.after["calibration_sha256"] = "e" * 64
        with self.assertRaises(verifier.ProofError):
            verifier.validate_pair(self.root, self.reference, self.before, self.after, CALIBRATION)

    def test_playback_claim_without_review_or_with_foreign_reviewer_stays_pending(self):
        verifier = self._require_verifier()
        digest = self.before["preview"]["sha256"]
        self.assertEqual(verifier.editorial_status(None, digest, expected_reviewer="operator"),
                         "pending_review")
        unreviewed = {
            "kind": "playback_1x", "status": "pass", "speed": 1.0,
            "artifact_sha256": digest, "observation": "played to ended",
            "automatic_completion": True,
        }
        self.assertEqual(verifier.editorial_status(
            unreviewed, digest, expected_reviewer="operator",
        ), "pending_review")
        foreign = {
            "kind": "playback_1x", "status": "pass", "speed": 1.0,
            "artifact_sha256": digest, "reviewer": "model", "observation": "looks good",
            "evidence_ref": "review:foreign",
        }
        self.assertEqual(verifier.editorial_status(
            foreign, digest, expected_reviewer="operator",
        ), "pending_review")

    def test_evidence_output_collision_is_refused_without_modifying_existing_files(self):
        verifier = self._require_verifier()
        out = self.root / "evidence" / "m4" / "20261127T191900000000"
        out.mkdir(parents=True)
        prior = out / "index.json"
        prior.write_text("previous evidence\n", encoding="utf-8")
        with self.assertRaises(verifier.ProofError):
            verifier.create_fresh_evidence_dir(out)
        self.assertEqual(prior.read_text(encoding="utf-8"), "previous evidence\n")

    def test_resume_selects_only_the_three_finished_existing_runs(self):
        verifier = self._require_verifier()
        prior = {"status": "blocked_or_incomplete", "blocker": {
            "message": "non-motion composition metadata changed across the proof pair"},
            "runs": [
                {"purpose": "intentional_hold_calibration_control", "run_id": "hold-run-0000001",
                 "status": "finished"},
                {"purpose": "negative_preview", "run_id": "negative-run-0001",
                 "status": "finished"},
                {"purpose": "repaired_preview", "run_id": "repaired-run-0001",
                 "status": "finished"},
            ]}
        self.assertEqual(verifier.resume_run_ids(prior), {
            "intentional_hold_calibration_control": "hold-run-0000001",
            "negative_preview": "negative-run-0001",
            "repaired_preview": "repaired-run-0001",
        })
        prior["runs"][2]["run_id"] = prior["runs"][1]["run_id"]
        with self.assertRaises(verifier.ProofError):
            verifier.resume_run_ids(prior)

    def test_pair_rejects_real_nonmotion_project_change_with_matching_zip_pins(self):
        verifier = self._require_verifier()
        for record in (self.before, self.after):
            self._rewrite_project(record, {"subtitle_mode": "web"})
        with self.assertRaisesRegex(verifier.ProofError, "metadata changed from the original"):
            verifier.validate_pair(self.root, self.reference, self.before, self.after, CALIBRATION)

    def test_valid_consecutive_same_job_pair_keeps_editorial_review_pending(self):
        verifier = self._require_verifier()
        result = verifier.validate_pair(self.root, self.reference, self.before, self.after, CALIBRATION)
        self.assertEqual(result["pair_status"], "technical_motion_verified")
        self.assertEqual(result["before_motion_status"], "fail")
        self.assertEqual(result["after_motion_status"], "pass")
        self.assertEqual(result["editorial_status"], "pending_review")


if __name__ == "__main__":
    unittest.main()
