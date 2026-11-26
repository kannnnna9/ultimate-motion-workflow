from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Mapping
from typing import Any

from .audio import validate_speech_timeline


MOBILE_ASS_STYLE = {
    "font_name": "DejaVu Sans",
    "font_size": 56,
    "bold": True,
    "outline": 3.0,
    "shadow": 0.0,
    "margin_l": 64,
    "margin_r": 64,
    "margin_v": 230,
    "max_words_per_cue": 5,
    "anchor": "bottom-center",
}
_STYLE_FIELDS = frozenset(MOBILE_ASS_STYLE)
_FONT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,63}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_MAX_ASS_DIMENSION = 8192
_MAX_CUE_GAP_MS = 600
_SENTENCE_END = frozenset(".!?…")
_CLOSING_PUNCTUATION = "\"'’”)]}»"
_WORD_JOINER = "\u2060"


def _integer(value: Any, field: str, *, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{field} must be an integer between {minimum} and {maximum}")
    return value


def _finite_number(value: Any, field: str, *, minimum: float, maximum: float) -> float:
    if type(value) is int:
        try:
            number = float(value)
        except OverflowError as exc:
            raise ValueError(f"{field} must be a finite number") from exc
    elif type(value) is float and math.isfinite(value):
        number = value
    else:
        raise ValueError(f"{field} must be a finite number")
    if not minimum <= number <= maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}")
    return number


def _validate_style(style: Mapping[str, Any], *, width: int, height: int) -> dict[str, Any]:
    if not isinstance(style, Mapping) or set(style) != _STYLE_FIELDS:
        raise ValueError("style must contain exactly the supported mobile ASS fields")
    font_name = style["font_name"]
    if not isinstance(font_name, str) or not _FONT_NAME.fullmatch(font_name):
        raise ValueError("font_name contains unsupported ASS style characters")
    font_size = _integer(style["font_size"], "font_size", minimum=18, maximum=112)
    if type(style["bold"]) is not bool:
        raise ValueError("bold must be boolean")
    outline = _finite_number(style["outline"], "outline", minimum=0, maximum=8)
    shadow = _finite_number(style["shadow"], "shadow", minimum=0, maximum=8)
    margin_l = _integer(style["margin_l"], "margin_l", minimum=0, maximum=width)
    margin_r = _integer(style["margin_r"], "margin_r", minimum=0, maximum=width)
    margin_v = _integer(style["margin_v"], "margin_v", minimum=0, maximum=height)
    max_words = _integer(style["max_words_per_cue"], "max_words_per_cue", minimum=1, maximum=8)
    if style["anchor"] != "bottom-center":
        raise ValueError("anchor must be bottom-center")
    return {
        "font_name": font_name,
        "font_size": font_size,
        "bold": style["bold"],
        "outline": outline,
        "shadow": shadow,
        "margin_l": margin_l,
        "margin_r": margin_r,
        "margin_v": margin_v,
        "max_words_per_cue": max_words,
        "anchor": "bottom-center",
    }


def _ends_sentence(text: str) -> bool:
    stripped = text.rstrip(_CLOSING_PUNCTUATION)
    return bool(stripped and stripped[-1] in _SENTENCE_END)


def _cue_groups(words: list[dict[str, Any]], max_words: int) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for word in words:
        text = word["text"]
        if any(unicodedata.category(char) == "Cc" or char in "\u2028\u2029" for char in text):
            raise ValueError("word text cannot contain control or line-separator characters")
        if current and word["start_ms"] - current[-1]["end_ms"] > _MAX_CUE_GAP_MS:
            groups.append(current)
            current = []
        current.append(word)
        if len(current) >= max_words or _ends_sentence(text):
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def _escape_ass_text(text: str) -> str:
    escaped: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\":
            escaped.append("\\")
            if index + 1 >= len(text) or text[index + 1] != _WORD_JOINER:
                escaped.append(_WORD_JOINER)
        elif char == "{":
            escaped.append(r"\{{}")
        else:
            escaped.append(char)
        index += 1
    return "".join(escaped)


def _ass_time(milliseconds: int) -> str:
    centiseconds = milliseconds // 10
    seconds, cs = divmod(centiseconds, 100)
    minutes, second = divmod(seconds, 60)
    hours, minute = divmod(minutes, 60)
    return f"{hours}:{minute:02}:{second:02}.{cs:02}"


def _serialize_ass(
    words: list[dict[str, Any]], style: dict[str, Any], *, width: int, height: int,
) -> bytes:
    header = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "WrapStyle:0",
        "ScaledBorderAndShadow:yes",
        f"PlayResX:{width}",
        f"PlayResY:{height}",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Default,{font_name},{font_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,{bold},0,0,0,100,100,0,0,1,{outline:g},{shadow:g},2,{margin_l},{margin_r},{margin_v},1".format(
            font_name=style["font_name"],
            font_size=style["font_size"],
            bold=-1 if style["bold"] else 0,
            outline=style["outline"],
            shadow=style["shadow"],
            margin_l=style["margin_l"],
            margin_r=style["margin_r"],
            margin_v=style["margin_v"],
        ),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    lines = header
    for group in _cue_groups(words, style["max_words_per_cue"]):
        start_ms = group[0]["start_ms"]
        end_ms = group[-1]["end_ms"]
        start_cs = start_ms // 10
        end_cs = end_ms // 10
        if start_cs >= end_cs:
            raise ValueError("subtitle cue is shorter than ASS centisecond resolution")
        text = " ".join(word["text"] for word in group)
        lines.append(
            f"Dialogue: 0,{_ass_time(start_ms)},{_ass_time(end_ms)},Default,,0,0,0,,{_escape_ass_text(text)}"
        )
    return ("\n".join(lines) + "\n").encode("utf-8", "strict")


def render_ass(
    timeline: Mapping[str, Any],
    style: Mapping[str, Any],
    *,
    audio_sha256: str,
    track_duration_ms: int,
    width: int = 1080,
    height: int = 1920,
) -> bytes:
    if not isinstance(audio_sha256, str) or not _SHA256.fullmatch(audio_sha256):
        raise ValueError("audio_sha256 must be lowercase SHA-256 hex")
    if type(track_duration_ms) is not int or track_duration_ms < 1:
        raise ValueError("track_duration_ms must be a positive integer")
    width = _integer(width, "width", minimum=1, maximum=_MAX_ASS_DIMENSION)
    height = _integer(height, "height", minimum=1, maximum=_MAX_ASS_DIMENSION)
    validated_style = _validate_style(style, width=width, height=height)
    validated = validate_speech_timeline(
        timeline, audio_sha256=audio_sha256, duration_ms=track_duration_ms,
    )
    if validated["status"] == "unresolved":
        raise ValueError("unresolved speech timing cannot create ASS subtitles")
    if not validated["words"]:
        raise ValueError("ASS subtitles require at least one timed word")
    if any(word["timing_status"] == "unresolved" for word in validated["words"]):
        raise ValueError("unresolved word timing cannot create ASS subtitles")
    return _serialize_ass(validated["words"], validated_style, width=width, height=height)


def render_subtitles(
    mode: str,
    timeline: Mapping[str, Any] | None,
    style: Mapping[str, Any] | None,
    *,
    audio_sha256: str | None = None,
    track_duration_ms: int | None = None,
    width: int = 1080,
    height: int = 1920,
) -> bytes | None:
    if mode == "none":
        return None
    if mode != "ass":
        raise ValueError("subtitle mode must be none or ass")
    if timeline is None or style is None or audio_sha256 is None or track_duration_ms is None:
        raise ValueError("ASS mode requires timeline, style, source hash, and track duration")
    return render_ass(
        timeline,
        style,
        audio_sha256=audio_sha256,
        track_duration_ms=track_duration_ms,
        width=width,
        height=height,
    )
