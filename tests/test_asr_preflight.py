import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zipfile import ZipFile

from scripts.asr.preflight import (
    MODEL_CHECKPOINT,
    MODEL_CHECKPOINT_SHA256,
    MODEL_CHECKPOINT_SIZE,
    MODEL_FILE_ALLOWLIST,
    MODEL_LICENSE,
    MODEL_REPO,
    MODEL_REVISION,
    INSTALL_TEMP_RESERVE_BYTES,
    WHISPERX_WHEEL_SHA256,
    _wheel_license,
    build_preflight_report,
    check_resource_budget,
    fetch_model_metadata,
)


GIB = 1024 ** 3


class _HeadResponse:
    def __init__(self, size):
        self.headers = {"Content-Length": str(size)}
        self._body = b"x" * size

    def read(self, size=-1):
        return self._body if size < 0 else self._body[:size]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class ModelMetadataTests(unittest.TestCase):
    def test_metadata_reads_small_pinned_files_and_heads_checkpoint(self):
        files = sorted(MODEL_FILE_ALLOWLIST)
        responses = [
            _HeadResponse(MODEL_CHECKPOINT_SIZE if name == MODEL_CHECKPOINT else 200 + index)
            for index, name in enumerate(files)
        ]
        with patch("scripts.asr.preflight.urllib.request.urlopen", side_effect=responses) as urlopen:
            metadata = fetch_model_metadata()
        self.assertEqual(metadata["repo"], MODEL_REPO)
        self.assertEqual(metadata["revision"], MODEL_REVISION)
        self.assertEqual(metadata["license"], MODEL_LICENSE)
        checkpoint = next(item for item in metadata["files"] if item["filename"] == MODEL_CHECKPOINT)
        self.assertEqual(checkpoint["size_bytes"], MODEL_CHECKPOINT_SIZE)
        self.assertEqual(checkpoint["sha256"], MODEL_CHECKPOINT_SHA256)
        self.assertEqual(urlopen.call_count, len(MODEL_FILE_ALLOWLIST))
        for name, call in zip(files, urlopen.call_args_list, strict=True):
            request = call.args[0]
            self.assertEqual(request.get_method(), "HEAD" if name == MODEL_CHECKPOINT else "GET")
            self.assertIn("hf-mirror.com", request.full_url)
            self.assertIn(MODEL_REVISION, request.full_url)
        self.assertTrue(all(len(item["sha256"]) == 64 for item in metadata["files"]))

    def test_metadata_rejects_checkpoint_size_drift(self):
        files = sorted(MODEL_FILE_ALLOWLIST)
        responses = [
            _HeadResponse(MODEL_CHECKPOINT_SIZE - 1 if name == MODEL_CHECKPOINT else 200 + index)
            for index, name in enumerate(files)
        ]
        with patch("scripts.asr.preflight.urllib.request.urlopen", side_effect=responses):
            with self.assertRaisesRegex(ValueError, "checkpoint"):
                fetch_model_metadata()


class WheelLicenseTests(unittest.TestCase):
    def test_bundled_license_is_classified_when_metadata_has_no_license_field(self):
        license_text = b"The MIT License (MIT)\nCopyright (c) Test"
        with tempfile.TemporaryDirectory() as temporary:
            wheel = Path(temporary) / "sample-1.0-py3-none-any.whl"
            with ZipFile(wheel, "w") as archive:
                archive.writestr("sample-1.0.dist-info/METADATA", "Name: sample\nVersion: 1.0\n")
                archive.writestr("sample-1.0.dist-info/licenses/LICENSE", license_text)
            with ZipFile(wheel) as archive:
                metadata = archive.read("sample-1.0.dist-info/METADATA").decode()
                result = _wheel_license(metadata, archive)
        self.assertEqual(result["license"], "MIT")
        self.assertEqual(result["license_source"], "dist-info license file")
        self.assertEqual(result["license_evidence_sha256"], hashlib.sha256(license_text).hexdigest())

    def test_apache_software_classifier_is_recognized(self):
        metadata_text = (
            "Name: tokenizers\nVersion: 0.22.2\n"
            "Classifier: License :: OSI Approved :: Apache Software License\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            wheel = Path(temporary) / "sample-1.0-py3-none-any.whl"
            with ZipFile(wheel, "w") as archive:
                archive.writestr("sample-1.0.dist-info/METADATA", metadata_text)
            with ZipFile(wheel) as archive:
                result = _wheel_license(metadata_text, archive)
        self.assertEqual(result["license"], "Apache-2.0")
        self.assertEqual(result["license_source"], "METADATA License classifier")


class ResourceBudgetTests(unittest.TestCase):
    def test_budget_passes_only_when_both_limits_are_preserved(self):
        result = check_resource_budget(
            root_free_bytes=8 * GIB,
            data_root_bytes=GIB,
            planned_root_bytes=2 * GIB,
            planned_data_root_bytes=0,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["root_free_after_bytes"], 6 * GIB)
        self.assertEqual(result["data_root_after_bytes"], GIB)
        self.assertEqual(result["reasons"], [])

    def test_budget_pauses_below_root_reserve(self):
        result = check_resource_budget(
            root_free_bytes=6 * GIB,
            data_root_bytes=GIB,
            planned_root_bytes=2 * GIB,
            planned_data_root_bytes=0,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["root_free_after_bytes"], 4 * GIB)
        self.assertIn("root_reserve", result["reasons"])

    def test_budget_pauses_above_data_root_soft_threshold(self):
        data_root_bytes = 19 * GIB // 10
        planned_data_root_bytes = GIB // 5
        result = check_resource_budget(
            root_free_bytes=8 * GIB,
            data_root_bytes=data_root_bytes,
            planned_root_bytes=2 * GIB,
            planned_data_root_bytes=planned_data_root_bytes,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["data_root_after_bytes"], data_root_bytes + planned_data_root_bytes)
        self.assertIn("data_root_soft_threshold", result["reasons"])


class PreflightReportTests(unittest.TestCase):
    def test_root_projection_includes_private_uv_cache_upper_bound(self):
        wheels = [
            {
                "name": "whisperx", "version": "3.8.6", "license": "BSD-2-Clause",
                "license_source": "METADATA License-Expression", "license_evidence_sha256": "a" * 64,
                "filename": "whisperx.whl", "download_bytes": 100,
                "installed_bytes_upper_bound": 250, "sha256": WHISPERX_WHEEL_SHA256,
            },
            {
                "name": "faster-whisper", "version": "1.2.1", "license": "MIT",
                "license_source": "METADATA License", "license_evidence_sha256": "b" * 64,
                "filename": "faster_whisper.whl", "download_bytes": 80,
                "installed_bytes_upper_bound": 150, "sha256": "c" * 64,
            },
        ]
        model_files = [
            {
                "filename": name,
                "size_bytes": MODEL_CHECKPOINT_SIZE if name == MODEL_CHECKPOINT else 10,
                "sha256": MODEL_CHECKPOINT_SHA256 if name == MODEL_CHECKPOINT else "d" * 64,
            }
            for name in MODEL_FILE_ALLOWLIST
        ]
        model = {
            "repo": MODEL_REPO,
            "revision": MODEL_REVISION,
            "license": MODEL_LICENSE,
            "files": model_files,
        }
        with (
            patch("scripts.asr.preflight.inspect_wheelhouse", return_value=wheels),
            patch("scripts.asr.preflight.fetch_model_metadata", return_value=model),
            patch("scripts.asr.preflight.shutil.disk_usage", return_value=SimpleNamespace(free=20 * GIB)),
            patch("scripts.asr.preflight._directory_bytes", return_value=0),
        ):
            report = build_preflight_report(Path("/unused/wheelhouse"), data_root=Path("/unused/data"))
        expanded = sum(item["installed_bytes_upper_bound"] for item in wheels)
        model_bytes = sum(item["size_bytes"] for item in model_files)
        self.assertEqual(report["installer_cache_upper_bound_bytes"], expanded)
        self.assertEqual(
            report["planned_root_bytes"],
            expanded + expanded + model_bytes + INSTALL_TEMP_RESERVE_BYTES,
        )


if __name__ == "__main__":
    unittest.main()
