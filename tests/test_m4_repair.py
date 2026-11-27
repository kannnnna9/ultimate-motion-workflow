import hashlib
import importlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ump_core import tools
from ump_core.jobs import DomainError, JobStore
from ump_core.quality import build_review


def _repair_module():
    try:
        return importlib.import_module("ump_core.repair")
    except ModuleNotFoundError as exc:
        if exc.name == "ump_core.repair":
            return None
        raise


def _composition(html="<!doctype html><body>scene</body>"):
    return {
        "html": html,
        "width": 1080,
        "height": 1920,
        "fps": 30,
        "duration_frames": 30,
        "audio_enabled": False,
        "subtitles_enabled": False,
    }


def _review(run, artifact, *, failures=1, pending=False, advisory_only=False):
    digest = hashlib.sha256(artifact["bytes"]).hexdigest()
    binding = {
        "job_id": run["job_id"],
        "rev": run["rev"],
        "run_id": run["run_id"],
        "artifact_sha256": digest,
    }
    checks = [
        {"category": category, "status": "pass", "criterion": f"{category}_baseline"}
        for category in ("technical", "motion", "visual", "editorial")
    ]
    if failures:
        for index in range(failures):
            checks.append({
                "category": "motion",
                "status": "fail",
                "criterion": "required_wing_flap",
                "required": True,
                "evidence_ref": f"crop:wing:{index}",
            })
    if pending:
        checks.append({
            "category": "editorial",
            "status": "pending_review",
            "criterion": "normal_speed_playback",
            "requires_playback": True,
        })
    if advisory_only:
        checks.append({
            "category": "visual",
            "status": "fail",
            "criterion": "optional_artistic_rhythm",
            "advisory": True,
        })
    observations = [{
        "shot": "S1",
        "time_ms": 1000,
        "intent": "wing_flap",
        "actual": "frozen wing",
        "severity": "major",
        "suspected_cause": "wing transform is static",
        "repair": "restore the requested articulation",
        "impact": "the action reads as a hold",
        "status": "fail" if failures else "pending_review" if pending else "pass",
        "reviewer": "test-reviewer",
        "observation_type": "decoded_video_crop",
        "evidence_ref": "crop:wing:1000",
    }]
    return build_review(binding, checks, observations)


class RepairTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = JobStore(self.root / "data")

    def _assert_repair_module(self):
        module = _repair_module()
        if module is None:
            raise AssertionError("ump_core.repair repair controller is not implemented")
        return module

    def _preview(self, *, mode="autonomous", html="<!doctype html><body>scene</body>"):
        job = self.store.create("bounded creative repair", mode=mode)
        composed = self.store.write_composition(job["job_id"], job["rev"], _composition(html))
        run = self.store.claim(job["job_id"], composed["rev"], "preview")
        raw = b"preview artifact bound to this exact run"
        (self.root / "data" / "runs" / run["run_id"] / "preview.mp4").write_bytes(raw)
        finished = self.store.finish(run["run_id"], "preview.mp4", None)
        return {
            "job_id": job["job_id"],
            "rev": composed["rev"],
            "run": run,
            "artifact": {**finished["artifact"], "bytes": raw},
        }

    def _invoke(self, preview, report, *, value=None, issue_id=None, initial_rev=None,
                request_id="repair-request", budget="allow"):
        payload = {
            "job_id": preview["job_id"],
            "rev": report["binding"]["rev"],
            "review": report,
            "request_id": request_id,
        }
        if initial_rev is not None:
            payload["initial_rev"] = initial_rev
        if issue_id is not None:
            payload["issue_id"] = issue_id
        if value is not None:
            payload["value"] = value
        with (
            patch.object(tools, "_store", return_value=self.store),
            patch.object(tools, "_repair_budget", return_value={"decision": budget, "reasons": []}, create=True),
        ):
            return json.loads(tools.dispatch("ump_job", {"action": "repair", "payload": payload}))

    def _rendered_preview(self, job_id, rev, marker):
        run = self.store.claim(job_id, rev, "preview")
        raw = f"preview-{marker}".encode()
        (self.root / "data" / "runs" / run["run_id"] / "preview.mp4").write_bytes(raw)
        result = self.store.finish(run["run_id"], "preview.mp4", None)
        return {
            "job_id": job_id,
            "rev": rev,
            "run": run,
            "artifact": {**result["artifact"], "bytes": raw},
        }

    def test_repair_decision_stops_at_three_automatic_cycles(self):
        module = self._assert_repair_module()
        failed = {"overall_status": "fail"}
        self.assertEqual(module.repair_decision(failed, 3, True, False), "stop")
        self.assertEqual(module.repair_decision(failed, 0, True, True), "stop")

    def test_first_repair_counts_after_preview_and_is_idempotent_across_reopen(self):
        preview = self._preview()
        report = _review(preview["run"], preview["artifact"])
        result = self._invoke(preview, report, value=_composition("<!doctype html><body>repaired</body>"))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["data"]["decision"], "repair")
        repaired_rev = result["data"]["rev"]
        self.assertEqual(repaired_rev, preview["rev"] + 1)

        reopened = JobStore(self.root / "data")
        status = reopened.get(preview["job_id"])["repairs"][0]
        self.assertEqual(status["attempts"], 1)
        self.assertEqual(status["remaining"], 2)
        self.assertEqual(status["history"][0]["rev"], repaired_rev)
        with patch.object(tools, "_store", return_value=reopened):
            fetched = json.loads(tools.dispatch("ump_job", {
                "action": "get", "payload": {"job_id": preview["job_id"]},
            }))
        self.assertTrue(fetched["ok"], fetched)
        self.assertEqual(fetched["data"]["repairs"][0]["attempts"], 1)

        retried = {
            "job_id": preview["job_id"],
            "rev": preview["rev"],
            "review": report,
            "issue_id": result["data"]["issue_id"],
            "initial_rev": preview["rev"],
            "request_id": "repair-request",
            "value": _composition("<!doctype html><body>repaired</body>"),
        }
        with (
            patch.object(tools, "_store", return_value=reopened),
            patch.object(tools, "_repair_budget", return_value={"decision": "allow", "reasons": []}, create=True),
        ):
            duplicate = json.loads(tools.dispatch("ump_job", {"action": "repair", "payload": retried}))
        self.assertTrue(duplicate["ok"], duplicate)
        self.assertEqual(duplicate["data"]["rev"], repaired_rev)
        latest = reopened.get(preview["job_id"])["repairs"][0]
        self.assertEqual(latest["attempts"], 1)
        self.assertEqual(latest["remaining"], 2)

    def test_revision_is_counted_and_linked_when_its_render_fails(self):
        preview = self._preview()
        report = _review(preview["run"], preview["artifact"])
        result = self._invoke(preview, report, value=_composition("<!doctype html><body>retry</body>"))
        self.assertTrue(result["ok"], result)
        rev = result["data"]["rev"]
        run = self.store.claim(preview["job_id"], rev, "preview")
        failed = self.store.finish(run["run_id"], None, "renderer failed")
        self.assertEqual(failed["status"], "failed")

        reopened = JobStore(self.root / "data")
        status = reopened.get(preview["job_id"])["repairs"][0]
        self.assertEqual(status["attempts"], 1)
        self.assertEqual(status["history"][0]["rev"], rev)
        self.assertIn(run["run_id"], status["history"][0]["run_ids"])
        self.assertEqual(status["history"][0]["status"], "render_failed")
        retry = reopened.claim(preview["job_id"], rev, "preview")
        reopened.finish(retry["run_id"], None, "interrupted retry")
        after_retry = reopened.get(preview["job_id"])["repairs"][0]
        self.assertEqual(after_retry["attempts"], 1)
        self.assertEqual(len(after_retry["history"][0]["run_ids"]), 2)
        self.assertIn(retry["run_id"], after_retry["history"][0]["run_ids"])
        self.assertEqual(after_retry["history"][0]["status"], "render_failed")

    def test_lock_conflict_stops_before_revision_or_attempt_mutation(self):
        preview = self._preview()
        self.store.lock_scope(preview["job_id"], preview["rev"], "composition")
        report = _review(preview["run"], preview["artifact"])
        result = self._invoke(preview, report, value=_composition("<!doctype html><body>changed</body>"))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["data"]["decision"], "stop")
        self.assertEqual(result["data"]["reason"], "lock_conflict")
        current = self.store.get(preview["job_id"])
        self.assertEqual(current["rev"], preview["rev"])
        self.assertFalse((self.root / "data" / "jobs" / preview["job_id"] / "revisions" / str(preview["rev"] + 1)).exists())
        self.assertEqual(current["repairs"][0]["attempts"], 0)

    def test_attempt_registration_and_source_revision_roll_back_together(self):
        preview = self._preview()
        report = _review(preview["run"], preview["artifact"])
        with patch.object(JobStore, "_record_repair_attempt", side_effect=DomainError("TEST_FAILURE", "fault injection")):
            result = self._invoke(
                preview, report, value=_composition("<!doctype html><body>transaction fault</body>"),
            )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["error"]["code"], "TEST_FAILURE")
        current = self.store.get(preview["job_id"])
        self.assertEqual(current["rev"], preview["rev"])
        self.assertEqual(current["repairs"][0]["attempts"], 0)
        self.assertFalse((self.root / "data" / "jobs" / preview["job_id"] / "revisions" / str(preview["rev"] + 1)).exists())

    def test_noop_source_request_does_not_consume_a_repair_attempt(self):
        preview = self._preview()
        report = _review(preview["run"], preview["artifact"])
        result = self._invoke(preview, report, value=_composition())
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["error"]["code"], "EMPTY_REPAIR")
        current = self.store.get(preview["job_id"])
        self.assertEqual(current["rev"], preview["rev"])
        self.assertEqual(current["repairs"][0]["attempts"], 0)

    def test_advisory_artistic_failure_does_not_start_an_automatic_cycle(self):
        preview = self._preview()
        report = _review(preview["run"], preview["artifact"], failures=0, advisory_only=True)
        result = self._invoke(preview, report)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["data"]["decision"], "stop")
        self.assertEqual(self.store.get(preview["job_id"])["rev"], preview["rev"])
        self.assertEqual(self.store.get(preview["job_id"]).get("repairs", []), [])

    def test_pending_human_review_does_not_create_a_revision(self):
        preview = self._preview()
        report = _review(preview["run"], preview["artifact"], failures=0, pending=True)
        result = self._invoke(preview, report)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["data"]["decision"], "pending_review")
        self.assertEqual(self.store.get(preview["job_id"])["rev"], preview["rev"])

    def test_budget_stop_preserves_initial_preview_and_open_issue_diagnosis(self):
        preview = self._preview()
        report = _review(preview["run"], preview["artifact"])
        result = self._invoke(
            preview, report, value=_composition("<!doctype html><body>must not write</body>"), budget="stop",
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["data"]["decision"], "stop")
        self.assertEqual(result["data"]["reason"], "resource_budget")
        current = self.store.get(preview["job_id"])
        self.assertEqual(current["rev"], preview["rev"])
        chain = current["repairs"][0]
        self.assertEqual(chain["attempts"], 0)
        self.assertEqual(chain["best_rev"], preview["rev"])
        self.assertTrue(chain["open_issues"])
        self.assertIn("resource_budget", chain["best_reason"])

    def test_soft_budget_warning_pauses_before_revision_or_attempt(self):
        preview = self._preview()
        report = _review(preview["run"], preview["artifact"])
        result = self._invoke(
            preview, report, value=_composition("<!doctype html><body>must wait</body>"), budget="warn",
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["data"]["decision"], "stop")
        self.assertEqual(result["data"]["reason"], "resource_budget")
        current = self.store.get(preview["job_id"])
        self.assertEqual(current["rev"], preview["rev"])
        self.assertEqual(current["repairs"][0]["attempts"], 0)
        self.assertTrue(current["repairs"][0]["open_issues"])

    def test_cap_retains_best_preview_open_issues_and_stop_reason(self):
        preview = self._preview()
        initial_rev = preview["rev"]
        current = preview
        for cycle in range(1, 4):
            report = _review(current["run"], current["artifact"], failures=2 if cycle == 1 else 1)
            result = self._invoke(
                current, report, initial_rev=initial_rev, request_id=f"cycle-{cycle}",
                value=_composition(f"<!doctype html><body>repair {cycle}</body>"),
            )
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["data"]["decision"], "repair")
            current = self._rendered_preview(preview["job_id"], result["data"]["rev"], cycle)

        final_report = _review(current["run"], current["artifact"], failures=1)
        capped = self._invoke(current, final_report, initial_rev=initial_rev, request_id="after-cap")
        self.assertTrue(capped["ok"], capped)
        self.assertEqual(capped["data"]["decision"], "stop")
        self.assertEqual(capped["data"]["reason"], "attempt_limit")
        status = JobStore(self.root / "data").get(preview["job_id"])["repairs"][0]
        self.assertEqual(status["attempts"], 3)
        self.assertEqual(status["remaining"], 0)
        self.assertEqual(status["best_rev"], initial_rev + 1)
        self.assertTrue(status["open_issues"])
        self.assertIn("attempt", status["best_reason"])
        with self.store._db() as db:
            approvals = db.execute("SELECT COUNT(*) FROM approvals WHERE job_id=?", (preview["job_id"],)).fetchone()[0]
        self.assertEqual(approvals, 0, "repair controller must not issue approvals")

    def test_quality_report_binding_must_match_actual_job_revision_run_and_hash(self):
        preview = self._preview()
        report = _review(preview["run"], preview["artifact"])
        report["binding"]["artifact_sha256"] = "0" * 64
        result = self._invoke(preview, report, value=_composition("<!doctype html><body>must not edit</body>"))
        self.assertFalse(result["ok"], result)
        self.assertIn(result["error"]["code"], {"INVALID_REVIEW", "REVIEW_BINDING_MISMATCH"})
        self.assertEqual(self.store.get(preview["job_id"])["rev"], preview["rev"])

        wrong_run = _review(preview["run"], preview["artifact"])
        wrong_run["binding"]["run_id"] = "A" * 16
        result = self._invoke(preview, wrong_run, value=_composition("<!doctype html><body>must not edit</body>"))
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["error"]["code"], "REVIEW_BINDING_MISMATCH")
        self.assertEqual(self.store.get(preview["job_id"])["rev"], preview["rev"])

    def test_repair_chain_cannot_reset_on_a_later_revision(self):
        preview = self._preview()
        report = _review(preview["run"], preview["artifact"])
        first = self._invoke(preview, report, value=_composition("<!doctype html><body>first</body>"))
        self.assertTrue(first["ok"], first)
        repaired_rev = first["data"]["rev"]
        repaired = self._rendered_preview(preview["job_id"], repaired_rev, "reviewed")
        next_report = _review(repaired["run"], repaired["artifact"])

        reset = self._invoke(
            repaired, next_report, initial_rev=repaired_rev, issue_id=first["data"]["issue_id"],
            request_id="reset-initial", value=_composition("<!doctype html><body>second</body>"),
        )
        self.assertFalse(reset["ok"], reset)
        self.assertEqual(reset["error"]["code"], "REPAIR_CHAIN_MISMATCH")
        changed_issue = self._invoke(
            repaired, next_report, initial_rev=preview["rev"], issue_id="different-issue",
            request_id="reset-issue", value=_composition("<!doctype html><body>second</body>"),
        )
        self.assertFalse(changed_issue["ok"], changed_issue)
        self.assertEqual(changed_issue["error"]["code"], "REPAIR_CHAIN_MISMATCH")
        status = self.store.get(preview["job_id"])["repairs"][0]
        self.assertEqual(status["attempts"], 1)
        self.assertEqual(self.store.get(preview["job_id"])["rev"], repaired_rev)

    def test_final_gate_refuses_mandatory_failure(self):
        preview = self._preview()
        report = _review(preview["run"], preview["artifact"])
        result = self._invoke(preview, report)
        self.assertFalse(result["ok"], result)
        with self.assertRaisesRegex(DomainError, "CREATIVE_REVIEW_REQUIRED"):
            self.store.claim(preview["job_id"], preview["rev"], "final")

    def test_final_gate_refuses_a_new_preview_that_has_not_been_reviewed(self):
        preview = self._preview()
        report = _review(preview["run"], preview["artifact"])
        repaired = self._invoke(
            preview, report, value=_composition("<!doctype html><body>new revision</body>"),
        )
        self.assertTrue(repaired["ok"], repaired)
        new_preview = self._rendered_preview(preview["job_id"], repaired["data"]["rev"], "unreviewed")
        with self.assertRaisesRegex(DomainError, "CREATIVE_REVIEW_REQUIRED"):
            self.store.claim(preview["job_id"], new_preview["rev"], "final")


if __name__ == "__main__":
    unittest.main()
