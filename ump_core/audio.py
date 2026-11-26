from __future__ import annotations

import copy
import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any


TIMING_STATUSES = frozenset({"native", "aligned", "estimated", "unresolved"})
PROVIDERS = frozenset({"edge-tts", "whisperx", "faster-whisper"})
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_TIMELINE_KEYS = frozenset({
    "schema_version", "language", "audio_sha256", "provider", "model_id",
    "model_revision", "status", "duration_ms", "words",
})
_OPTIONAL_TIMELINE_KEYS = frozenset({
    "reason", "approved_transcript", "recognized_text", "recognized_words", "metrics",
})
_REQUIRED_WORD_KEYS = frozenset({
    "id", "text", "start_ms", "end_ms", "speaker_id", "timing_status",
})
_OPTIONAL_WORD_KEYS = frozenset({"score"})


def _integer(value: Any, field: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{field} must be at least {minimum}")
    return value


def _nonempty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _validate_words(words: Any, duration_ms: int, field: str) -> list[dict[str, Any]]:
    if not isinstance(words, list):
        raise ValueError(f"{field} must be a list")
    ids: set[str] = set()
    last_start_by_speaker: dict[str | None, int] = {}
    result: list[dict[str, Any]] = []
    for index, word in enumerate(words):
        prefix = f"{field}[{index}]"
        if not isinstance(word, Mapping):
            raise ValueError(f"{prefix} must be an object")
        keys = set(word)
        if not _REQUIRED_WORD_KEYS <= keys or keys - _REQUIRED_WORD_KEYS - _OPTIONAL_WORD_KEYS:
            raise ValueError(f"{prefix} has missing or unsupported fields")
        word_id = _nonempty_string(word["id"], f"{prefix}.id")
        if word_id in ids:
            raise ValueError(f"{prefix}.id is duplicated")
        ids.add(word_id)
        text = _nonempty_string(word["text"], f"{prefix}.text")
        start_ms = _integer(word["start_ms"], f"{prefix}.start_ms", minimum=0)
        end_ms = _integer(word["end_ms"], f"{prefix}.end_ms", minimum=0)
        if start_ms >= end_ms:
            raise ValueError(f"{prefix}.start_ms must be before end_ms")
        if end_ms > duration_ms:
            raise ValueError(f"{prefix}.end_ms exceeds audio duration")
        speaker_id = word["speaker_id"]
        if speaker_id is not None:
            speaker_id = _nonempty_string(speaker_id, f"{prefix}.speaker_id")
        previous = last_start_by_speaker.get(speaker_id)
        if previous is not None and start_ms < previous:
            raise ValueError(f"{prefix}.start_ms is not monotonic on its speaker track")
        last_start_by_speaker[speaker_id] = start_ms
        timing_status = word["timing_status"]
        if not isinstance(timing_status, str) or timing_status not in TIMING_STATUSES:
            raise ValueError(f"{prefix}.timing_status is unsupported")
        item = {
            "id": word_id,
            "text": text,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "speaker_id": speaker_id,
            "timing_status": timing_status,
        }
        if "score" in word:
            score = word["score"]
            if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= 1:
                raise ValueError(f"{prefix}.score must be finite and between 0 and 1")
            item["score"] = score
        result.append(item)
    return result


def validate_speech_timeline(
    doc: Mapping[str, Any], *, audio_sha256: str, duration_ms: int
) -> dict[str, Any]:
    """Validate and copy a schema-v1 word timeline bound to one audio file."""
    if not isinstance(doc, Mapping):
        raise ValueError("timeline must be an object")
    keys = set(doc)
    if not _REQUIRED_TIMELINE_KEYS <= keys or keys - _REQUIRED_TIMELINE_KEYS - _OPTIONAL_TIMELINE_KEYS:
        raise ValueError("timeline has missing or unsupported fields")
    schema_version = _integer(doc["schema_version"], "schema_version", minimum=1)
    if schema_version != 1:
        raise ValueError("schema_version must be 1")
    language = _nonempty_string(doc["language"], "language")
    if not re.fullmatch(r"[a-z]{2,3}(?:-[A-Z]{2})?", language):
        raise ValueError("language must be a canonical language tag")
    declared_hash = doc["audio_sha256"]
    if not isinstance(audio_sha256, str) or not _SHA256_RE.fullmatch(audio_sha256):
        raise ValueError("expected audio_sha256 must be lowercase SHA-256 hex")
    if not isinstance(declared_hash, str) or not _SHA256_RE.fullmatch(declared_hash):
        raise ValueError("timeline audio_sha256 must be lowercase SHA-256 hex")
    if declared_hash != audio_sha256:
        raise ValueError("timeline audio_sha256 does not match the source audio")
    provider = doc["provider"]
    if not isinstance(provider, str) or provider not in PROVIDERS:
        raise ValueError("provider is unsupported")
    model_id = doc["model_id"]
    model_revision = doc["model_revision"]
    for value, field in ((model_id, "model_id"), (model_revision, "model_revision")):
        if value is not None:
            _nonempty_string(value, field)
    status = doc["status"]
    if not isinstance(status, str) or status not in TIMING_STATUSES:
        raise ValueError("status is unsupported")
    declared_duration = _integer(doc["duration_ms"], "duration_ms", minimum=1)
    expected_duration = _integer(duration_ms, "expected duration_ms", minimum=1)
    if declared_duration != expected_duration:
        raise ValueError("timeline duration_ms does not match decoded audio duration")
    words = _validate_words(doc["words"], declared_duration, "words")
    if status == "unresolved":
        _nonempty_string(doc.get("reason"), "reason")
    elif not words:
        raise ValueError("resolved timeline must contain at least one word")
    elif "reason" in doc:
        raise ValueError("reason is only valid on an unresolved timeline")

    result: dict[str, Any] = {
        "schema_version": 1,
        "language": language,
        "audio_sha256": declared_hash,
        "provider": provider,
        "model_id": model_id,
        "model_revision": model_revision,
        "status": status,
        "duration_ms": declared_duration,
        "words": words,
    }
    if "reason" in doc:
        result["reason"] = doc["reason"]
    for key in ("approved_transcript", "recognized_text"):
        if key in doc:
            if not isinstance(doc[key], str):
                raise ValueError(f"{key} must be a string")
            result[key] = doc[key]
    if "recognized_words" in doc:
        result["recognized_words"] = _validate_words(doc["recognized_words"], declared_duration, "recognized_words")
    if "metrics" in doc:
        metrics = doc["metrics"]
        if not isinstance(metrics, Mapping):
            raise ValueError("metrics must be an object")
        result["metrics"] = copy.deepcopy(dict(metrics))
    return result


def _clamp_word_spans(recognized_words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep decoder spans monotonic and non-overlapping without reordering the words.

    Decoders occasionally emit a word whose span starts before its predecessor ends, which
    would make the transcript's timing fail validation without any authoring mistake. Only
    span boundaries move here; text, speaker, score, and word order stay exactly as
    recognized, so a genuinely misordered transcript still fails the token comparison
    instead of being silently resequenced into a plausible-looking result.
    """

    last_start: int | None = None
    last_end = 0
    for word in recognized_words:
        if last_start is not None and word["start_ms"] < last_start:
            word["start_ms"] = last_start
        if word["start_ms"] < last_end:
            word["start_ms"] = last_end
        if word["start_ms"] >= word["end_ms"]:
            word["end_ms"] = word["start_ms"] + 1
        last_start = word["start_ms"]
        last_end = word["end_ms"]
    return recognized_words


def normalize_provider_timeline(
    approved_text: str,
    provider_words: Sequence[Mapping[str, Any]],
    *,
    audio_sha256: str,
    duration_ms: int,
    provider: str,
    model_id: str,
    model_revision: str,
    timing_status: str,
) -> dict[str, Any]:
    """Bind provider timestamps to approved words only after exact token agreement."""
    if not isinstance(approved_text, str) or not approved_text.split():
        raise ValueError("approved_text must contain at least one word")
    if not isinstance(provider, str) or provider not in {"whisperx", "faster-whisper"}:
        raise ValueError("provider must be whisperx or faster-whisper")
    if not isinstance(timing_status, str) or timing_status not in {"aligned", "estimated"}:
        raise ValueError("provider timing_status must be aligned or estimated")
    duration_ms = _integer(duration_ms, "duration_ms", minimum=1)
    if not isinstance(provider_words, Sequence) or isinstance(provider_words, (str, bytes)):
        raise ValueError("provider_words must be a list")

    source_tokens = approved_text.split()
    recognized_words = []
    for index, word in enumerate(provider_words):
        if not isinstance(word, Mapping) or not {"text", "start_ms", "end_ms"} <= set(word):
            raise ValueError(f"provider_words[{index}] is missing word timing fields")
        text = _nonempty_string(word["text"], f"provider_words[{index}].text")
        item = {
            "id": f"r{index + 1:05d}",
            "text": text,
            "start_ms": word["start_ms"],
            "end_ms": word["end_ms"],
            "speaker_id": None,
            "timing_status": timing_status,
        }
        if "score" in word:
            item["score"] = word["score"]
        recognized_words.append(item)
    recognized_text = " ".join(word["text"] for word in recognized_words)
    recognized_words = _clamp_word_spans(recognized_words)
    _validate_words(recognized_words, duration_ms, "recognized_words")

    def token_key(token: str) -> str:
        normalized = unicodedata.normalize("NFKC", token).casefold()
        return "".join(char for char in normalized if not unicodedata.category(char).startswith("P"))

    matches = (
        len(source_tokens) == len(recognized_words)
        and all(token_key(source) == token_key(recognized["text"]) for source, recognized in zip(source_tokens, recognized_words))
    )
    base = {
        "schema_version": 1,
        "language": "id",
        "audio_sha256": audio_sha256,
        "provider": provider,
        "model_id": model_id,
        "model_revision": model_revision,
        "status": timing_status if matches else "unresolved",
        "duration_ms": duration_ms,
        "words": [],
        "approved_transcript": approved_text,
        "recognized_text": recognized_text,
    }
    if matches:
        base["words"] = [
            {
                "id": f"w{index + 1:05d}",
                "text": token,
                "start_ms": recognized["start_ms"],
                "end_ms": recognized["end_ms"],
                "speaker_id": recognized["speaker_id"],
                "timing_status": timing_status,
                **({"score": recognized["score"]} if "score" in recognized else {}),
            }
            for index, (token, recognized) in enumerate(zip(source_tokens, recognized_words))
        ]
    else:
        base["reason"] = "transcript_mismatch"
        base["recognized_words"] = recognized_words
    return validate_speech_timeline(base, audio_sha256=audio_sha256, duration_ms=duration_ms)


def resolve_speech_anchors(
    timeline: Mapping[str, Any],
    specs: Sequence[Mapping[str, Any]],
    *,
    video_duration_ms: int,
) -> list[dict[str, Any]]:
    """Resolve word-relative anchor offsets on the separately supplied video clock."""
    duration = _integer(video_duration_ms, "video_duration_ms", minimum=1)
    if not isinstance(timeline, Mapping):
        raise ValueError("timeline must be an object")
    audio_sha256 = timeline.get("audio_sha256")
    audio_duration_ms = timeline.get("duration_ms")
    validated = validate_speech_timeline(
        timeline,
        audio_sha256=audio_sha256,
        duration_ms=audio_duration_ms,
    )
    if not isinstance(specs, Sequence) or isinstance(specs, (str, bytes)):
        raise ValueError("anchor specs must be a list")
    words = {word["id"]: word for word in validated["words"]}
    seen: set[str] = set()
    resolved = []
    for index, spec in enumerate(specs):
        prefix = f"specs[{index}]"
        if not isinstance(spec, Mapping) or set(spec) != {"id", "word_id", "action_id", "offset_ms"}:
            raise ValueError(f"{prefix} must contain exactly id, word_id, action_id, offset_ms")
        anchor_id = _nonempty_string(spec["id"], f"{prefix}.id")
        if anchor_id in seen:
            raise ValueError(f"{prefix}.id is duplicated")
        seen.add(anchor_id)
        word_id = _nonempty_string(spec["word_id"], f"{prefix}.word_id")
        action_id = _nonempty_string(spec["action_id"], f"{prefix}.action_id")
        offset_ms = _integer(spec["offset_ms"], f"{prefix}.offset_ms")
        if word_id not in words:
            raise ValueError(f"{prefix}.word_id does not exist in the timeline")
        at_ms = words[word_id]["start_ms"] + offset_ms
        if at_ms < 0 or at_ms >= duration:
            raise ValueError(f"{prefix} resolves outside video duration")
        resolved.append({"id": anchor_id, "word_id": word_id, "action_id": action_id, "at_ms": at_ms})
    return resolved


def native_timeline_from_boundaries(
    words: Sequence[Mapping[str, Any]],
    *,
    audio_sha256: str,
    duration_ms: int,
    voice: str,
    model_revision: str,
    language: str = "id",
) -> dict[str, Any]:
    """Adapt local Edge word boundaries without changing their text or times."""
    if not isinstance(words, Sequence) or isinstance(words, (str, bytes)) or not words:
        raise ValueError("native boundaries must be a non-empty list")
    normalized = []
    for index, word in enumerate(words):
        if not isinstance(word, Mapping) or set(word) != {"text", "start_ms", "end_ms"}:
            raise ValueError(f"boundaries[{index}] must contain exactly text, start_ms, end_ms")
        normalized.append({
            "id": f"w{index + 1:05d}",
            "text": word["text"],
            "start_ms": word["start_ms"],
            "end_ms": word["end_ms"],
            "speaker_id": None,
            "timing_status": "native",
        })
    doc = {
        "schema_version": 1,
        "language": language,
        "audio_sha256": audio_sha256,
        "provider": "edge-tts",
        "model_id": voice,
        "model_revision": model_revision,
        "status": "native",
        "duration_ms": duration_ms,
        "words": normalized,
    }
    return validate_speech_timeline(doc, audio_sha256=audio_sha256, duration_ms=duration_ms)
