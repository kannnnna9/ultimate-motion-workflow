"""Validation for job-owned, silent M1 HTML compositions."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path

from .jobs import DomainError

MAX_COMPOSITION_BYTES = 1_048_576
MAX_PROJECT_BYTES = 8 * 1024 * 1024
MAX_REVISION_BYTES = 10 * 1024 * 1024
MAX_DURATION_FRAMES = 1_800
_COMPOSITION_FIELDS = {
    "html",
    "width",
    "height",
    "fps",
    "duration_frames",
    "audio_enabled",
    "subtitles_enabled",
}


_MAX_ASSET_REFERENCES = 256
_MAX_ASSET_BYTES = 10 * 1024 * 1024
MAX_PINNED_ASSET_BYTES = 40 * 1024 * 1024
_ASSET_ID = re.compile(r"[a-z][a-z0-9_-]{0,47}\Z")

COMPOSITION_SCHEMA = {
    "type": "object",
    "properties": {
        "html": {"type": "string", "maxLength": MAX_COMPOSITION_BYTES},
        "width": {"type": "integer", "const": 1080},
        "height": {"type": "integer", "const": 1920},
        "fps": {"type": "integer", "const": 30},
        "duration_frames": {"type": "integer", "minimum": 1, "maximum": MAX_DURATION_FRAMES},
        "audio_enabled": {"type": "boolean", "const": False},
        "subtitles_enabled": {"type": "boolean", "const": False},
        "assets": {
            "type": "array",
            "maxItems": _MAX_ASSET_REFERENCES,
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "pattern": "^[a-z][a-z0-9_-]{0,47}$"},
                    "version": {"type": "integer", "minimum": 1},
                },
                "required": ["id", "version"],
                "additionalProperties": False,
            },
        },
    },
    "required": sorted(_COMPOSITION_FIELDS),
    "additionalProperties": False,
}


def validate_composition(value: object) -> dict:
    if (not isinstance(value, dict) or not _COMPOSITION_FIELDS <= value.keys()
            or value.keys() - (_COMPOSITION_FIELDS | {"assets"})):
        raise DomainError("INVALID_COMPOSITION", "expected only HTML and pinned composition metadata")

    html = value["html"]
    if isinstance(html, bytes):
        try:
            html.decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            raise DomainError("INVALID_HTML_ENCODING", "HTML must be UTF-8") from exc
        raise DomainError("INVALID_COMPOSITION", "HTML must be a Unicode string")
    if not isinstance(html, str) or not html.strip():
        raise DomainError("INVALID_COMPOSITION", "HTML must be non-empty text")
    try:
        html_bytes = html.encode("utf-8", "strict")
    except UnicodeEncodeError as exc:
        raise DomainError("INVALID_HTML_ENCODING", "HTML must be UTF-8") from exc
    if len(html_bytes) > MAX_COMPOSITION_BYTES:
        raise DomainError("COMPOSITION_TOO_LARGE", f"HTML exceeds {MAX_COMPOSITION_BYTES} UTF-8 bytes")

    for key, expected in (("width", 1080), ("height", 1920), ("fps", 30)):
        if type(value[key]) is not int or value[key] != expected:
            raise DomainError("INVALID_COMPOSITION", f"{key} must be {expected}")

    frames = value["duration_frames"]
    if type(frames) is not int or frames <= 0:
        raise DomainError("INVALID_DURATION", "duration_frames must be a positive integer")
    if frames > MAX_DURATION_FRAMES:
        raise DomainError("DURATION_LIMIT_EXCEEDED", "M1 compositions are limited to 60 seconds")

    for key in ("audio_enabled", "subtitles_enabled"):
        if type(value[key]) is not bool or value[key] is not False:
            raise DomainError("INVALID_COMPOSITION", f"{key} must be false for the M1 silent route")

    assets = value.get("assets", [])
    if not isinstance(assets, list) or len(assets) > _MAX_ASSET_REFERENCES:
        raise DomainError("INVALID_COMPOSITION", "assets must be a bounded list of version references")
    asset_refs = []
    seen_ids = set()
    for reference in assets:
        if (not isinstance(reference, dict) or set(reference) != {"id", "version"}
                or not isinstance(reference.get("id"), str)
                or not _ASSET_ID.fullmatch(reference["id"])
                or type(reference.get("version")) is not int
                or not 0 < reference["version"] <= 2_147_483_647
                or reference["id"] in seen_ids):
            raise DomainError("INVALID_COMPOSITION", "assets require unique IDs and positive versions only")
        seen_ids.add(reference["id"])
        asset_refs.append({"id": reference["id"], "version": reference["version"]})

    result = {key: value[key] for key in (
        "html", "width", "height", "fps", "duration_frames", "audio_enabled", "subtitles_enabled"
    )}
    if asset_refs:
        result["assets"] = asset_refs
    return result


_ASSET_MANIFEST_FIELDS = {
    "id", "version", "sha256", "type", "format", "media_type", "size_bytes", "width", "height",
    "source", "provenance", "license", "seed", "parameters", "locked", "path",
}
_MANIFEST_FORMATS = {
    "svg": ("vector", "image/svg+xml", ".svg"),
    "png": ("raster", "image/png", ".png"),
    "jpeg": ("raster", "image/jpeg", ".jpeg"),
}


def validate_asset_manifest(value: object) -> list[dict]:
    if not isinstance(value, list) or len(value) > _MAX_ASSET_REFERENCES:
        raise DomainError("INVALID_ASSET_MANIFEST", "asset manifest must be a bounded list")
    result = []
    seen_ids = set()
    total_bytes = 0
    for record in value:
        if not isinstance(record, dict) or set(record) != _ASSET_MANIFEST_FIELDS:
            raise DomainError("INVALID_ASSET_MANIFEST", "asset manifest record has an unexpected shape")
        asset_id = record.get("id")
        version = record.get("version")
        asset_format = record.get("format")
        if (not isinstance(asset_id, str) or not _ASSET_ID.fullmatch(asset_id)
                or type(version) is not int or not 0 < version <= 2_147_483_647
                or asset_id in seen_ids or not isinstance(asset_format, str)):
            raise DomainError("INVALID_ASSET_MANIFEST", "asset identity or version is invalid")
        expected_format = _MANIFEST_FORMATS.get(asset_format)
        if expected_format is None or (record.get("type"), record.get("media_type")) != expected_format[:2]:
            raise DomainError("INVALID_ASSET_MANIFEST", "asset format and media type disagree")
        if (not isinstance(record.get("sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", record["sha256"])):
            raise DomainError("INVALID_ASSET_MANIFEST", "asset SHA-256 is invalid")
        size_bytes = record.get("size_bytes")
        width, height = record.get("width"), record.get("height")
        if (type(size_bytes) is not int or not 1 <= size_bytes <= _MAX_ASSET_BYTES
                or type(width) is not int or type(height) is not int
                or width < 1 or height < 1 or width * height > 16_000_000):
            raise DomainError("INVALID_ASSET_MANIFEST", "asset dimensions or byte size are outside bounds")
        if asset_format == "svg" and (width > 4096 or height > 4096):
            raise DomainError("INVALID_ASSET_MANIFEST", "SVG manifest dimensions exceed bounds")
        expected_path = f"assets/{asset_id}-v{version}{expected_format[2]}"
        if record.get("path") != expected_path:
            raise DomainError("INVALID_ASSET_MANIFEST", "asset path must be its canonical relative name")
        for key, limit in (("source", 256), ("provenance", 1024), ("license", 256)):
            value_text = record.get(key)
            if (not isinstance(value_text, str) or not value_text.strip()
                    or value_text != value_text.strip() or len(value_text) > limit):
                raise DomainError("INVALID_ASSET_MANIFEST", f"asset {key} is invalid")
        seed, parameters = record.get("seed"), record.get("parameters")
        if seed is not None and type(seed) is not int:
            raise DomainError("INVALID_ASSET_MANIFEST", "asset seed must be an integer or null")
        if parameters is not None and not isinstance(parameters, dict):
            raise DomainError("INVALID_ASSET_MANIFEST", "asset parameters must be an object or null")
        if asset_format != "svg" and (seed is not None or parameters is not None):
            raise DomainError("INVALID_ASSET_MANIFEST", "raster assets cannot declare procedural parameters")
        if parameters is not None:
            try:
                encoded_parameters = json.dumps(
                    parameters, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
                ).encode("utf-8")
            except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as exc:
                raise DomainError("INVALID_ASSET_MANIFEST", "asset parameters are not bounded JSON") from exc
            if len(encoded_parameters) > 16 * 1024:
                raise DomainError("INVALID_ASSET_MANIFEST", "asset parameters exceed 16 KiB")
        if type(record.get("locked")) is not bool:
            raise DomainError("INVALID_ASSET_MANIFEST", "asset lock state must be boolean")
        total_bytes += size_bytes
        if total_bytes > MAX_PINNED_ASSET_BYTES:
            raise DomainError("ASSET_BUNDLE_TOO_LARGE", "pinned assets exceed 40 MiB")
        seen_ids.add(asset_id)
        result.append(dict(record))
    return result


def read_pinned_asset(composition_root: Path, record: dict) -> bytes:
    validated = validate_asset_manifest([record])[0]
    root_fd = assets_fd = None
    try:
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        root_fd = os.open(composition_root, directory_flags)
        assets_fd = os.open("assets", directory_flags, dir_fd=root_fd)
        file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        file_fd = os.open(Path(validated["path"]).name, file_flags, dir_fd=assets_fd)
        with os.fdopen(file_fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1
                    or info.st_mode & 0o077 or info.st_size != validated["size_bytes"]):
                raise DomainError("ASSET_INTEGRITY", "pinned asset is not a private regular file")
            data = stream.read(_MAX_ASSET_BYTES + 1)
        if len(data) != validated["size_bytes"] or hashlib.sha256(data).hexdigest() != validated["sha256"]:
            raise DomainError("ASSET_INTEGRITY", "pinned asset does not match its revision hash")
        return data
    except DomainError:
        raise
    except OSError as exc:
        raise DomainError("ASSET_INTEGRITY", "pinned asset is missing or unsafe") from exc
    finally:
        if assets_fd is not None:
            os.close(assets_fd)
        if root_fd is not None:
            os.close(root_fd)
