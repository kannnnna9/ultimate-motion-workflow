from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
import urllib.error
import urllib.request
import zipfile
from email.parser import Parser
from pathlib import Path
from typing import Any

GIB = 1024**3
ROOT_RESERVE_BYTES = 5 * GIB
DATA_ROOT_SOFT_BYTES = 2 * GIB
DATA_ROOT_HARD_BYTES = 3 * GIB
REPORT_RESERVE_BYTES = 128 * 1024
INSTALL_TEMP_RESERVE_BYTES = 256 * 1024**2

WHISPERX_WHEEL_SHA256 = "cb6d4fcd3fb6c42305cb8b222a33a0b78f6b657e9db3b714345fe43dc0a69c1f"
MODEL_REPO = "cahya/wav2vec2-large-xlsr-indonesian"
MODEL_REVISION = "ce6da00000a403c7cf3857197f4132439667e8f0"
MODEL_LICENSE = "apache-2.0"
MODEL_CHECKPOINT = "pytorch_model.bin"
MODEL_CHECKPOINT_SIZE = 1_262_048_599
MODEL_CHECKPOINT_SHA256 = "c5040535ffd0d67f26d24fbebbc3b9c887450085e7014c9294e25494d7b3954b"
MAX_MODEL_CONFIG_BYTES = 64 * 1024
MODEL_FILE_ALLOWLIST = {
    "config.json",
    "preprocessor_config.json",
    "pytorch_model.bin",
    "special_tokens_map.json",
    "tokenizer_config.json",
    "vocab.json",
}


def check_resource_budget(
    root_free_bytes: int,
    data_root_bytes: int,
    planned_root_bytes: int,
    planned_data_root_bytes: int = 0,
) -> dict[str, Any]:
    values = (root_free_bytes, data_root_bytes, planned_root_bytes, planned_data_root_bytes)
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in values):
        raise ValueError("resource sizes must be non-negative integers")
    root_after = root_free_bytes - planned_root_bytes
    data_after = data_root_bytes + planned_data_root_bytes
    reasons: list[str] = []
    if root_after < ROOT_RESERVE_BYTES:
        reasons.append("root_reserve")
    if data_after > DATA_ROOT_SOFT_BYTES:
        reasons.append("data_root_soft_threshold")
    if data_after > DATA_ROOT_HARD_BYTES:
        reasons.append("data_root_hard_limit")
    return {
        "ok": not reasons,
        "root_free_after_bytes": root_after,
        "data_root_after_bytes": data_after,
        "reasons": reasons,
    }


def _directory_bytes(root: Path) -> int:
    if not root.exists():
        return 0
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"data root must be a real directory: {root}")
    total = 0
    for current, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(current, name)
            try:
                total += path.lstat().st_size
            except FileNotFoundError as exc:
                raise ValueError(f"data root changed during measurement: {path}") from exc
    return total


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _license_label(value: str) -> str | None:
    text = value.strip().lower().replace("_", "-")
    if "bsd 2-clause" in text or "2-clause bsd" in text or "bsd-2-clause" in text:
        return "BSD-2-Clause"
    if "bsd 3-clause" in text or "3-clause bsd" in text or "bsd-3-clause" in text:
        return "BSD-3-Clause"
    if "apache software license" in text:
        return "Apache-2.0"
    if "apache" in text and ("2.0" in text or "version 2" in text or text.strip() == "apache 2.0"):
        return "Apache-2.0"
    if "mit license" in text or text.strip() in {"mit", "mit license"}:
        return "MIT"
    if "python software foundation license" in text or text.strip() == "psfl":
        return "PSF-2.0"
    if "mozilla public license" in text or text.strip() == "mpl-2.0":
        return "MPL-2.0"
    if "lgpl" in text and "2.1" in text:
        return "LGPL-2.1-or-later"
    if "gnu general public license" in text and "version 3" in text:
        return "GPL-3.0-or-later"
    if "bsd license" in text or text.strip() == "bsd":
        return "BSD (clause unspecified)"
    return None


def _wheel_license(metadata: str, archive: zipfile.ZipFile) -> dict[str, str]:
    parsed = Parser().parsestr(metadata)
    expression = parsed.get("License-Expression")
    if expression and expression.strip().lower() not in {"unknown", "n/a"}:
        evidence = expression.strip().encode("utf-8")
        return {
            "license": expression.strip(),
            "license_source": "METADATA License-Expression",
            "license_evidence_sha256": hashlib.sha256(evidence).hexdigest(),
        }

    license_text = parsed.get("License") or ""
    classifier_values = [
        line.split("::", 1)[1].strip()
        for line in parsed.get_all("Classifier", [])
        if line.startswith("License ::")
    ]
    label = _license_label(license_text) if license_text else None
    source = "METADATA License"
    evidence = license_text.encode("utf-8") if license_text else b""
    if label is None:
        for classifier in classifier_values:
            label = _license_label(classifier)
            if label:
                source = "METADATA License classifier"
                evidence = classifier.encode("utf-8")
                break

    if label is None:
        metadata_name = next(
            (name for name in archive.namelist() if name.endswith(".dist-info/METADATA")),
            "",
        )
        dist_info = metadata_name.rsplit("/", 1)[0]
        license_paths = [
            name for name in archive.namelist()
            if name.startswith(dist_info + "/licenses/") and not name.endswith("/")
        ]
        license_texts = []
        for name in license_paths:
            info = archive.getinfo(name)
            if info.file_size <= 100_000:
                license_texts.append((name, archive.read(name)))
        recognized = [
            (name, content, _license_label(content.decode("utf-8", errors="replace")))
            for name, content in license_texts
        ]
        recognized = [(name, content, value) for name, content, value in recognized if value]
        if recognized:
            labels = sorted({value for _, _, value in recognized})
            label = " AND ".join(labels)
            source = "dist-info license file"
            evidence = b"\n".join(content for _, content, _ in recognized)

    if label is None:
        label = "unknown"
        source = "missing"
    return {
        "license": label,
        "license_source": source,
        "license_evidence_sha256": hashlib.sha256(evidence).hexdigest() if evidence else "",
    }


def inspect_wheelhouse(wheelhouse: Path) -> list[dict[str, Any]]:
    if wheelhouse.is_symlink() or not wheelhouse.is_dir():
        raise ValueError("wheelhouse must be a real directory")
    paths = sorted(wheelhouse.glob("*.whl"))
    if not paths:
        raise ValueError("wheelhouse contains no wheels")
    results: list[dict[str, Any]] = []
    for path in paths:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError(f"wheel is not a regular owned file: {path.name}")
        with zipfile.ZipFile(path) as archive:
            metadata_files = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
            if len(metadata_files) != 1:
                raise ValueError(f"wheel metadata is missing or ambiguous: {path.name}")
            metadata = archive.read(metadata_files[0]).decode("utf-8", errors="strict")
            parsed = Parser().parsestr(metadata)
            license_info = _wheel_license(metadata, archive)
            expanded_bytes = sum(item.file_size for item in archive.infolist() if not item.is_dir())
            results.append({
                "filename": path.name,
                "name": (parsed.get("Name") or "").lower().replace("_", "-").replace(".", "-"),
                "version": parsed.get("Version") or "",
                **license_info,
                "download_bytes": info.st_size,
                "installed_bytes_upper_bound": expanded_bytes,
                "sha256": _sha256(path),
            })
    if not any(item["name"] == "whisperx" and item["sha256"] == WHISPERX_WHEEL_SHA256 for item in results):
        raise ValueError("the pinned WhisperX wheel digest is absent from the measured wheelhouse")
    return results


def fetch_model_metadata() -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    for name in sorted(MODEL_FILE_ALLOWLIST):
        url = f"https://hf-mirror.com/{MODEL_REPO}/resolve/{MODEL_REVISION}/{name}"
        method = "HEAD" if name == MODEL_CHECKPOINT else "GET"
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "ump-m3-preflight/1.0"},
            method=method,
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            if method == "HEAD":
                raw_size = response.headers.get("x-linked-size") or response.headers.get("Content-Length")
                try:
                    size = int(raw_size)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"model file size is missing: {name}") from exc
                digest = MODEL_CHECKPOINT_SHA256
            else:
                body = response.read(MAX_MODEL_CONFIG_BYTES + 1)
                if len(body) > MAX_MODEL_CONFIG_BYTES:
                    raise ValueError(f"model config exceeds {MAX_MODEL_CONFIG_BYTES} bytes: {name}")
                size = len(body)
                digest = hashlib.sha256(body).hexdigest()
        if size <= 0:
            raise ValueError(f"invalid model file size: {name}")
        files.append({"filename": name, "size_bytes": size, "sha256": digest})
    checkpoint = next((item for item in files if item["filename"] == MODEL_CHECKPOINT), None)
    if checkpoint is None:
        raise ValueError("pinned PyTorch model checkpoint is missing")
    if checkpoint["size_bytes"] != MODEL_CHECKPOINT_SIZE or checkpoint["sha256"] != MODEL_CHECKPOINT_SHA256:
        raise ValueError("pinned model checkpoint size or SHA-256 changed")
    return {
        "repo": MODEL_REPO,
        "revision": MODEL_REVISION,
        "license": MODEL_LICENSE,
        "metadata_source": "hf-mirror.com pinned-revision requests; checkpoint SHA-256 pinned from official Hugging Face file metadata",
        "files": files,
    }


def build_preflight_report(
    wheelhouse: Path,
    *,
    root: Path = Path("/"),
    data_root: Path,
    model_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    wheels = inspect_wheelhouse(wheelhouse)
    model = model_metadata or fetch_model_metadata()
    if model.get("repo") != MODEL_REPO or model.get("revision") != MODEL_REVISION:
        raise ValueError("model repository or immutable revision changed")
    if str(model.get("license", "")).lower() != MODEL_LICENSE:
        raise ValueError("model license is missing or changed")
    model_files = model.get("files")
    if (
        not isinstance(model_files, list)
        or any(not isinstance(item, dict) for item in model_files)
        or {item.get("filename") for item in model_files} != MODEL_FILE_ALLOWLIST
    ):
        raise ValueError("pinned model file inventory is incomplete or unexpected")
    for item in model_files:
        digest = item.get("sha256")
        if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError(f"model file SHA-256 is missing or invalid: {item['filename']}")
    checkpoint = next(item for item in model_files if item["filename"] == MODEL_CHECKPOINT)
    if checkpoint.get("size_bytes") != MODEL_CHECKPOINT_SIZE or checkpoint.get("sha256") != MODEL_CHECKPOINT_SHA256:
        raise ValueError("pinned model checkpoint size or SHA-256 changed")
    licenses = {item["name"]: item["license"] for item in wheels}
    expected_licenses = {"whisperx": "bsd-2-clause", "faster-whisper": "mit"}
    license_errors = [
        f"{item['name']}: license metadata missing"
        for item in wheels
        if item["license"].strip().lower() == "unknown"
    ]
    for package, expected in expected_licenses.items():
        actual = licenses.get(package, "unknown").lower().replace(" ", "-")
        if actual != expected:
            license_errors.append(f"{package}: expected {expected}, found {actual}")
    disk_free = shutil.disk_usage(root).free
    current_data = _directory_bytes(data_root)
    wheel_download_bytes = sum(item["download_bytes"] for item in wheels)
    wheel_installed_upper_bound = sum(item["installed_bytes_upper_bound"] for item in wheels)
    installer_cache_upper_bound = wheel_installed_upper_bound
    model_bytes = sum(item["size_bytes"] for item in model.get("files", []))
    planned_root = wheel_installed_upper_bound + installer_cache_upper_bound + model_bytes + INSTALL_TEMP_RESERVE_BYTES
    planned_data = REPORT_RESERVE_BYTES
    budget = check_resource_budget(disk_free, current_data, planned_root, planned_data)
    ok = budget["ok"] and not license_errors
    return {
        "ok": ok,
        "platform": {"python": sys.version.split()[0], "machine": os.uname().machine},
        "wheel_count": len(wheels),
        "wheel_download_bytes": wheel_download_bytes,
        "wheel_installed_upper_bound_bytes": wheel_installed_upper_bound,
        "installer_cache_upper_bound_bytes": installer_cache_upper_bound,
        "model": model,
        "model_download_bytes": model_bytes,
        "temporary_install_reserve_bytes": INSTALL_TEMP_RESERVE_BYTES,
        "planned_root_bytes": planned_root,
        "root_free_before_bytes": disk_free,
        "data_root_before_bytes": current_data,
        "budget": budget,
        "license_errors": license_errors,
        "wheels": wheels,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure and gate the isolated UMP M3 CPU ASR footprint")
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, default=Path(os.environ.get("UMP_DATA_ROOT", Path.home() / ".hermes" / "ump-v2")))
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    try:
        report = build_preflight_report(args.wheelhouse, data_root=args.data_root)
    except (OSError, ValueError, zipfile.BadZipFile, urllib.error.URLError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 2
    rendered = json.dumps(report, sort_keys=True, indent=2)
    print(rendered)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        args.report.write_text(rendered + "\n", encoding="utf-8")
        os.chmod(args.report, 0o600)
    return 0 if report["ok"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
