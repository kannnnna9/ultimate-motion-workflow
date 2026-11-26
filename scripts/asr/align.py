from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import logging
import math
import os
import pwd
import resource
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from ump_core.audio import normalize_provider_timeline, validate_speech_timeline


CODE_ROOT = Path(__file__).resolve().parents[2]
MODEL_LOCK_PATH = CODE_ROOT / "scripts" / "asr" / "models.lock.json"
MAX_AUDIO_BYTES = 100 * 1024 * 1024
MAX_TRANSCRIPT_BYTES = 1024 * 1024
MAX_WORDS = 10_000
MAX_OUTPUT_BYTES = 4 * 1024 * 1024


class ModelIntegrityError(ValueError):
    pass


def flatten_transcript(doc: Any) -> str:
    """Flatten approved narration text while retaining each word's original spelling."""
    if not isinstance(doc, dict):
        raise ValueError("transcript JSON must be an object")
    if isinstance(doc.get("text"), str):
        text = doc["text"]
        if text.strip():
            return text
        raise ValueError("transcript text is empty")
    beats = doc.get("beats")
    if not isinstance(beats, list) or not beats:
        raise ValueError("transcript must contain text or non-empty beats")
    parts = []
    for index, beat in enumerate(beats):
        if not isinstance(beat, dict) or not isinstance(beat.get("narration"), str) or not beat["narration"].strip():
            raise ValueError(f"beats[{index}].narration must be non-empty text")
        parts.append(beat["narration"].strip())
    return " ".join(parts)


def resolve_workspace_file(path_value: str | os.PathLike[str], workspace: str | os.PathLike[str]) -> Path:
    """Resolve a staged regular file and reject paths or symlinks outside its workspace."""
    root = Path(workspace).resolve(strict=True)
    supplied = Path(path_value)
    candidate = supplied if supplied.is_absolute() else root / supplied
    try:
        lexical_relative = candidate.absolute().relative_to(root)
    except ValueError as exc:
        raise ValueError("input must resolve inside the staged workspace") from exc
    cursor = root
    for part in lexical_relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError("workspace inputs may not contain symlinks")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ValueError("input must resolve inside the staged workspace") from exc
    if not resolved.is_file():
        raise ValueError("workspace input must be a regular file")
    return resolved


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative_path(value: Any, field: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ModelIntegrityError(f"{field} must be a relative path")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts or "." in relative.parts:
        raise ModelIntegrityError(f"{field} must remain beneath the model root")
    return relative


def verify_model_snapshot(spec: dict[str, Any], model_root: str | os.PathLike[str]) -> Path:
    """Verify the exact allowlisted model files and return the pinned snapshot path."""
    root = Path(model_root).resolve(strict=True)
    relative = _safe_relative_path(spec.get("cache_relative_path"), "cache_relative_path")
    snapshot = root / relative
    try:
        resolved = snapshot.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ModelIntegrityError("pinned model snapshot is unavailable or outside the model root") from exc
    if snapshot.is_symlink() or not resolved.is_dir():
        raise ModelIntegrityError("pinned model snapshot must be a real directory")
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ModelIntegrityError("model snapshot path may not contain symlinks")

    files = spec.get("files")
    if not isinstance(files, list) or not files:
        raise ModelIntegrityError("model lock must list files")
    expected: dict[str, dict[str, Any]] = {}
    for entry in files:
        if not isinstance(entry, dict):
            raise ModelIntegrityError("model file entry must be an object")
        name = entry.get("filename")
        size = entry.get("size_bytes")
        digest = entry.get("sha256")
        if not isinstance(name, str) or Path(name).name != name or name in {"", ".", ".."}:
            raise ModelIntegrityError("model filename must be a basename")
        if name in expected or isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ModelIntegrityError("model file size or filename is invalid")
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ModelIntegrityError("model file SHA-256 is invalid")
        expected[name] = entry
    actual = {item.name for item in resolved.iterdir()}
    if actual != set(expected):
        raise ModelIntegrityError("model snapshot file set does not match the pinned allowlist")
    for name, entry in expected.items():
        path = resolved / name
        try:
            target = path.resolve(strict=True)
            target.relative_to(root)
        except (OSError, ValueError) as exc:
            raise ModelIntegrityError(f"pinned model file target is unavailable or outside the model root: {name}") from exc
        if not target.is_file():
            raise ModelIntegrityError(f"pinned model file target is not regular: {name}")
        if target.stat().st_size != entry["size_bytes"] or _hash_file(target) != entry["sha256"]:
            raise ModelIntegrityError(f"pinned model file size or SHA-256 mismatch: {name}")
    return resolved


def load_model_lock(path: str | os.PathLike[str] = MODEL_LOCK_PATH) -> dict[str, Any]:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ModelIntegrityError("model lock is unavailable or invalid JSON") from exc
    if not isinstance(doc, dict) or doc.get("schema_version") != 1 or not isinstance(doc.get("models"), dict):
        raise ModelIntegrityError("model lock schema is invalid")
    return doc


def _model_root() -> Path:
    try:
        home = pwd.getpwuid(os.getuid()).pw_dir
    except KeyError as exc:
        raise ModelIntegrityError("cannot resolve the dedicated ASR model owner") from exc
    return Path(home) / ".cache" / "ump-v2" / "whisperx"


def _probe_audio_duration_ms(audio_path: Path) -> int:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "json", str(audio_path),
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    data = json.loads(result.stdout)
    seconds = float(data["format"]["duration"])
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("audio duration is invalid")
    return round(seconds * 1000)


def _approved_transcript(path: Path) -> str:
    if path.stat().st_size > MAX_TRANSCRIPT_BYTES:
        raise ValueError("transcript JSON exceeds the size limit")
    return flatten_transcript(json.loads(path.read_text(encoding="utf-8")))


class _SingleSpanSentenceSplitter:
    def span_tokenize(self, text: str) -> list[tuple[int, int]]:
        return [(0, len(text))] if text else []


def install_whisperx_local_sentence_splitter(alignment_module: Any) -> None:
    """Prevent WhisperX from lazily downloading Punkt for one supplied transcript segment."""
    original_loader = alignment_module.nltk_load

    def offline_loader(resource_url: Any, *args: Any, **kwargs: Any) -> Any:
        if str(resource_url).startswith("tokenizers/punkt_tab/"):
            return _SingleSpanSentenceSplitter()
        return original_loader(resource_url, *args, **kwargs)

    alignment_module.nltk_load = offline_loader


def _provider_words_whisperx(audio_path: Path, transcript: str, model_dir: Path, duration_ms: int) -> list[dict[str, Any]]:
    import importlib
    import torch
    import whisperx

    alignment_module = importlib.import_module("whisperx.alignment")
    install_whisperx_local_sentence_splitter(alignment_module)
    torch.set_num_threads(2)
    model, metadata = whisperx.load_align_model(
        language_code="id",
        device="cpu",
        model_name=str(model_dir),
        model_cache_only=True,
    )
    audio = whisperx.load_audio(str(audio_path))
    if not len(audio):
        raise ValueError("decoded audio is empty")
    segments = [{"start": 0.0, "end": duration_ms / 1000.0, "text": transcript}]
    aligned = whisperx.align(
        segments,
        model,
        metadata,
        audio,
        device="cpu",
        return_char_alignments=False,
        print_progress=False,
    )
    words = aligned.get("word_segments")
    if not isinstance(words, list):
        raise ValueError("WhisperX returned no word segment list")
    result = []
    for word in words:
        if not isinstance(word, dict) or not isinstance(word.get("word"), str):
            raise ValueError("WhisperX returned an invalid word segment")
        if "start" not in word or "end" not in word:
            raise ValueError("WhisperX left a transcript word without timing")
        item = {
            "text": word["word"],
            "start_ms": round(float(word["start"]) * 1000),
            "end_ms": round(float(word["end"]) * 1000),
        }
        if "score" in word:
            item["score"] = float(word["score"])
        result.append(item)
        if len(result) > MAX_WORDS:
            raise ValueError("WhisperX word count exceeds the limit")
    return result


def _provider_words_faster_whisper(audio_path: Path, model_dir: Path) -> list[dict[str, Any]]:
    from faster_whisper import WhisperModel

    model = WhisperModel(
        str(model_dir),
        device="cpu",
        compute_type="int8",
        cpu_threads=2,
        num_workers=1,
        local_files_only=True,
    )
    segments, _info = model.transcribe(
        str(audio_path),
        language="id",
        word_timestamps=True,
        vad_filter=False,
        beam_size=5,
        best_of=1,
    )
    result = []
    for segment in segments:
        for word in segment.words or []:
            result.append({
                "text": word.word.strip(),
                "start_ms": round(float(word.start) * 1000),
                "end_ms": round(float(word.end) * 1000),
                "score": float(word.probability),
            })
            if len(result) > MAX_WORDS:
                raise ValueError("faster-whisper word count exceeds the limit")
    return result


def _unresolved_timeline(
    *,
    audio_sha256: str,
    duration_ms: int,
    provider: str,
    model_id: str,
    model_revision: str,
    transcript: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "language": "id",
        "audio_sha256": audio_sha256,
        "provider": provider,
        "model_id": model_id,
        "model_revision": model_revision,
        "status": "unresolved",
        "duration_ms": duration_ms,
        "words": [],
        "reason": reason,
        "approved_transcript": transcript,
    }


def run_provider(
    provider: str,
    audio_path: Path,
    transcript: str,
    *,
    audio_sha256: str,
    duration_ms: int,
    model_root: Path,
    model_lock: dict[str, Any],
) -> dict[str, Any]:
    if provider not in {"whisperx", "faster-whisper"}:
        raise ValueError("provider is unsupported")
    spec = model_lock["models"].get(provider)
    if not isinstance(spec, dict):
        raise ModelIntegrityError("provider has no pinned model in the lock")
    model_dir = verify_model_snapshot(spec, model_root)
    status = "aligned" if provider == "whisperx" else "estimated"
    if provider == "whisperx":
        words = _provider_words_whisperx(audio_path, transcript, model_dir, duration_ms)
    else:
        words = _provider_words_faster_whisper(audio_path, model_dir)
    return normalize_provider_timeline(
        transcript,
        words,
        audio_sha256=audio_sha256,
        duration_ms=duration_ms,
        provider=provider,
        model_id=spec["repo"],
        model_revision=spec["revision"],
        timing_status=status,
    )


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline Indonesian word alignment runner")
    parser.add_argument("--audio", required=True)
    parser.add_argument("--transcript-json", required=True)
    parser.add_argument("--language", choices=("id",), required=True)
    parser.add_argument("--provider", choices=("whisperx", "faster-whisper"), required=True)
    args = parser.parse_args(argv)
    try:
        workspace = Path.cwd().resolve(strict=True)
        audio_path = resolve_workspace_file(args.audio, workspace)
        transcript_path = resolve_workspace_file(args.transcript_json, workspace)
        if audio_path.stat().st_size > MAX_AUDIO_BYTES:
            raise ValueError("audio file exceeds the size limit")
        transcript = _approved_transcript(transcript_path)
        audio_sha256 = _hash_file(audio_path)
        duration_ms = _probe_audio_duration_ms(audio_path)
        model_lock = load_model_lock()
        model_root = _model_root()
        model_spec = model_lock["models"].get(args.provider)
        if not isinstance(model_spec, dict):
            raise ModelIntegrityError("provider has no pinned model in the lock")
    except Exception as exc:
        print(f"ASR_INPUT_ERROR:{type(exc).__name__}:{exc}", file=sys.stderr)
        return 2

    started = time.monotonic()
    model_bytes = sum(item["size_bytes"] for item in model_spec["files"])
    logging.disable(logging.CRITICAL)
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            timeline = run_provider(
                args.provider,
                audio_path,
                transcript,
                audio_sha256=audio_sha256,
                duration_ms=duration_ms,
                model_root=model_root,
                model_lock=model_lock,
            )
    except Exception as exc:
        timeline = _unresolved_timeline(
            audio_sha256=audio_sha256,
            duration_ms=duration_ms,
            provider=args.provider,
            model_id=model_spec.get("repo"),
            model_revision=model_spec.get("revision"),
            transcript=transcript,
            reason=f"provider_error:{type(exc).__name__}",
        )
    timeline["metrics"] = {
        "wall_seconds": round(time.monotonic() - started, 3),
        "max_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "model_bytes": model_bytes,
    }
    try:
        validated = validate_speech_timeline(timeline, audio_sha256=audio_sha256, duration_ms=duration_ms)
        encoded = json.dumps(validated, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_OUTPUT_BYTES:
            raise ValueError("ASR result exceeds the output size limit")
    except Exception as exc:
        print(f"ASR_OUTPUT_ERROR:{type(exc).__name__}:{exc}", file=sys.stderr)
        return 3
    sys.stdout.write(encoded + "\n")
    return 0


def main() -> int:
    return _main()


if __name__ == "__main__":
    raise SystemExit(main())
