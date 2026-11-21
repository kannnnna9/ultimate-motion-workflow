#!/usr/bin/env python3
"""Bangun VO (mp3) + caption per kata (JSON) dari naskah episode.

Dua jalur:
  edge      — Edge TTS, WordBoundary memberi timestamp per kata. Butuh jaringan.
  external  — file VO yang sudah ada; transkrip lokal faster-whisper (kalau terpasang).

Pemakaian:
    python3 scripts/caption.py <folder-episode> [--engine edge|external] [--force]

Keluaran (relatif terhadap folder episode):
    assets/audio/vo.mp3        audio narasi
    work/captions.json         {"words":[{text,start_ms,end_ms}], "boundaries":[beat...]}

`boundaries` memetakan kapan tiap beat mulai bicara, dihitung dari kata pertama
beat itu. Nilai ini yang dipakai cue.json untuk menggeser aksi ke ucapan nyata.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

BEAT_WORDS_PER_SECOND = 2.7  # cadangan bila transkrip tidak memberi batas akurat


def load_episode(ep: Path) -> dict:
    p = ep / "naskah.json"
    if not p.exists():
        sys.exit(f"naskah.json tidak ada di {ep}")
    return json.loads(p.read_text(encoding="utf-8"))


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def flatten(naskah: dict) -> tuple[str, list[tuple[str, int, int]]]:
    """Teks utuh + rentang indeks karakter untuk tiap beat."""
    parts, spans, cursor = [], [], 0
    for beat in naskah["beats"]:
        text = beat["narration"].strip()
        start = cursor
        parts.append(text)
        cursor += len(text) + 1  # spasi pemisah
        spans.append((beat["id"], start, cursor - 1))
    return " ".join(parts), spans


def run_edge(text: str, voice: dict, mp3: Path, raw_json: Path) -> list[dict]:
    try:
        import edge_tts
    except ImportError:
        sys.exit("edge-tts tidak terpasang: pip install edge-tts")

    async def _go() -> None:
        comm = edge_tts.Communicate(
            text, voice["name"], rate=voice.get("rate", "+0%"), boundary="WordBoundary"
        )
        words: list[dict] = []
        with mp3.open("wb") as fh:
            async for chunk in comm.stream():
                kind = chunk.get("type")
                if kind == "audio":
                    fh.write(chunk["data"])
                elif kind == "WordBoundary":
                    start_ms = int(chunk.get("offset") or 0) // 10000
                    dur_ms = int(chunk.get("duration") or 0) // 10000
                    words.append({
                        "text": str(chunk.get("text") or ""),
                        "start_ms": start_ms,
                        "end_ms": start_ms + (dur_ms or 350),
                    })
        if not words:
            sys.exit("Edge TTS tidak mengembalikan WordBoundary — cek jaringan/nama voice")
        raw_json.write_text(json.dumps({"words": words}, ensure_ascii=False, indent=1), encoding="utf-8")

    import asyncio
    asyncio.run(_go())
    return json.loads(raw_json.read_text(encoding="utf-8"))["words"]


def run_external(vo: Path, language: str) -> list[dict]:
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        sys.exit("faster-whisper tidak terpasang: pip install faster-whisper")
    model = WhisperModel("small", device="cpu", compute_type="int8", cpu_threads=2, num_workers=1)
    segments, _ = model.transcribe(str(vo), language=language, word_timestamps=True, vad_filter=True)
    words: list[dict] = []
    for seg in segments:
        for w in seg.words or []:
            words.append({"text": w.word.strip(), "start_ms": int(w.start * 1000),
                          "end_ms": int(w.end * 1000)})
    if not words:
        sys.exit("transkrip kosong — periksa file VO")
    return words


def beat_boundaries(naskah: dict, words: list[dict]) -> list[dict]:
    """Tandai kata pertama tiap beat dengan mencocokkan teks berurutan."""
    beats = naskah["beats"]
    out: list[dict] = []
    idx = 0
    for beat in beats:
        first = norm(beat["narration"].split()[0]) if beat["narration"].split() else ""
        found = None
        for i in range(idx, len(words)):
            if norm(words[i]["text"]) == first:
                found = i
                break
        if found is None:
            out.append({"beat": beat["id"], "start_ms": None, "word_index": None})
            continue
        out.append({"beat": beat["id"], "start_ms": words[found]["start_ms"], "word_index": found})
        idx = found + 1
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("episode", type=Path)
    ap.add_argument("--engine", choices=["edge", "external"], default=None)
    ap.add_argument("--force", action="store_true", help="timpa keluaran yang sudah ada")
    args = ap.parse_args(argv)

    ep = args.episode.expanduser().resolve()
    naskah = load_episode(ep)
    engine = args.engine or naskah["voice"]["engine"]

    audio_dir = ep / "assets" / "audio"
    work = ep / "work"
    audio_dir.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    mp3 = audio_dir / "vo.mp3"
    caps = work / "captions.json"

    if caps.exists() and not args.force:
        print(f"lewat: {caps} sudah ada (pakai --force untuk menimpa)")
        return 0

    text, _spans = flatten(naskah)
    if engine == "edge":
        words = run_edge(text, naskah["voice"], mp3, work / "word-boundaries.raw.json")
    else:
        if not mp3.exists():
            sys.exit(f"VO_ENGINE=external tetapi {mp3} tidak ada — letakkan file VO di sana")
        words = run_external(mp3, naskah["language"])

    bounds = beat_boundaries(naskah, words)
    doc = {"words": words, "boundaries": bounds,
           "duration_ms": words[-1]["end_ms"] + 300 if words else 0}
    caps.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")

    missing = [b["beat"] for b in bounds if b["start_ms"] is None]
    total_s = doc["duration_ms"] / 1000
    print(f"ok  {len(words)} kata, durasi {total_s:.2f}s → {caps.relative_to(ep)}")
    for b in bounds:
        when = f"{b['start_ms']/1000:.2f}s" if b["start_ms"] is not None else "TIDAK DITEMUKAN"
        print(f"    {b['beat']}  mulai {when}")
    if missing:
        print(f"PERINGATAN: beat tidak terdeteksi di audio: {', '.join(missing)}", file=sys.stderr)
        return 1

    target = naskah.get("target_duration_s")
    if target:
        if total_s > target["max"]:
            print(f"PERINGATAN: {total_s:.1f}s > target max {target['max']}s — potong kata, jangan percepat gerak",
                  file=sys.stderr)
            return 1
        if total_s < target["min"]:
            print(f"PERINGATAN: {total_s:.1f}s < target min {target['min']}s", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
