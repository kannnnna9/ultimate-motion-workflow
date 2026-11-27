"""Validation for job-owned HTML compositions and pinned media."""
from __future__ import annotations

import hashlib
import json
import math
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
MAX_AUDIO_TRACKS = 8
MAX_AUDIO_TRACK_BYTES = 20 * 1024 * 1024
MAX_PINNED_AUDIO_BYTES = 80 * 1024 * 1024
MAX_AUDIO_DURATION_MS = 600_000
MAX_SPEECH_ANCHORS = 32
MAX_SPEECH_ANCHOR_OFFSET_MS = 1_000
_ASSET_ID = re.compile(r"[a-z][a-z0-9_-]{0,47}\Z")
_AUDIO_TRACK_ID = re.compile(r"[a-z][a-z0-9_-]{0,47}\Z")
_AUDIO_BLOB_REF = re.compile(r"[A-Za-z0-9_-]{16}\Z")
_SPEECH_ANCHOR_ID = re.compile(r"[a-z][a-z0-9_-]{0,47}\Z")
_SPEECH_WORD_ID = re.compile(r"w[0-9]{5}\Z")
_SPEECH_ACTION_ID = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
_AUDIO_FORMATS = {
    ".mp3": {"media_type": "audio/mpeg", "source_format": "mp3", "format_names": {"mp3"}, "codecs": {"mp3"}},
    ".ogg": {"media_type": "audio/ogg", "source_format": "ogg", "format_names": {"ogg"}, "codecs": {"opus", "vorbis"}},
    ".opus": {"media_type": "audio/ogg", "source_format": "ogg", "format_names": {"ogg"}, "codecs": {"opus"}},
    ".wav": {"media_type": "audio/wav", "source_format": "wav", "format_names": {"wav"},
             "codecs": {"pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le"}},
    ".m4a": {"media_type": "audio/mp4", "source_format": "m4a", "format_names": {"mov", "mp4", "m4a"},
             "codecs": {"aac", "alac"}},
    ".aac": {"media_type": "audio/aac", "source_format": "aac", "format_names": {"aac"}, "codecs": {"aac"}},
    ".flac": {"media_type": "audio/flac", "source_format": "flac", "format_names": {"flac"}, "codecs": {"flac"}},
    ".webm": {"media_type": "audio/webm", "source_format": "webm", "format_names": {"webm"},
              "codecs": {"opus", "vorbis"}},
}

COMPOSITION_SCHEMA = {
    "type": "object",
    "properties": {
        "html": {"type": "string", "maxLength": MAX_COMPOSITION_BYTES},
        "width": {"type": "integer", "const": 1080},
        "height": {"type": "integer", "const": 1920},
        "fps": {"type": "integer", "const": 30},
        "duration_frames": {"type": "integer", "minimum": 1, "maximum": MAX_DURATION_FRAMES},
        "audio_enabled": {"type": "boolean"},
        "subtitles_enabled": {"type": "boolean"},
        "subtitle_mode": {"type": "string", "enum": ["none", "ass", "web"]},
        "subtitle_effect": {"type": ["string", "null"], "enum": ["karaoke", None]},
        "ass_style": {
            "type": "object",
            "properties": {
                "font_name": {"type": "string", "maxLength": 64},
                "font_size": {"type": "integer", "minimum": 18, "maximum": 112},
                "bold": {"type": "boolean"},
                "outline": {"type": "number", "minimum": 0, "maximum": 8},
                "shadow": {"type": "number", "minimum": 0, "maximum": 8},
                "margin_l": {"type": "integer", "minimum": 0, "maximum": 1080},
                "margin_r": {"type": "integer", "minimum": 0, "maximum": 1080},
                "margin_v": {"type": "integer", "minimum": 0, "maximum": 1920},
                "max_words_per_cue": {"type": "integer", "minimum": 1, "maximum": 8},
                "anchor": {"type": "string", "const": "bottom-center"},
            },
            "required": ["font_name", "font_size", "bold", "outline", "shadow", "margin_l",
                         "margin_r", "margin_v", "max_words_per_cue", "anchor"],
            "additionalProperties": False,
        },
        "draws_speech_text": {"type": "boolean"},
        "speech_anchors": {
            "type": "array",
            "maxItems": MAX_SPEECH_ANCHORS,
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "pattern": "^[a-z][a-z0-9_-]{0,47}$"},
                    "word_id": {"type": "string", "pattern": "^w[0-9]{5}$"},
                    "action_id": {"type": "string", "pattern": "^[a-z][a-z0-9_-]{0,63}$"},
                    "offset_ms": {"type": "integer", "minimum": -MAX_SPEECH_ANCHOR_OFFSET_MS,
                                   "maximum": MAX_SPEECH_ANCHOR_OFFSET_MS},
                },
                "required": ["id", "word_id", "action_id", "offset_ms"],
                "additionalProperties": False,
            },
        },
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
        "audio_tracks": {
            "type": "array",
            "maxItems": MAX_AUDIO_TRACKS,
            "items": {
                "type": "object",
                "properties": {
                    "track_id": {"type": "string", "pattern": "^[a-z][a-z0-9_-]{0,47}$"},
                    "blob_ref": {"type": "string", "pattern": "^[A-Za-z0-9_-]{16}$"},
                    "kind": {"type": "string", "enum": ["voice", "music", "sfx", "ambience"]},
                    "offset_ms": {"type": "integer", "minimum": 0, "maximum": 59999},
                    "trim_start_ms": {"type": "integer", "minimum": 0, "maximum": MAX_AUDIO_DURATION_MS},
                    "trim_end_ms": {"type": "integer", "minimum": 1, "maximum": MAX_AUDIO_DURATION_MS},
                    "gain_db": {"type": "number", "minimum": -60, "maximum": 6},
                    "fade_in_ms": {"type": "integer", "minimum": 0, "maximum": MAX_AUDIO_DURATION_MS},
                    "fade_out_ms": {"type": "integer", "minimum": 0, "maximum": MAX_AUDIO_DURATION_MS},
                },
                "required": ["track_id", "blob_ref", "kind", "offset_ms", "trim_start_ms", "trim_end_ms",
                             "gain_db", "fade_in_ms", "fade_out_ms"],
                "additionalProperties": False,
            },
        },
    },
    "required": sorted(_COMPOSITION_FIELDS),
    "additionalProperties": False,
}


def validate_audio_track_references(value: object, *, video_duration_ms: int) -> list[dict]:
    if not isinstance(value, list):
        raise DomainError("INVALID_AUDIO_TRACK", "audio_tracks must be a list")
    if len(value) > MAX_AUDIO_TRACKS:
        raise DomainError("AUDIO_TRACK_LIMIT_EXCEEDED", "audio_tracks supports at most eight tracks")
    if not value:
        raise DomainError("INVALID_AUDIO_TRACK", "audio_tracks cannot be empty when enabled")
    fields = {
        "track_id", "blob_ref", "kind", "offset_ms", "trim_start_ms", "trim_end_ms",
        "gain_db", "fade_in_ms", "fade_out_ms",
    }
    result = []
    seen_ids = set()
    for track in value:
        if not isinstance(track, dict) or set(track) != fields:
            raise DomainError("INVALID_AUDIO_TRACK", "audio track reference has an unexpected shape")
        track_id, blob_ref, kind = track.get("track_id"), track.get("blob_ref"), track.get("kind")
        if (not isinstance(track_id, str) or not _AUDIO_TRACK_ID.fullmatch(track_id)
                or not isinstance(blob_ref, str) or not _AUDIO_BLOB_REF.fullmatch(blob_ref)
                or not isinstance(kind, str) or kind not in {"voice", "music", "sfx", "ambience"}
                or track_id in seen_ids):
            raise DomainError("INVALID_AUDIO_TRACK", "audio track identity, blob reference, or kind is invalid")
        offset = track.get("offset_ms")
        trim_start, trim_end = track.get("trim_start_ms"), track.get("trim_end_ms")
        gain = track.get("gain_db")
        fade_in, fade_out = track.get("fade_in_ms"), track.get("fade_out_ms")
        if type(gain) is int:
            gain_valid = -60 <= gain <= 6
        elif type(gain) is float:
            gain_valid = math.isfinite(gain) and -60 <= gain <= 6
        else:
            gain_valid = False
        if (type(offset) is not int or not 0 <= offset < video_duration_ms
                or type(trim_start) is not int or type(trim_end) is not int
                or not 0 <= trim_start < trim_end <= MAX_AUDIO_DURATION_MS
                or not gain_valid
                or type(fade_in) is not int or type(fade_out) is not int
                or not 0 <= fade_in <= MAX_AUDIO_DURATION_MS
                or not 0 <= fade_out <= MAX_AUDIO_DURATION_MS
                or fade_in + fade_out > trim_end - trim_start):
            raise DomainError("INVALID_AUDIO_TRACK", "audio timing, gain, or fade bounds are invalid")
        seen_ids.add(track_id)
        result.append(dict(track))
    return result


def validate_speech_anchor_specs(value: object) -> list[dict]:
    if not isinstance(value, list):
        raise DomainError("INVALID_SPEECH_ANCHOR", "speech_anchors must be a list")
    if len(value) > MAX_SPEECH_ANCHORS:
        raise DomainError("SPEECH_ANCHOR_LIMIT_EXCEEDED", "at most 32 speech anchors are allowed")
    fields = {"id", "word_id", "action_id", "offset_ms"}
    seen: set[str] = set()
    result = []
    for spec in value:
        if not isinstance(spec, dict) or set(spec) != fields:
            raise DomainError("INVALID_SPEECH_ANCHOR", "anchor must contain exactly id, word_id, action_id, and offset_ms")
        anchor_id, word_id, action_id, offset = (
            spec.get("id"), spec.get("word_id"), spec.get("action_id"), spec.get("offset_ms"),
        )
        if (not isinstance(anchor_id, str) or not _SPEECH_ANCHOR_ID.fullmatch(anchor_id)
                or anchor_id in seen or not isinstance(word_id, str) or not _SPEECH_WORD_ID.fullmatch(word_id)
                or not isinstance(action_id, str) or not _SPEECH_ACTION_ID.fullmatch(action_id)
                or type(offset) is not int or not -MAX_SPEECH_ANCHOR_OFFSET_MS <= offset <= MAX_SPEECH_ANCHOR_OFFSET_MS):
            raise DomainError("INVALID_SPEECH_ANCHOR", "anchor ID, word ID, action ID, or offset is invalid")
        seen.add(anchor_id)
        result.append({"id": anchor_id, "word_id": word_id, "action_id": action_id, "offset_ms": offset})
    return result


def validate_composition(value: object) -> dict:
    if (not isinstance(value, dict) or not _COMPOSITION_FIELDS <= value.keys()
            or value.keys() - (_COMPOSITION_FIELDS | {
                "assets", "audio_tracks", "subtitle_mode", "subtitle_effect", "ass_style",
                "draws_speech_text", "speech_anchors",
            })):
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

    audio_enabled = value["audio_enabled"]
    if type(audio_enabled) is not bool:
        raise DomainError("INVALID_COMPOSITION", "audio_enabled must be boolean")
    subtitle_enabled = value["subtitles_enabled"]
    if type(subtitle_enabled) is not bool:
        raise DomainError("INVALID_COMPOSITION", "subtitles_enabled must be boolean")
    if "subtitle_mode" not in value:
        if subtitle_enabled:
            raise DomainError("INVALID_COMPOSITION", "subtitle_mode is required when subtitles are enabled")
        subtitle_mode = "none"
    else:
        subtitle_mode = value["subtitle_mode"]
        if not isinstance(subtitle_mode, str) or subtitle_mode not in {"none", "ass", "web"}:
            raise DomainError("INVALID_COMPOSITION", "subtitle_mode must be none, ass, or web")
        if subtitle_enabled is not (subtitle_mode != "none"):
            raise DomainError("INVALID_COMPOSITION", "subtitles_enabled must match subtitle_mode")
    subtitle_effect = value.get("subtitle_effect")
    if subtitle_mode == "web":
        if subtitle_effect != "karaoke":
            raise DomainError("INVALID_COMPOSITION", "web subtitle_mode requires subtitle_effect=karaoke")
    elif subtitle_effect is not None:
        raise DomainError("INVALID_COMPOSITION", "subtitle_effect is only valid with web subtitle_mode")
    ass_style = None
    if "ass_style" in value:
        if subtitle_mode != "ass":
            raise DomainError("INVALID_COMPOSITION", "ass_style is only valid with ASS subtitles")
        from .subtitles import _validate_style

        try:
            ass_style = _validate_style(value["ass_style"], width=1080, height=1920)
        except ValueError as exc:
            raise DomainError("INVALID_ASS_STYLE", str(exc)) from exc
    video_duration_ms = frames * 1000 // 30
    raw_audio_tracks = value.get("audio_tracks", [])
    if not audio_enabled:
        if raw_audio_tracks:
            raise DomainError("INVALID_COMPOSITION", "audio tracks require audio_enabled=true")
        audio_refs = []
    else:
        if not raw_audio_tracks:
            raise DomainError("INVALID_COMPOSITION", "audio_enabled=true requires at least one track")
        audio_refs = validate_audio_track_references(
            raw_audio_tracks, video_duration_ms=video_duration_ms,
        )
    draws_speech_text = value.get("draws_speech_text", False)
    if type(draws_speech_text) is not bool:
        raise DomainError("INVALID_COMPOSITION", "draws_speech_text must be boolean")
    speech_anchors = validate_speech_anchor_specs(value.get("speech_anchors", []))
    if subtitle_mode in {"ass", "web"} and draws_speech_text:
        raise DomainError("DUPLICATE_SUBTITLE_OWNER", "subtitle renderer and in-scene speech text cannot both be enabled")
    if subtitle_mode in {"ass", "web"} and (not audio_enabled or not any(track["kind"] == "voice" for track in audio_refs)):
        raise DomainError("INVALID_COMPOSITION", "speech subtitles require a voice audio track")
    if speech_anchors and (not audio_enabled or not any(track["kind"] == "voice" for track in audio_refs)):
        raise DomainError("SPEECH_ANCHOR_REQUIRES_VOICE", "speech anchors require an enabled voice track")

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
    result["subtitle_mode"] = subtitle_mode
    result["subtitle_effect"] = subtitle_effect
    if ass_style is not None:
        result["ass_style"] = ass_style
    result["draws_speech_text"] = draws_speech_text
    result["speech_anchors"] = speech_anchors
    if asset_refs:
        result["assets"] = asset_refs
    if audio_refs:
        result["audio_tracks"] = audio_refs
    return result


_AUDIO_LIBRARY_FIELDS = {
    "track_id", "blob_ref", "kind", "sha256", "size_bytes", "extension", "source_format", "media_type",
    "codec", "duration_ms", "sample_rate", "channels", "provenance", "rights_status",
}


def validate_audio_library(value: object) -> list[dict]:
    if not isinstance(value, list):
        raise DomainError("INVALID_AUDIO_TRACK", "audio library must be a list")
    if len(value) > MAX_AUDIO_TRACKS:
        raise DomainError("AUDIO_TRACK_LIMIT_EXCEEDED", "audio library supports at most eight tracks")
    result = []
    seen_ids = set()
    seen_refs = set()
    total_bytes = 0
    for record in value:
        if not isinstance(record, dict) or set(record) != _AUDIO_LIBRARY_FIELDS:
            raise DomainError("INVALID_AUDIO_TRACK", "audio library record has an unexpected shape")
        track_id, blob_ref = record["track_id"], record["blob_ref"]
        extension = record["extension"]
        audio_format = _AUDIO_FORMATS.get(extension) if isinstance(extension, str) else None
        if (not isinstance(track_id, str) or not _AUDIO_TRACK_ID.fullmatch(track_id)
                or not isinstance(blob_ref, str) or not _AUDIO_BLOB_REF.fullmatch(blob_ref)
                or not isinstance(record["kind"], str)
                or record["kind"] not in {"voice", "music", "sfx", "ambience"}
                or track_id in seen_ids or blob_ref in seen_refs or audio_format is None
                or record["media_type"] != audio_format["media_type"]
                or record["source_format"] != audio_format["source_format"]
                or not isinstance(record["codec"], str) or record["codec"] not in audio_format["codecs"]):
            raise DomainError("INVALID_AUDIO_TRACK", "audio library identity or media format is invalid")
        if (not isinstance(record["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", record["sha256"])
                or type(record["size_bytes"]) is not int
                or not 1 <= record["size_bytes"] <= MAX_AUDIO_TRACK_BYTES
                or type(record["duration_ms"]) is not int
                or not 1 <= record["duration_ms"] <= MAX_AUDIO_DURATION_MS
                or type(record["sample_rate"]) is not int or record["sample_rate"] < 1
                or type(record["channels"]) is not int or record["channels"] < 1):
            raise DomainError("INVALID_AUDIO_TRACK", "audio library probe metadata is invalid")
        provenance = record["provenance"]
        if (not isinstance(provenance, str) or not provenance.strip() or provenance != provenance.strip()
                or len(provenance) > 1024 or record["rights_status"] != "unverified"):
            raise DomainError("INVALID_AUDIO_TRACK", "audio provenance or rights status is invalid")
        total_bytes += record["size_bytes"]
        if total_bytes > MAX_PINNED_AUDIO_BYTES:
            raise DomainError("AUDIO_BUNDLE_TOO_LARGE", "audio library exceeds 80 MiB")
        seen_ids.add(track_id)
        seen_refs.add(blob_ref)
        result.append(dict(record))
    return result


_AUDIO_MANIFEST_FIELDS = {
    "track_id", "blob_ref", "kind", "path", "sha256", "size_bytes", "extension", "source_format",
    "media_type", "codec", "duration_ms", "sample_rate", "channels", "offset_ms", "trim_start_ms",
    "trim_end_ms", "gain_db", "fade_in_ms", "fade_out_ms", "provenance", "rights_status",
}


def validate_audio_manifest(value: object, *, video_duration_ms: int) -> list[dict]:
    if not isinstance(value, list):
        raise DomainError("INVALID_AUDIO_TRACK", "audio manifest must be a list")
    if len(value) > MAX_AUDIO_TRACKS:
        raise DomainError("AUDIO_TRACK_LIMIT_EXCEEDED", "audio manifest supports at most eight tracks")
    if not value:
        raise DomainError("INVALID_AUDIO_TRACK", "audio manifest cannot be empty when enabled")
    total_bytes = 0
    seen_ids = set()
    result = []
    ref_fields = {
        "track_id", "blob_ref", "kind", "offset_ms", "trim_start_ms", "trim_end_ms",
        "gain_db", "fade_in_ms", "fade_out_ms",
    }
    for track in value:
        if not isinstance(track, dict) or set(track) != _AUDIO_MANIFEST_FIELDS:
            raise DomainError("INVALID_AUDIO_TRACK", "audio manifest record has an unexpected shape")
        reference = {key: track[key] for key in ref_fields if key in track}
        validate_audio_track_references([reference], video_duration_ms=video_duration_ms)
        track_id, extension = track["track_id"], track["extension"]
        audio_format = _AUDIO_FORMATS.get(extension) if isinstance(extension, str) else None
        if (audio_format is None or track["media_type"] != audio_format["media_type"]
                or track["source_format"] != audio_format["source_format"]
                or not isinstance(track["codec"], str) or track["codec"] not in audio_format["codecs"]):
            raise DomainError("INVALID_AUDIO_TRACK", "audio codec, extension, and media type disagree")
        if (type(track["duration_ms"]) is not int or not 1 <= track["duration_ms"] <= MAX_AUDIO_DURATION_MS
                or track["trim_end_ms"] > track["duration_ms"]):
            raise DomainError("INVALID_AUDIO_TRACK", "audio trims exceed the probed source duration")
        if (type(track["size_bytes"]) is not int or not 1 <= track["size_bytes"] <= MAX_AUDIO_TRACK_BYTES
                or type(track["sample_rate"]) is not int or track["sample_rate"] < 1
                or type(track["channels"]) is not int or track["channels"] < 1):
            raise DomainError("INVALID_AUDIO_TRACK", "audio stream metadata is invalid")
        if (not isinstance(track["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", track["sha256"])
                or track["path"] != f"audio/{track_id}{extension}"):
            raise DomainError("INVALID_AUDIO_TRACK", "audio hash or canonical path is invalid")
        provenance, rights_status = track["provenance"], track["rights_status"]
        if (not isinstance(provenance, str) or not provenance.strip() or provenance != provenance.strip()
                or len(provenance) > 1024 or rights_status != "unverified"):
            raise DomainError("INVALID_AUDIO_TRACK", "audio provenance or rights status is invalid")
        if track_id in seen_ids:
            raise DomainError("INVALID_AUDIO_TRACK", "audio track IDs must be unique")
        seen_ids.add(track_id)
        total_bytes += track["size_bytes"]
        if total_bytes > MAX_PINNED_AUDIO_BYTES:
            raise DomainError("AUDIO_BUNDLE_TOO_LARGE", "pinned audio exceeds 80 MiB")
        result.append(dict(track))
    return result


def read_pinned_audio(composition_root: Path, record: dict) -> bytes:
    validated = validate_audio_manifest([record], video_duration_ms=60_000)[0]
    root_fd = audio_fd = None
    try:
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        root_fd = os.open(composition_root, directory_flags)
        audio_fd = os.open("audio", directory_flags, dir_fd=root_fd)
        file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        file_fd = os.open(Path(validated["path"]).name, file_flags, dir_fd=audio_fd)
        with os.fdopen(file_fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1
                    or info.st_mode & 0o077 or info.st_size != validated["size_bytes"]):
                raise DomainError("AUDIO_INTEGRITY", "pinned audio is not a private regular file")
            data = stream.read(MAX_AUDIO_TRACK_BYTES + 1)
        if len(data) != validated["size_bytes"] or hashlib.sha256(data).hexdigest() != validated["sha256"]:
            raise DomainError("AUDIO_INTEGRITY", "pinned audio does not match its revision hash")
        return data
    except DomainError:
        raise
    except OSError as exc:
        raise DomainError("AUDIO_INTEGRITY", "pinned audio is missing or unsafe") from exc
    finally:
        if audio_fd is not None:
            os.close(audio_fd)
        if root_fd is not None:
            os.close(root_fd)


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
