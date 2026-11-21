#!/usr/bin/env python3
"""Non-interactive acceptance checks for a UMP checkout."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def _probe(path: Path) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
        check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)


def inspect_media(path: Path, *, expected_width: int, expected_height: int, expected_fps: int) -> dict:
    info = _probe(Path(path))
    video = next((s for s in info["streams"] if s.get("codec_type") == "video"), None)
    audio = next((s for s in info["streams"] if s.get("codec_type") == "audio"), None)
    if not video or not audio:
        raise ValueError("missing audio or video stream")
    if (video.get("width"), video.get("height")) != (expected_width, expected_height):
        raise ValueError("wrong dimensions")
    num, den = video["avg_frame_rate"].split("/")
    if abs(float(num) / float(den) - expected_fps) > 0.01:
        raise ValueError("wrong fps")
    if video.get("codec_name") != "h264" or audio.get("codec_name") != "aac":
        raise ValueError("wrong codecs")
    decoded = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"], capture_output=True, text=True)
    if decoded.returncode:
        raise ValueError("decode failed")
    levels = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True)
    mean = next((line.split(":", 1)[1].strip() for line in levels.stderr.splitlines() if "mean_volume:" in line), "-inf dB")
    if mean.startswith("-inf") or float(mean.split()[0]) <= -60:
        raise ValueError("silent audio")
    return {"path": str(path), "width": video["width"], "height": video["height"], "fps": expected_fps, "video_codec": video["codec_name"], "audio_codec": audio["codec_name"], "duration": float(info["format"]["duration"]), "mean_volume": mean}


def verify_checkout(checkout: Path) -> dict:
    root = Path(checkout).resolve()
    required = [root / "styles" / "registry.json", root / "demo" / "explain-9x16", root / "scripts" / "render.py"]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise ValueError("missing required paths: " + ", ".join(missing))
    return {"checkout": str(root), "required_paths": len(required)}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    report = verify_checkout(args.checkout)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("VERIFY UMP: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(__import__("sys").argv[1:]))
