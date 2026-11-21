#!/usr/bin/env python3
"""render — HyperFrames → frame MP4 → caption terbakar + audio → MP4 final.

Tahap:
  1. validasi episode (template/episode/validate.py)
  2. snapshot HyperFrames (opsional, cek frame kunci tanpa render penuh)
  3. render HyperFrames ke out/<episode>/frames.mp4
  4. mux: VO + bed, bakar caption ASS, encode H.264/AAC
  5. verifikasi ffprobe: resolusi, fps, codec

Pemakaian:
    python3 scripts/render.py <folder-episode> --project <folder-hyperframes>
    python3 scripts/render.py <folder-episode> --project <folder-hyperframes> --preview
    python3 scripts/render.py <folder-episode> --project <folder-hyperframes> --snapshot 1,4,8

Tidak pernah menulis ke folder episode selain work/; hasil ada di out/ (di-ignore Git).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HYPERFRAMES_VERSION = "0.9.72"


def sh(cmd: list[str], cwd: Path | None = None, capture: bool = False, timeout: int | None = None):
    print(f"$ {' '.join(cmd)}" + (f"   (cwd={cwd})" if cwd else ""))
    res = subprocess.run(cmd, cwd=str(cwd) if cwd else None, text=True,
                         capture_output=capture, timeout=timeout)
    if res.returncode != 0:
        if capture:
            print((res.stdout or "")[-4000:], file=sys.stderr)
            print((res.stderr or "")[-4000:], file=sys.stderr)
        sys.exit(f"gagal (exit {res.returncode}): {cmd[0]}")
    return res


def hf(args: list[str], cwd: Path) -> None:
    """Panggil HyperFrames dengan cache privat + Chrome sistem.

    ~/.cache bisa group-writable di VM bersama, dan HyperFrames menolak
    menjalankan Chrome dari direktori seperti itu. Cache diarahkan ke
    HYPERFRAMES_CACHE_DIR (lihat .env.example) yang mode-nya 700.
    """
    env = os.environ.copy()
    cache = Path(env.get("HYPERFRAMES_CACHE_DIR") or Path.home() / ".hermes" / "hyperframes-cache")
    cache.mkdir(parents=True, exist_ok=True)
    if cache.stat().st_mode & 0o077:
        cache.chmod(0o700)
    env["HYPERFRAMES_CACHE_DIR"] = str(cache)
    chrome = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
    if chrome:
        env.setdefault("HYPERFRAMES_BROWSER_PATH", chrome)
    print(f"$ npx hyperframes@{HYPERFRAMES_VERSION} {' '.join(args)}   (cwd={cwd}, cache={cache})")
    res = subprocess.run(
        ["npx", "--yes", f"hyperframes@{HYPERFRAMES_VERSION}", *args],
        cwd=str(cwd), text=True, env=env,
    )
    if res.returncode != 0:
        sys.exit(f"gagal (exit {res.returncode}): hyperframes {args[0]}")


def probe(path: Path) -> dict:
    res = sh(["ffprobe", "-v", "error", "-print_format", "json",
              "-show_format", "-show_streams", str(path)], capture=True)
    return json.loads(res.stdout)


def validate_output(info: dict, brand: dict) -> list[str]:
    problems: list[str] = []
    canvas = brand["canvas"]
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    if not v:
        problems.append("tidak ada stream video")
    else:
        if (v["width"], v["height"]) != (canvas["width"], canvas["height"]):
            problems.append(f"resolusi {v['width']}x{v['height']} != {canvas['width']}x{canvas['height']}")
        num, den = v["avg_frame_rate"].split("/")
        fps = float(num) / float(den)
        if abs(fps - canvas["fps"]) > 0.01:
            problems.append(f"fps {fps:.3f} != {canvas['fps']}")
        if v["codec_name"] != canvas["codec"]:
            problems.append(f"codec video {v['codec_name']} != {canvas['codec']}")
    if not a:
        problems.append("tidak ada stream audio")
    elif a["codec_name"] != canvas["audio_codec"]:
        problems.append(f"codec audio {a['codec_name']} != {canvas['audio_codec']}")
    return problems


def build_mux_command(frames: Path, vo: Path, ass: Path, final: Path, *, preview: bool, video_duration: float, bed: Path | None = None) -> list[str]:
    audio_inputs = ["-i", str(vo)]
    video_filter = f"[0:v]ass={ass.as_posix()}"
    if preview:
        video_filter += ",scale=540:960"
    filter_parts = [f"{video_filter}[v0]"]
    pad = f"apad=whole_dur={video_duration:.3f}"
    if bed and bed.exists():
        audio_inputs += ["-i", str(bed)]
        filter_parts.append(f"[1:a]volume=1.0,{pad}[vo]")
        filter_parts.append("[2:a]volume=0.12[bed]")
        filter_parts.append("[vo][bed]amix=inputs=2:duration=first:dropout_transition=0[aout]")
    else:
        filter_parts.append(f"[1:a]{pad}[aout]")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-i", str(frames), *audio_inputs,
           "-filter_complex", ";".join(filter_parts),
           "-map", "[v0]", "-map", "[aout]",
           "-c:v", "libx264", "-preset", "medium", "-crf", "23" if preview else "18",
           "-pix_fmt", "yuv420p", "-r", "30",
           "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
           "-movflags", "+faststart", str(final)]
    return cmd


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("episode", type=Path)
    ap.add_argument("--project", type=Path, default=None,
                    help="folder proyek HyperFrames (index.html). Default: komposisi dari style_id episode")
    ap.add_argument("--style", default=None, help="override style_id episode untuk render ini")
    ap.add_argument("--preview", action="store_true", help="render draft resolusi setengah")
    ap.add_argument("--snapshot", default=None, help="detik dipisah koma, mis. 1,4,8 — hanya snapshot")
    ap.add_argument("--keep-frames", action="store_true", help="simpan MP4 antara (frames.mp4)")
    ap.add_argument("--skip-check", action="store_true")
    args = ap.parse_args(argv)

    ep = args.episode.expanduser().resolve()
    brand = json.loads((ep / "brand.json").read_text(encoding="utf-8"))
    style_id = args.style or brand.get("style_id")
    entry_name = "index.html"
    if args.project:
        project = args.project.expanduser().resolve()
    else:
        registry = json.loads((ROOT / "styles" / "registry.json").read_text(encoding="utf-8"))
        entry = next((s for s in registry["styles"] if s["id"] == style_id), None)
        if entry is None:
            sys.exit(f"style_id '{style_id}' tidak ada di styles/registry.json")
        if not entry.get("composition"):
            sys.exit(f"style '{style_id}' belum punya komposisi (status {entry.get('status')}) — "
                     f"pakai --project manual atau selesaikan komposisinya dulu")
        composition = ROOT / entry["composition"]
        entry_name = composition.name
        project = composition.parent
    print(f"style: {style_id} → {project}")
    out_dir = ROOT / "out" / ep.name
    out_dir.mkdir(parents=True, exist_ok=True)
    work = ep / "work"
    work.mkdir(exist_ok=True)

    if not (project / entry_name).exists():
        sys.exit(f"{project}/{entry_name} tidak ada")

    # 1. validasi data episode
    sh([sys.executable, str(ROOT / "templates/episode/validate.py"), str(ep)])

    # 2. snapshot saja
    if args.snapshot:
        hf(["snapshot", "--at", args.snapshot, "--output", str(out_dir / "snapshots")], project)
        print(f"ok  snapshot → {out_dir/'snapshots'}")
        return 0

    # 3. render HyperFrames
    frames = out_dir / "frames.mp4"
    if not args.skip_check:
        hf(["check"], project)
    quality = "draft" if args.preview else "delivery"
    hf(["render", "--quality", quality, "--workers", "1", "-o", str(frames)], project)
    if not frames.exists():
        sys.exit("HyperFrames selesai tanpa menghasilkan MP4")

    # 4. mux + caption
    vo = ep / "assets/audio/vo.mp3"
    ass = work / "captions.ass"
    if not ass.exists():
        sh([sys.executable, str(ROOT / "scripts/subs.py"), str(ep)])
    if not vo.exists():
        sys.exit(f"{vo} tidak ada — jalankan scripts/caption.py dulu")

    bed = ep / "assets/audio/bed.mp3"
    # apad dibatasi ke durasi `d`: apad TANPA argumen mengisi audio tanpa batas,
    # dan bersama -shortest ffmpeg menulis sampai disk penuh ("No space left on
    # device"). Nilai d = durasi video nyata diambil dari ffprobe.
    video_s = float(probe(frames)["format"]["duration"])
    suffix = "-preview" if args.preview else ""
    final = out_dir / f"{ep.name}{suffix}.mp4"
    cmd = build_mux_command(frames, vo, ass, final, preview=args.preview, video_duration=video_s, bed=bed)
    if args.preview:
        print("CATATAN: preview=540x960, validasi resolusi dilewati (bukan master)")
    sh(cmd)

    # 5. verifikasi
    info = probe(final)
    problems = [] if args.preview else validate_output(info, brand)
    duration = float(info["format"]["duration"])
    print()
    print(f"HASIL: {final}")
    print(f"       durasi {duration:.2f}s")
    if problems:
        print("VERIFIKASI GAGAL:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"VERIFIKASI LULUS: {brand['canvas']['width']}x{brand['canvas']['height']} "
          f"{brand['canvas']['fps']}fps {brand['canvas']['codec']}/{brand['canvas']['audio_codec']}")

    if not args.keep_frames and frames.exists():
        frames.unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
