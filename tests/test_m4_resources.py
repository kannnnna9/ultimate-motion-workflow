from __future__ import annotations

import json
import multiprocessing
import os
import signal
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ump_core import resources, sandbox, worker
from ump_core.jobs import DomainError, JobStore

GIB = 1024 ** 3


def _lease_process(lock_root, job_id, run_id, command, result):
    sandbox.WORKSPACE_PARENT = Path(lock_root) / "lease-parent"
    sandbox.WORKSPACE_ROOT = sandbox.WORKSPACE_PARENT / str(os.getuid())
    try:
        for attempt in range(2):
            try:
                with sandbox.render_lease():
                    result.send(("acquired", job_id, run_id))
                    if not command.poll(5):
                        result.send(("timeout", job_id, run_id))
                        return
                    command.recv()
                result.send(("released", job_id, run_id))
                return
            except DomainError as exc:
                result.send(("busy", exc.code, job_id, run_id))
                if attempt or not command.poll(5):
                    return
                command.recv()
            except KeyboardInterrupt:
                result.send(("interrupted", job_id, run_id))
                return
    finally:
        command.close()
        result.close()


def _receive(connection, process):
    if not connection.poll(5):
        raise AssertionError(f"process {process.pid} did not report within five seconds")
    return connection.recv()


def _storyboard_run(root):
    store = JobStore(Path(root))
    job_id = store.create("resource budget test")["job_id"]
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
    for action, expected, target in chain:
        if store.stage_of(job_id) == expected:
            store.advance(job_id, store.get(job_id)["rev"], action, expect=expected, to=target)
    run = store.claim(job_id, store.get(job_id)["rev"], "style_frame")
    return store, run


def _sample(*, root_free=10 * GIB, data_free=10 * GIB, data_used=0, temp_free=10 * GIB, temp_projected=0,
            root_device=1, data_device=1, memory_headroom=None, memory_complete=False):
    return {
        "root_free_bytes": root_free,
        "root_device": root_device,
        "data_root_free_bytes": data_free,
        "data_root_device": data_device,
        "data_root_used_bytes": data_used,
        "temp_free_bytes": temp_free,
        "projected_temp_bytes": temp_projected,
        "memory_headroom_bytes": memory_headroom,
        "memory_headroom_complete": memory_complete,
    }


class RenderBudgetTests(unittest.TestCase):
    def test_below_soft_threshold_allows_with_before_and_after_values(self):
        sample = _sample(data_used=2 * GIB - 1)

        result = resources.check_budget(sample, 0, "proof")

        self.assertEqual(result["decision"], "allow")
        self.assertEqual(result["data_root_used_before_bytes"], 2 * GIB - 1)
        self.assertEqual(result["data_root_used_after_bytes"], 2 * GIB - 1)
        self.assertEqual(result["root_free_before_bytes"], 10 * GIB)
        self.assertEqual(result["root_free_after_bytes"], 10 * GIB)
        self.assertEqual(result["reasons"], [])

    def test_exact_soft_threshold_warns_and_reports_numeric_projection(self):
        result = resources.check_budget(_sample(data_used=2 * GIB), 23, "long_render")

        self.assertEqual(result["decision"], "warn")
        self.assertIn("data_root_soft_threshold", result["reasons"])
        self.assertEqual(result["data_root_used_after_bytes"], 2 * GIB + 23)
        self.assertEqual(result["root_free_after_bytes"], 10 * GIB - 23)

    def test_exact_hard_threshold_stops(self):
        result = resources.check_budget(_sample(data_used=3 * GIB - 1), 1, "proof")

        self.assertEqual(result["decision"], "stop")
        self.assertIn("data_root_hard_limit", result["reasons"])
        self.assertEqual(result["data_root_used_after_bytes"], 3 * GIB)

    def test_projected_data_root_usage_over_hard_limit_stops(self):
        result = resources.check_budget(_sample(data_used=3 * GIB - 2), 3, "proof")

        self.assertEqual(result["decision"], "stop")
        self.assertEqual(result["data_root_used_after_bytes"], 3 * GIB + 1)
        self.assertIn("data_root_hard_limit", result["reasons"])

    def test_projected_temporary_space_is_counted(self):
        result = resources.check_budget(
            _sample(temp_free=128, temp_projected=129), 1, "long_render",
        )

        self.assertEqual(result["decision"], "stop")
        self.assertEqual(result["temp_free_after_bytes"], -1)
        self.assertIn("temporary_space", result["reasons"])

    def test_projected_data_root_volume_free_space_stops_independently_of_root_reserve(self):
        result = resources.check_budget(
            _sample(root_free=10 * GIB, data_free=1, root_device=1, data_device=2),
            1024 * 1024, "custom_render",
        )

        self.assertEqual(result["decision"], "stop")
        self.assertEqual(result["root_free_after_bytes"], 10 * GIB)
        self.assertEqual(result["data_root_free_before_bytes"], 1)
        self.assertEqual(result["data_root_free_after_bytes"], 1 - 1024 * 1024)
        self.assertIn("data_root_space", result["reasons"])

    def test_projection_crossing_root_reserve_stops(self):
        result = resources.check_budget(_sample(root_free=5 * GIB + 99), 100, "proof")

        self.assertEqual(result["decision"], "stop")
        self.assertEqual(result["root_free_after_bytes"], 5 * GIB - 1)
        self.assertIn("root_reserve", result["reasons"])

    def test_zero_finite_cgroup_memory_headroom_stops_without_guessing_a_workload_size(self):
        result = resources.check_budget(
            _sample(memory_headroom=0, memory_complete=True), 0, "custom_render",
        )

        self.assertEqual(result["decision"], "stop")
        self.assertEqual(result["memory_headroom_bytes"], 0)
        self.assertIn("cgroup_memory_exhausted", result["reasons"])

    def test_invalid_or_missing_samples_fail_closed(self):
        missing_data_free = _sample()
        missing_data_free.pop("data_root_free_bytes")
        for sample in ({}, _sample(root_free=-1), _sample(data_used=True), _sample(data_free=-1), missing_data_free):
            with self.subTest(sample=sample):
                result = resources.check_budget(sample, 1, "proof")
                self.assertEqual(result["decision"], "stop")
                self.assertIn("invalid_sample", result["reasons"])
        invalid_projection = resources.check_budget(_sample(), -1, "proof")
        self.assertEqual(invalid_projection["decision"], "stop")
        self.assertIn("invalid_projection", invalid_projection["reasons"])

    def test_cgroup_limits_use_minimum_across_hierarchy_and_ignore_unlimited(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            files = {
                "memory.max": "max\n",
                "cpu.max": "max 100000\n",
                "cpuset.cpus.effective": "0-7\n",
                "tenant/memory.max": f"{8 * 1024}\n",
                "tenant/memory.current": "7000\n",
                "tenant/cpu.max": "150000 100000\n",
                "tenant/cpuset.cpus.effective": "2-3\n",
                "tenant/leaf/memory.max": "max\n",
                "tenant/leaf/cpu.max": "max 100000\n",
                "tenant/leaf/cpuset.cpus.effective": "2-3\n",
                "tenant/leaf/memory.current": "2048\n",
                "tenant/leaf/memory.peak": "4096\n",
            }
            for relative, value in files.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(value, encoding="ascii")

            result = resources._cgroup_limits(root, "/tenant/leaf")

        self.assertEqual(result["memory_limit_bytes"], 8 * 1024)
        self.assertEqual(result["memory_headroom_bytes"], 8 * 1024 - 7000)
        self.assertEqual(result["cpu_limit_cores"], 1.5)
        self.assertEqual(result["cpuset_limit_cores"], 2)
        self.assertEqual(result["memory_current_bytes"], 2048)
        self.assertEqual(result["memory_peak_bytes"], 4096)

    def test_unlimited_cgroup_uses_physical_memory_and_cpu_fallback(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            leaf = root / "leaf"
            leaf.mkdir()
            (leaf / "memory.max").write_text("max\n", encoding="ascii")
            (leaf / "cpu.max").write_text("max 100000\n", encoding="ascii")
            with patch.object(resources, "_physical_memory_bytes", return_value=16 * GIB), patch.object(
                resources, "_physical_available_bytes", return_value=7 * GIB,
            ), patch.object(resources.os, "cpu_count", return_value=6):
                result = resources.probe_resources(
                    data_root=root, root=root, temp_root=root, cgroup_root=root,
                    cgroup_text="0::/leaf\n",
                )

        self.assertEqual(result["memory_limit_bytes"], 16 * GIB)
        self.assertEqual(result["memory_available_bytes"], 7 * GIB)
        self.assertEqual(result["cpu_limit_cores"], 6)
        self.assertEqual(result["memory_source"], "physical_fallback")

    def test_projection_includes_data_cache_and_temporary_workspace(self):
        projection = resources.estimate_render_projection("custom", audio_enabled=True)

        self.assertGreater(projection["data_root_bytes"], 0)
        self.assertGreater(projection["temporary_bytes"], 0)
        self.assertGreater(projection["cache_bytes"], 0)
        self.assertEqual(
            projection["total_bytes"],
            projection["data_root_bytes"] + projection["temporary_bytes"],
        )


class WorkerBudgetIntegrationTests(unittest.TestCase):
    def test_soft_threshold_pauses_long_render_before_renderer(self):
        with tempfile.TemporaryDirectory() as raw:
            store, run = _storyboard_run(raw)
            run_dir = Path(raw) / "runs" / run["run_id"]
            projection = resources.estimate_render_projection("legacy_render")
            sample = _sample(data_used=2 * GIB - projection["data_root_bytes"])
            with patch.object(sandbox, "WORKSPACE_PARENT", Path(raw) / "lease-parent"), patch.object(
                sandbox, "WORKSPACE_ROOT", Path(raw) / "lease-parent" / str(os.getuid()),
            ), patch.object(
                worker.resources, "probe_resources", return_value=sample,
            ), patch.object(
                worker, "_render", side_effect=AssertionError("long render was not paused at soft limit"),
            ):
                result = worker.execute_run(store, run["run_id"])

            assert result is not None
            self.assertEqual(result["status"], "failed")
            self.assertFalse(result.get("artifact"))
            report = json.loads((run_dir / "resources.json").read_text(encoding="utf-8"))
            self.assertEqual(report["budget"]["decision"], "warn")
            self.assertIn("data_root_soft_threshold", report["budget"]["reasons"])
            self.assertIn("paused for inventory", report["failure_reason"])

    def test_budget_stop_persists_report_fails_run_without_artifact_and_releases_lease(self):
        with tempfile.TemporaryDirectory() as raw:
            store, run = _storyboard_run(raw)
            run_dir = Path(raw) / "runs" / run["run_id"]
            sample = _sample(root_free=1)
            with patch.object(sandbox, "WORKSPACE_PARENT", Path(raw) / "lease-parent"), patch.object(
                sandbox, "WORKSPACE_ROOT", Path(raw) / "lease-parent" / str(os.getuid()),
            ), patch.object(
                worker.resources, "probe_resources", return_value=sample,
            ) as probe, patch.object(
                worker, "_render", side_effect=AssertionError("render ran despite a stopped budget"),
            ):
                result = worker.execute_run(store, run["run_id"])

                assert result is not None
                self.assertTrue(probe.call_args_list)
                self.assertTrue(all(call.kwargs["root"] == Path("/") for call in probe.call_args_list))
                self.assertEqual(result["status"], "failed")
                self.assertFalse(result.get("artifact"))
                self.assertFalse(list(Path(raw).rglob("*.mp4")))
                report_path = run_dir / "resources.json"
                self.assertTrue(report_path.is_file())
                self.assertEqual(report_path.stat().st_mode & 0o777, 0o600)
                report = json.loads(report_path.read_text(encoding="utf-8"))
                self.assertEqual(report["budget"]["decision"], "stop")
                self.assertEqual(report["budget"]["root_free_before_bytes"], 1)
                self.assertIn("root_reserve", report["budget"]["reasons"])
                self.assertGreaterEqual(report["wall_time_seconds"], 0)
                self.assertEqual(report["peak_memory_scope"], "worker_process_only")

                with sandbox.render_lease():
                    pass

    def test_custom_asr_and_render_phases_share_one_non_nested_lease(self):
        with tempfile.TemporaryDirectory() as raw:
            store, run = _storyboard_run(raw)
            run_dir = Path(raw) / "runs" / run["run_id"]
            payload = {"composition": {"metadata": {"audio_enabled": True, "subtitle_mode": "ass"}}}
            phases = []
            sample = _sample()

            def fake_custom(_store, _run, _directory, _deadline, *, budget_check=None):
                for phase in ("asr", "render"):
                    self.assertIsNotNone(budget_check)
                    budget_check()
                    with self.assertRaises(DomainError) as raised:
                        with sandbox.render_lease():
                            pass
                    self.assertEqual(raised.exception.code, "RENDER_BUSY")
                    phases.append(phase)
                raise DomainError("TEST_ABORT", "stop after lease phase assertions")

            with tempfile.TemporaryDirectory() as lease_root, patch.object(
                sandbox, "WORKSPACE_PARENT", Path(lease_root) / "parent",
            ), patch.object(
                sandbox, "WORKSPACE_ROOT", Path(lease_root) / "parent" / str(os.getuid()),
            ), patch.object(
                store, "get_revision_payload", return_value=payload,
            ), patch.object(
                worker.resources, "probe_resources", return_value=sample,
            ), patch.object(
                worker, "_render_custom", side_effect=fake_custom,
            ):
                result = worker.execute_run(store, run["run_id"])

            self.assertEqual(phases, ["asr", "render"])
            assert result is not None
            self.assertEqual(result["status"], "failed")
            self.assertFalse(result.get("artifact"))
            report = json.loads((run_dir / "resources.json").read_text(encoding="utf-8"))
            self.assertIn("TEST_ABORT", report["failure_reason"])


class RenderLeaseProcessTests(unittest.TestCase):
    def test_distinct_job_processes_compete_and_retry_after_normal_or_interrupted_owner(self):
        context = multiprocessing.get_context("fork")
        for ending in ("normal", "interrupt"):
            with self.subTest(owner_ending=ending), tempfile.TemporaryDirectory() as raw:
                owner_command_parent, owner_command_child = context.Pipe()
                owner_result_parent, owner_result_child = context.Pipe()
                contender_command_parent, contender_command_child = context.Pipe()
                contender_result_parent, contender_result_child = context.Pipe()
                owner = context.Process(
                    target=_lease_process,
                    args=(raw, "job-owner", "run-owner-00000001", owner_command_child, owner_result_child),
                )
                contender = context.Process(
                    target=_lease_process,
                    args=(raw, "job-other", "run-other-00000002", contender_command_child, contender_result_child),
                )
                owner.start()
                try:
                    self.assertEqual(
                        _receive(owner_result_parent, owner),
                        ("acquired", "job-owner", "run-owner-00000001"),
                    )
                    contender.start()
                    self.assertEqual(
                        _receive(contender_result_parent, contender),
                        ("busy", "RENDER_BUSY", "job-other", "run-other-00000002"),
                    )

                    if ending == "normal":
                        owner_command_parent.send("release")
                        self.assertEqual(
                            _receive(owner_result_parent, owner),
                            ("released", "job-owner", "run-owner-00000001"),
                        )
                    else:
                        os.kill(owner.pid, signal.SIGINT)
                        self.assertEqual(
                            _receive(owner_result_parent, owner),
                            ("interrupted", "job-owner", "run-owner-00000001"),
                        )
                    owner.join(5)
                    self.assertFalse(owner.is_alive())

                    contender_command_parent.send("retry")
                    self.assertEqual(
                        _receive(contender_result_parent, contender),
                        ("acquired", "job-other", "run-other-00000002"),
                    )
                    contender_command_parent.send("release")
                    self.assertEqual(
                        _receive(contender_result_parent, contender),
                        ("released", "job-other", "run-other-00000002"),
                    )
                    contender.join(5)
                    self.assertFalse(contender.is_alive())
                finally:
                    for process in (owner, contender):
                        if process.is_alive():
                            process.terminate()
                        process.join(5)
                    for connection in (
                        owner_command_parent, owner_command_child, owner_result_parent, owner_result_child,
                        contender_command_parent, contender_command_child,
                        contender_result_parent, contender_result_child,
                    ):
                        connection.close()
