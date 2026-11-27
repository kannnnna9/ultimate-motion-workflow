"""Effective-input impact and lock-scope hashing for immutable job revisions."""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

from .jobs import DomainError

_DEPENDENCIES = (
    "voice_bytes",
    "alignment",
    "anchor",
    "caption",
    "sidecar",
    "mux",
    "subtitle_qa",
    "audio_qa",
    "composition",
    "visual_qa",
    "review",
)
_OUTPUTS = frozenset(_DEPENDENCIES) - {"voice_bytes"}
_ALL_INVALID = _OUTPUTS
_LOCK_SCOPE = re.compile(r"[a-z][a-z0-9_.-]{0,127}\Z")
_MISSING = object()


def _encode(value: Any) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as exc:
        raise DomainError("INVALID_REVISION_INPUT", "effective input must be bounded JSON") from exc


def _composition_metadata(value: Mapping[str, Any]) -> Mapping[str, Any]:
    composition = value.get("composition")
    if isinstance(composition, Mapping):
        metadata = composition.get("metadata")
        if isinstance(metadata, Mapping):
            return metadata
        return composition
    return value


def _needs_timing(value: Mapping[str, Any]) -> bool:
    mode = value.get("subtitle_mode", "none")
    anchors = value.get("speech_anchors", [])
    return mode in {"ass", "web"} or bool(anchors)


def _normalized(value: Any) -> Any:
    if isinstance(value, Mapping):
        result = {key: _normalized(item) for key, item in value.items()}
        if result.get("subtitle_mode") == "ass" and "ass_style" not in result:
            from .subtitles import MOBILE_ASS_STYLE

            result["ass_style"] = dict(MOBILE_ASS_STYLE)
        return result
    if isinstance(value, list):
        return [_normalized(item) for item in value]
    return value


def _effective_inputs(value: dict) -> dict:
    if not isinstance(value, dict):
        raise TypeError("revision inputs must be dictionaries")
    normalized = _normalized(value)
    if not isinstance(normalized, dict):
        raise TypeError("revision inputs must be dictionaries")
    known_job_fields = {
        "brief", "mode", "requirements", "stage", "origin", "status", "error",
        "sources", "audio_library", "composition", "naskah", "fixture", "brand",
        "cue", "cues", "voice_asset", "voice_words", "storyboard", "style_id",
        "assets", "audio_tracks", "subtitle_mode", "subtitle_effect", "ass_style",
        "draws_speech_text", "speech_anchors", "html", "width", "height", "fps",
        "duration_frames", "audio_enabled", "subtitles_enabled", "approved_naskah_sha256",
    }
    if isinstance(normalized.get("composition"), dict):
        composition = normalized["composition"]
        result = {
            "composition": {key: item for key, item in composition.items() if key != "project_sha256"},
        }
        metadata = _composition_metadata(normalized)
        if _needs_timing(metadata) and "naskah" in normalized:
            result["naskah"] = normalized["naskah"]
        unknown = set(normalized) - known_job_fields
        if unknown:
            result["__unknown__"] = {key: normalized[key] for key in sorted(unknown)}
        return result

    legacy_fields = {
        "fixture", "brand", "naskah", "cue", "cues", "voice_asset", "voice_words",
        "storyboard", "style_id", "assets", "audio_tracks", "subtitle_mode",
        "subtitle_effect", "ass_style", "draws_speech_text", "speech_anchors",
        "html", "width", "height", "fps", "duration_frames", "audio_enabled",
        "subtitles_enabled", "approved_naskah_sha256",
    }
    if set(normalized) & {"brief", "mode", "requirements", "origin", "stage", "status"}:
        result = {key: normalized[key] for key in sorted(set(normalized) & legacy_fields)}
        unknown = set(normalized) - known_job_fields
        if unknown:
            result["__unknown__"] = {key: normalized[key] for key in sorted(unknown)}
        return result
    return normalized


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, Mapping) and value:
        result: dict[str, Any] = {}
        for key in sorted(value):
            path = f"{prefix}.{key}" if prefix else str(key)
            result.update(_flatten(value[key], path))
        return result
    return {prefix: value}


def _voice_signature(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def visit(item: Any) -> None:
        if isinstance(item, Mapping):
            tracks = item.get("audio_tracks")
            if isinstance(tracks, list):
                for track in tracks:
                    if isinstance(track, Mapping) and track.get("kind") == "voice":
                        found.append({key: track[key] for key in sorted(track)})
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    if isinstance(value, Mapping):
        for key in ("voice_asset", "voice_words"):
            if key in value:
                found.append({key: value[key]})
    return sorted(found, key=lambda record: _encode(record))


def _timing_signature(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    metadata = _composition_metadata(value)
    return _needs_timing(metadata)


def impact(before: dict, after: dict) -> dict:
    """Compare effective renderer inputs and list affected and reusable outputs."""
    before_effective = _effective_inputs(before)
    after_effective = _effective_inputs(after)
    before_flat = _flatten(before_effective)
    after_flat = _flatten(after_effective)
    changed = sorted(
        path for path in before_flat.keys() | after_flat.keys()
        if before_flat.get(path, _MISSING) != after_flat.get(path, _MISSING)
    )
    if not changed:
        return {"changed": [], "invalidate": [], "preserve": list(_DEPENDENCIES)}

    invalid: set[str] = set()
    unknown = False
    voice_changed = _voice_signature(before_effective) != _voice_signature(after_effective)
    timing_changed = _timing_signature(before_effective) != _timing_signature(after_effective)

    for path in changed:
        parts = path.lower().split(".")
        if "__unknown__" in parts:
            unknown = True
        elif "ass_style" in parts:
            invalid.update({"caption", "sidecar", "mux", "subtitle_qa", "review"})
        elif any(part in {"audio_tracks", "audio_enabled"} for part in parts):
            if voice_changed or timing_changed:
                invalid.update({"alignment", "anchor", "caption", "sidecar", "mux", "subtitle_qa", "audio_qa", "review"})
            else:
                invalid.update({"mux", "audio_qa", "review"})
        elif any(part in {"voice_asset", "voice_words", "naskah", "approved_naskah_sha256"} for part in parts):
            invalid.update({"alignment", "anchor", "caption", "sidecar", "mux", "subtitle_qa", "review"})
        elif any(part in {"speech_anchors", "anchor"} for part in parts):
            invalid.update({"anchor", "composition", "visual_qa", "mux", "review"})
        elif any(part in {"subtitle_mode", "subtitle_effect", "subtitles_enabled", "draws_speech_text"} for part in parts):
            invalid.update({"caption", "sidecar", "mux", "subtitle_qa", "review"})
            if timing_changed:
                invalid.update({"alignment", "anchor"})
        elif any(part in {"html", "html_sha256", "assets", "width", "height", "fps", "duration_frames"} for part in parts):
            invalid.update({"composition", "visual_qa", "mux", "review"})
            if any(part in {"fps", "duration_frames"} for part in parts):
                invalid.update({"alignment", "anchor", "caption", "sidecar", "subtitle_qa"})
        elif any(part in {"fixture", "brand", "style_id", "storyboard", "cue", "cues"} for part in parts):
            invalid.update({"composition", "visual_qa", "mux", "review"})
            if any(part in {"cue", "cues"} for part in parts):
                invalid.update({"alignment", "anchor", "caption", "sidecar", "subtitle_qa"})
        else:
            unknown = True

    if voice_changed:
        invalid.update({"alignment", "anchor", "caption", "sidecar", "mux", "subtitle_qa", "audio_qa", "review"})
    if unknown:
        invalid.update(_ALL_INVALID)
        preserve: list[str] = []
    else:
        preserve = [name for name in _DEPENDENCIES if name not in invalid]
        if voice_changed:
            preserve = [name for name in preserve if name != "voice_bytes"]
    return {"changed": changed, "invalidate": sorted(invalid), "preserve": preserve}


def scope_value(snapshot: dict, scope: str) -> Any:
    if not isinstance(snapshot, dict) or not isinstance(scope, str) or not _LOCK_SCOPE.fullmatch(scope):
        raise DomainError("INVALID_LOCK_SCOPE", "scope must be a bounded lowercase identifier")
    if scope == "voice":
        signature = _voice_signature(snapshot)
        if not signature:
            raise DomainError("INVALID_LOCK_SCOPE", "revision has no voice input to protect")
        return signature
    if scope == "ass_style":
        metadata = _composition_metadata(snapshot)
        if metadata.get("subtitle_mode") != "ass":
            raise DomainError("INVALID_LOCK_SCOPE", "revision has no ASS subtitle style to protect")
        if "ass_style" in metadata:
            return metadata["ass_style"]
        from .subtitles import MOBILE_ASS_STYLE

        return dict(MOBILE_ASS_STYLE)
    if scope == "composition":
        value = snapshot.get("composition")
        if not isinstance(value, dict):
            raise DomainError("INVALID_LOCK_SCOPE", "revision has no composition to protect")
        return value

    current: Any = snapshot
    for part in scope.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        else:
            raise DomainError("INVALID_LOCK_SCOPE", "scope does not identify a value in this revision")
    return current


def scope_hash(snapshot: dict, scope: str) -> str:
    return hashlib.sha256(_encode(scope_value(snapshot, scope))).hexdigest()
