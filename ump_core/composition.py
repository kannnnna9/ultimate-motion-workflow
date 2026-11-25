"""Validation for job-owned, silent M1 HTML compositions."""
from __future__ import annotations

from .jobs import DomainError

MAX_COMPOSITION_BYTES = 1_048_576
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
    },
    "required": sorted(_COMPOSITION_FIELDS),
    "additionalProperties": False,
}


def validate_composition(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != _COMPOSITION_FIELDS:
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

    return {key: value[key] for key in (
        "html", "width", "height", "fps", "duration_frames", "audio_enabled", "subtitles_enabled"
    )}
