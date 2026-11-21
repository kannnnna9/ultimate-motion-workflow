#!/usr/bin/env python3
"""captions.json -> subtitle ASS yang terbakar di video akhir.

Gaya mengikuti brand sheet episode: font, ukuran, dan kotak aman dibaca dari
brand.json. Satu peristiwa caption = maksimal `max_words_per_line` kata,
supaya baseline tidak keluar dari safe area dan tidak ada dinding teks.

Pemakaian:
    python3 scripts/subs.py <folder-episode> [-o keluaran.ass]

Keluaran default: <folder-episode>/work/captions.ass
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,{bold},0,0,0,100,100,0,0,1,4,2,2,{ml},{mr},{mv},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def ts(ms: int) -> str:
    ms = max(0, int(ms))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h}:{m:02d}:{s:02d}.{ms // 10:02d}"


def esc(t: str) -> str:
    return t.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def chunk(words: list[dict], max_words: int) -> list[list[dict]]:
    """Pecah per kata menjadi baris caption pendek pada tanda baca alami."""
    out: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        cur.append(w)
        end_sentence = w["text"].rstrip().endswith((".", ",", "?", "!", ";", ":"))
        if len(cur) >= max_words or end_sentence:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("episode", type=Path)
    ap.add_argument("-o", "--output", type=Path, default=None)
    args = ap.parse_args(argv)

    ep = args.episode.expanduser().resolve()
    brand = json.loads((ep / "brand.json").read_text(encoding="utf-8"))
    caps_path = ep / "work" / "captions.json"
    if not caps_path.exists():
        sys.exit(f"{caps_path} belum ada — jalankan scripts/caption.py dulu")
    caps = json.loads(caps_path.read_text(encoding="utf-8"))

    w = brand["canvas"]["width"]
    h = brand["canvas"]["height"]
    font = brand["typography"]["captions"]
    max_words = brand["rules"]["max_words_per_line"]

    # MarginV = jarak dari bawah ke baseline baris caption.
    safe = brand["safe_area"]
    margin_v = h - (safe["y"] + safe["height"])
    ml = safe["x"]
    mr = w - (safe["x"] + safe["width"])

    out = HEADER.format(
        w=w, h=h, font=font["family"], size=font["size_px"],
        bold=-1 if font.get("weight", 400) >= 700 else 0,
        ml=ml, mr=mr, mv=margin_v,
    )

    lines = []
    for group in chunk(caps["words"], max_words):
        start = group[0]["start_ms"]
        end = group[-1]["end_ms"]
        text = esc(" ".join(x["text"] for x in group))
        lines.append(f"Dialogue: 0,{ts(start)},{ts(end)},Cap,,0,0,0,,{text}")

    output = args.output or (ep / "work" / "captions.ass")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(out + "\n".join(lines) + "\n", encoding="utf-8")
    print(f"ok  {len(lines)} peristiwa caption, MarginV={margin_v}, MarginL={ml}, MarginR={mr} → {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
