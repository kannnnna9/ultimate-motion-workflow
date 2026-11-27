"""Content-addressed, job-private render units with fail-closed manifests."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import struct
import zlib
import uuid
from pathlib import Path
from typing import Mapping


SCHEMA_VERSION = 1
STAGES = frozenset({"alignment", "sidecar", "capture", "mux"})
_HEX_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_MAX_MANIFEST_BYTES = 4 * 1024 * 1024
_MAX_FILE_BYTES = 512 * 1024 * 1024
_MAX_IN_MEMORY_FILE_BYTES = 16 * 1024 * 1024
_MAX_CAPTURE_PNG_BYTES = 32 * 1024 * 1024
_MAX_DECODED_PNG_BYTES = 64 * 1024 * 1024


class CacheError(ValueError):
    """A cache unit cannot be safely published or read."""


def stage_key(stage: str, inputs: dict) -> str:
    """Hash the stage and its caller-supplied effective input values canonically.

    Callers pass byte digests for source material and explicit values for the
    settings consumed by that stage. Paths alone are not content identity.
    """
    if not isinstance(stage, str) or stage not in STAGES or not isinstance(inputs, dict):
        raise ValueError("stage and inputs are invalid")
    try:
        encoded = json.dumps(
            {"schema_version": SCHEMA_VERSION, "stage": stage, "inputs": inputs},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ValueError("stage inputs must be canonical JSON") from exc
    return hashlib.sha256(b"ump-render-stage\0" + encoded).hexdigest()


def unit_path(cache_root: str | os.PathLike[str], stage: str, key: str) -> Path:
    if stage not in STAGES or not isinstance(key, str) or not _HEX_SHA256.fullmatch(key):
        raise ValueError("stage cache identity is invalid")
    return Path(cache_root) / stage / key


def _safe_name(name: object) -> bool:
    return (isinstance(name, str) and bool(name) and name not in {".", "..", "manifest.json"}
            and Path(name).name == name and "/" not in name and "\\" not in name
            and not name.startswith(".") and not name.endswith(".tmp"))


def _regular_bytes(path: Path, *, limit: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
                or before.st_nlink != 1 or before.st_size < 0 or before.st_size > limit
                or before.st_mode & 0o002):
            raise CacheError("unit file is not a bounded private regular file")
        chunks = []
        total = 0
        while True:
            chunk = os.read(fd, min(1024 * 1024, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                raise CacheError("unit file exceeds its byte limit")
        after = os.fstat(fd)
        if (total != before.st_size or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
            raise CacheError("unit file changed while being read")
        return b"".join(chunks)
    finally:
        os.close(fd)


def _stream_sha256(path: Path, *, limit: int = _MAX_FILE_BYTES) -> tuple[int, str]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
                or before.st_nlink != 1 or before.st_size < 0 or before.st_size > limit
                or before.st_mode & 0o002):
            raise CacheError("unit file is not a bounded private regular file")
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(fd, min(1024 * 1024, limit - total + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise CacheError("unit file exceeds its byte limit")
            digest.update(chunk)
        after = os.fstat(fd)
        if (total != before.st_size or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
            raise CacheError("unit file changed while being hashed")
        return total, digest.hexdigest()
    finally:
        os.close(fd)


def _assert_real_directory(path: Path) -> None:
    absolute = path.absolute()
    if absolute.resolve(strict=True) != absolute:
        raise CacheError("unit path contains a symlink")
    info = absolute.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise CacheError("unit directory is not private")


def _png_dimensions(data: bytes) -> tuple[int, int] | None:
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    position = 8
    width = height = None
    bit_depth = color_type = interlace = None
    compressed = bytearray()
    saw_header = saw_data = saw_end = False
    try:
        while position < len(data):
            if position + 12 > len(data):
                return None
            size = struct.unpack_from(">I", data, position)[0]
            end = position + 12 + size
            if end > len(data):
                return None
            kind = data[position + 4:position + 8]
            payload = data[position + 8:position + 8 + size]
            crc = struct.unpack_from(">I", data, position + 8 + size)[0]
            if zlib.crc32(kind + payload) & 0xFFFFFFFF != crc:
                return None
            if kind == b"IHDR":
                if saw_header or size != 13:
                    return None
                width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(">2I5B", payload)
                if (width < 1 or height < 1 or bit_depth != 8 or color_type not in {2, 6}
                        or compression != 0 or filtering != 0 or interlace != 0):
                    return None
                saw_header = True
            elif kind == b"IDAT":
                if not saw_header or saw_end:
                    return None
                compressed.extend(payload)
                saw_data = True
            elif kind == b"IEND":
                if size != 0 or not saw_data or end != len(data):
                    return None
                saw_end = True
                break
            elif not saw_header or saw_end:
                return None
            position = end
        if not (saw_header and saw_data and saw_end):
            return None
        if width is None or height is None or color_type is None:
            return None
        channels = 3 if color_type == 2 else 4
        row_size = width * channels
        raw_size = height * (row_size + 1)
        if raw_size > _MAX_DECODED_PNG_BYTES:
            return None
        decompressor = zlib.decompressobj()
        raw = decompressor.decompress(bytes(compressed), raw_size + 1)
        if (len(raw) != raw_size or not decompressor.eof or decompressor.unused_data
                or decompressor.unconsumed_tail):
            return None
        if any(raw[row * (row_size + 1)] > 4 for row in range(height)):
            return None
        return width, height
    except (ValueError, zlib.error, struct.error):
        return None


def validate_capture_source(source: Path, *, total_frames: int, width: int, height: int) -> bool:
    """Check a recorder directory before encoding or caching its full frame sequence."""
    try:
        source = Path(source)
        if source.is_symlink() or not source.is_dir() or source.resolve(strict=True) != source:
            return False
        names = sorted(entry.name for entry in os.scandir(source))
        if names != [f"f{index:05d}.png" for index in range(total_frames)]:
            return False
        for name in names:
            if _png_dimensions(_regular_bytes(source / name, limit=_MAX_CAPTURE_PNG_BYTES)) != (width, height):
                return False
        return True
    except (OSError, ValueError, TypeError, CacheError):
        return False


def _read_manifest(path: Path) -> tuple[dict, set[str]]:
    manifest_path = path / "manifest.json"
    raw = _regular_bytes(manifest_path, limit=_MAX_MANIFEST_BYTES)
    document = json.loads(raw.decode("utf-8", "strict"))
    if (not isinstance(document, dict) or type(document.get("schema_version")) is not int
            or document.get("schema_version") != SCHEMA_VERSION):
        raise CacheError("unknown unit manifest schema")
    stage = document.get("stage")
    key = document.get("key")
    if stage not in STAGES or not isinstance(key, str) or not _HEX_SHA256.fullmatch(key):
        raise CacheError("unit manifest identity is malformed")
    actual_names = set()
    for entry in os.scandir(path):
        info = entry.stat(follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_mode & 0o002:
            raise CacheError("unit contains a symlink, directory, or unsafe file")
        actual_names.add(entry.name)
    if "manifest.json" not in actual_names:
        raise CacheError("unit manifest is missing")
    return document, actual_names


def _validate_file_entries(path: Path, entries: object, actual_names: set[str]) -> None:
    if not isinstance(entries, list) or not entries:
        raise CacheError("unit file list is empty or malformed")
    expected_names = {"manifest.json"}
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"name", "size_bytes", "sha256"}:
            raise CacheError("unit file entry is malformed")
        name, size, digest = entry["name"], entry["size_bytes"], entry["sha256"]
        if (not _safe_name(name) or name in seen or type(size) is not int or size < 1
                or size > _MAX_FILE_BYTES or not isinstance(digest, str) or not _HEX_SHA256.fullmatch(digest)):
            raise CacheError("unit file metadata is invalid")
        actual_size, actual_digest = _stream_sha256(path / name, limit=_MAX_FILE_BYTES)
        if actual_size != size or actual_digest != digest:
            raise CacheError("unit file checksum mismatch")
        seen.add(name)
        expected_names.add(name)
    if expected_names != actual_names:
        raise CacheError("unit contains missing or extra files")


def validate_unit(path: Path, expected_key: str) -> bool:
    """Validate a complete, private unit and every file checksum; reject unknown formats."""
    try:
        candidate = Path(path).absolute()
        _assert_real_directory(candidate)
        document, actual_names = _read_manifest(candidate)
        if document.get("key") != expected_key:
            return False
        if document["stage"] == "capture":
            required = {"schema_version", "stage", "key", "frame_range", "total_frames", "width", "height", "frames"}
            if set(document) != required:
                return False
            total = document["total_frames"]
            width, height = document["width"], document["height"]
            frame_range = document["frame_range"]
            frames = document["frames"]
            if (type(total) is not int or total < 1 or type(width) is not int or width < 1
                    or type(height) is not int or height < 1 or not isinstance(frame_range, list)
                    or len(frame_range) != 2 or type(frame_range[0]) is not int
                    or type(frame_range[1]) is not int or frame_range != [0, total]
                    or not isinstance(frames, list) or len(frames) != total):
                return False
            expected_names = {"manifest.json"}
            for index, entry in enumerate(frames):
                if (not isinstance(entry, dict) or set(entry) != {"index", "name", "size_bytes", "sha256"}
                        or entry.get("index") != index or type(entry.get("index")) is not int
                        or entry.get("name") != f"f{index:05d}.png"):
                    return False
                name, size, digest = entry["name"], entry["size_bytes"], entry["sha256"]
                if (not _safe_name(name) or type(size) is not int or size < 1 or size > _MAX_CAPTURE_PNG_BYTES
                        or not isinstance(digest, str) or not _HEX_SHA256.fullmatch(digest)):
                    return False
                data = _regular_bytes(candidate / name, limit=_MAX_CAPTURE_PNG_BYTES)
                if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
                    return False
                if _png_dimensions(data) != (width, height):
                    return False
                expected_names.add(name)
            return actual_names == expected_names
        if set(document) != {"schema_version", "stage", "key", "files"}:
            return False
        _validate_file_entries(candidate, document["files"], actual_names)
        return True
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, CacheError, json.JSONDecodeError, RecursionError):
        return False


def read_unit_file(path: Path, expected_key: str, name: str) -> bytes:
    if not validate_unit(path, expected_key) or not _safe_name(name):
        raise CacheError("unit is not valid for this stage key")
    manifest = json.loads(_regular_bytes(Path(path) / "manifest.json", limit=_MAX_MANIFEST_BYTES))
    if manifest["stage"] == "capture":
        raise CacheError("capture units expose indexed frames, not generic files")
    if name not in {entry["name"] for entry in manifest["files"]}:
        raise CacheError("requested unit file is not in the manifest")
    return _regular_bytes(Path(path) / name, limit=_MAX_IN_MEMORY_FILE_BYTES)


def restore_unit_file(path: Path, expected_key: str, name: str, destination: Path) -> Path:
    """Restore a validated generic unit file to a private destination without buffering it."""
    candidate = Path(path).absolute()
    if not validate_unit(candidate, expected_key) or not _safe_name(name):
        raise CacheError("unit is not valid for this stage key")
    try:
        manifest, _ = _read_manifest(candidate)
    except (OSError, UnicodeError, ValueError, TypeError, CacheError, RecursionError) as exc:
        raise CacheError("unit manifest changed during restore") from exc
    if manifest.get("stage") == "capture":
        raise CacheError("capture units expose indexed frames, not generic files")
    entries = manifest.get("files")
    entry = next((item for item in entries if isinstance(item, dict) and item.get("name") == name), None) \
        if isinstance(entries, list) else None
    if entry is None:
        raise CacheError("requested unit file is not in the manifest")
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise CacheError("restore destination already exists")
    temporary = destination.parent / f".{destination.name}.tmp-{uuid.uuid4().hex}"
    try:
        _copy_regular_file(
            candidate / name, temporary, limit=_MAX_FILE_BYTES,
            expected_size=entry["size_bytes"], expected_digest=entry["sha256"],
        )
        os.replace(temporary, destination)
        parent_fd = os.open(destination.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
        return destination
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _ensure_parent(path: Path) -> None:
    absolute = path.absolute()
    cursor = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        cursor = cursor / part
        created = False
        try:
            info = cursor.lstat()
        except FileNotFoundError:
            cursor.mkdir(mode=0o700)
            info = cursor.lstat()
            created = True
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or (created and info.st_uid != os.getuid())):
            raise CacheError("cache parent is not a real directory")


def _write_private(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _copy_regular_file(source: Path, destination: Path, *, limit: int,
                       expected_size: int | None = None, expected_digest: str | None = None) -> tuple[int, str]:
    source, destination = Path(source), Path(destination)
    _assert_real_directory(source.parent)
    _assert_real_directory(destination.parent)
    source_fd = os.open(source, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
    destination_fd = None
    destination_created = False
    try:
        before = os.fstat(source_fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid() or before.st_nlink != 1
                or before.st_size < 1 or before.st_size > limit or before.st_mode & 0o002):
            raise CacheError("stream source is not a bounded private regular file")
        destination_fd = os.open(
            destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600,
        )
        destination_created = True
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(source_fd, min(1024 * 1024, limit - total + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise CacheError("stream source exceeds its byte limit")
            digest.update(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(destination_fd, view)
                if written <= 0:
                    raise OSError("short write while copying cache file")
                view = view[written:]
        after = os.fstat(source_fd)
        if (total != before.st_size or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
            raise CacheError("stream source changed while being copied")
        actual_digest = digest.hexdigest()
        if ((expected_size is not None and total != expected_size)
                or (expected_digest is not None and actual_digest != expected_digest)):
            raise CacheError("stream source checksum mismatch")
        os.fsync(destination_fd)
        return total, actual_digest
    except Exception:
        if destination_fd is not None:
            os.close(destination_fd)
            destination_fd = None
        if destination_created:
            destination.unlink(missing_ok=True)
        raise
    finally:
        if destination_fd is not None:
            os.close(destination_fd)
        os.close(source_fd)


def file_unit_manifest_size(stage: str, key: str, name: str, size_bytes: int) -> int:
    if (stage not in STAGES or stage == "capture" or not _HEX_SHA256.fullmatch(key)
            or not _safe_name(name) or type(size_bytes) is not int or size_bytes < 1):
        raise CacheError("file-unit identity or size is invalid")
    manifest = {"schema_version": SCHEMA_VERSION, "stage": stage, "key": key, "files": [{
        "name": name, "size_bytes": size_bytes, "sha256": "0" * 64,
    }]}
    return len(json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8"))


def _publish(destination: Path, stage: str, key: str, write_outputs) -> Path:
    if stage not in STAGES or not _HEX_SHA256.fullmatch(key):
        raise CacheError("invalid unit identity")
    _ensure_parent(destination.parent)
    temporary = destination.parent / f".{destination.name}.tmp-{uuid.uuid4().hex}"
    temporary.mkdir(mode=0o700)
    try:
        entries = write_outputs(temporary)
        manifest = {"schema_version": SCHEMA_VERSION, "stage": stage, "key": key, **entries}
        raw = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        _write_private(temporary / "manifest.json", raw)
        directory_fd = os.open(temporary, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        if not validate_unit(temporary, key):
            raise CacheError("published files failed validation")
        if destination.exists() or destination.is_symlink():
            stale = destination.parent / f".{destination.name}.invalid-{uuid.uuid4().hex}"
            os.replace(destination, stale)
        os.replace(temporary, destination)
        parent_fd = os.open(destination.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
        return destination
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def publish_file_unit(destination: Path, stage: str, key: str, files: Mapping[str, bytes]) -> Path:
    """Write a generic stage unit; manifest publication and directory rename are atomic."""
    if stage == "capture" or not isinstance(files, Mapping) or not files:
        raise CacheError("generic unit contents are invalid")

    def write_outputs(temporary: Path) -> dict:
        entries = []
        for name in sorted(files):
            data = files[name]
            if not _safe_name(name) or not isinstance(data, bytes) or not data or len(data) > _MAX_FILE_BYTES:
                raise CacheError("generic unit output is invalid")
            _write_private(temporary / name, data)
            entries.append({"name": name, "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        return {"files": entries}

    return _publish(Path(destination), stage, key, write_outputs)


def publish_file_unit_from_path(destination: Path, stage: str, key: str, name: str, source: Path) -> Path:
    """Publish one generic file unit by copying, hashing, and validating in bounded chunks."""
    if stage == "capture" or not _safe_name(name):
        raise CacheError("generic unit path output is invalid")

    def write_outputs(temporary: Path) -> dict:
        size, digest = _copy_regular_file(source, temporary / name, limit=_MAX_FILE_BYTES)
        return {"files": [{"name": name, "size_bytes": size, "sha256": digest}]}

    return _publish(Path(destination), stage, key, write_outputs)


def publish_capture_unit(destination: Path, key: str, source: Path, *, total_frames: int,
                         width: int, height: int) -> Path:
    """Atomically publish a fully verified `[0, total_frames)` PNG sequence."""
    source = Path(source)
    if (type(total_frames) is not int or total_frames < 1 or type(width) is not int or width < 1
            or type(height) is not int or height < 1):
        raise CacheError("capture dimensions or frame count are invalid")
    if source.is_symlink() or not source.is_dir() or source.resolve(strict=True) != source:
        raise CacheError("capture source directory is unsafe")
    names = sorted(entry.name for entry in os.scandir(source))
    expected = [f"f{index:05d}.png" for index in range(total_frames)]
    if names != expected:
        raise CacheError("capture source is not a complete ordered frame sequence")

    def write_outputs(temporary: Path) -> dict:
        frames = []
        for index, name in enumerate(expected):
            data = _regular_bytes(source / name, limit=_MAX_CAPTURE_PNG_BYTES)
            if _png_dimensions(data) != (width, height):
                raise CacheError("captured PNG dimensions or encoding are invalid")
            _write_private(temporary / name, data)
            frames.append({"index": index, "name": name, "size_bytes": len(data),
                           "sha256": hashlib.sha256(data).hexdigest()})
        return {"frame_range": [0, total_frames], "total_frames": total_frames,
                "width": width, "height": height, "frames": frames}

    return _publish(Path(destination), "capture", key, write_outputs)


def cache_bytes(root: Path) -> int:
    """Count regular, non-symlink cache bytes without following untrusted entries."""
    root = Path(root)
    if not root.exists():
        return 0
    if root.is_symlink() or not root.is_dir() or root.resolve(strict=True) != root:
        raise CacheError("cache root is unsafe")
    total = 0
    for current, dirs, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in dirs:
            if (current_path / name).is_symlink():
                raise CacheError("cache contains a symlinked directory")
        for name in files:
            path = current_path / name
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise CacheError("cache contains a non-regular file")
            total += info.st_size
    return total


def reclaim_unit_if_fits(cache_root: Path, stage: str, key: str, *, new_unit_bytes: int,
                         reserve_bytes: int, max_unit_bytes: int = _MAX_FILE_BYTES + _MAX_MANIFEST_BYTES) -> int | None:
    """Remove only an exact private cache-key directory when replacement fits the job reserve."""
    root = Path(cache_root).absolute()
    target = unit_path(root, stage, key)
    if root.is_symlink():
        raise CacheError("cache root is a symlink")
    if not root.exists():
        return None
    _assert_real_directory(root)
    stage_dir = root / stage
    if stage_dir.is_symlink():
        raise CacheError("cache stage directory is a symlink")
    if not stage_dir.exists():
        return None
    _assert_real_directory(stage_dir)
    if target.is_symlink():
        raise CacheError("cache unit is a symlink")
    if not target.exists():
        return None
    _assert_real_directory(target)
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    root_fd = os.open(root, directory_flags)
    stage_fd = None
    unit_fd = None
    try:
        stage_fd = os.open(stage, directory_flags, dir_fd=root_fd)
        unit_fd = os.open(key, directory_flags, dir_fd=stage_fd)
        unit_stat = os.fstat(unit_fd)
        if unit_stat.st_uid != os.getuid() or unit_stat.st_mode & 0o077:
            raise CacheError("cache unit is not privately owned")
        names = os.listdir(unit_fd)
        total = 0
        for name in names:
            info = os.stat(name, dir_fd=unit_fd, follow_symlinks=False)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_nlink != 1 or info.st_mode & 0o002):
                raise CacheError("cache unit contains an unsafe entry")
            total += info.st_size
            if total > max_unit_bytes:
                raise CacheError("cache unit exceeds the reclaim bound")
        current = cache_bytes(root)
        if current - total + new_unit_bytes > reserve_bytes:
            return None
        for name in names:
            os.unlink(name, dir_fd=unit_fd)
        os.rmdir(key, dir_fd=stage_fd)
        os.fsync(stage_fd)
        return total
    finally:
        if unit_fd is not None:
            os.close(unit_fd)
        if stage_fd is not None:
            os.close(stage_fd)
        os.close(root_fd)
