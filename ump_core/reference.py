from __future__ import annotations

import json
from bisect import bisect_right
from decimal import Decimal
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory


class ReferenceError(ValueError):
    pass


def _run(args: list[str]) -> str:
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode:
        raise ReferenceError(result.stderr.strip() or "ffmpeg failed")
    return result.stdout


def _probe(video: Path) -> tuple[list[Decimal], Decimal]:
    try:
        doc = json.loads(_run([
            "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_frames", "-show_streams",
            "-show_entries", "frame=best_effort_timestamp_time,duration_time,pkt_duration_time:stream=start_time,duration",
            "-of", "json", str(video),
        ]))
        frames = doc["frames"]
        pts = [Decimal(f["best_effort_timestamp_time"]) for f in frames]
        clock = [(p - pts[0]) * 1000 for p in pts]
        if clock != sorted(clock):
            raise ValueError("timestamps out of order")
        for key in ("duration_time", "pkt_duration_time"):
            if (tail := frames[-1].get(key)) not in (None, "N/A") and Decimal(tail) > 0:
                return clock, clock[-1] + Decimal(tail) * 1000
        stream = doc["streams"][0]
        start = stream.get("start_time")
        end = (Decimal(stream["duration"]) + (Decimal(start) if start not in (None, "N/A") else pts[0]) - pts[0]) * 1000
        if end <= clock[-1]:
            raise ValueError("missing last frame duration")
        return clock, end
    except (OSError, KeyError, IndexError, ArithmeticError, ValueError) as exc:
        raise ReferenceError(f"cannot probe video: {video}") from exc


def make_contact_sheet(video: Path, times_ms: list[int], out: Path) -> dict:
    """Use the last decoded presentation timestamp at or before each request.

    Time is relative to the first video frame; the end is exclusive. Frame
    indices are zero-based and the output keeps request order and duplicates.
    """
    video = Path(video).resolve()
    out = Path(out)
    if not video.is_file():
        raise ReferenceError(f"video not found: {video}")
    if not 1 <= len(times_ms) <= 48:
        raise ReferenceError("provide 1–48 timestamps")
    if any(not isinstance(t, int) or isinstance(t, bool) or t < 0 for t in times_ms):
        raise ReferenceError("timestamp must be a non-negative integer")
    clock, duration_ms = _probe(video)
    if any(t >= duration_ms for t in times_ms):
        raise ReferenceError(f"timestamp outside video: duration is {duration_ms}ms")
    with TemporaryDirectory() as tmp:
        return _write_sheet(video, times_ms, clock, duration_ms, out, Path(tmp))


def _write_sheet(video, times_ms, clock, duration_ms, out, frame_dir):
    records = []
    inputs: list[str] = []
    filters: list[str] = []
    tile_w, tile_h = 320, 180
    for index, at_ms in enumerate(times_ms):
        frame_index = bisect_right(clock, at_ms) - 1
        frame_ms = float(clock[frame_index])
        png = frame_dir / f"{index:04d}.png"
        _run(["ffmpeg", "-xerror", "-v", "error", "-i", str(video), "-map", "0:v:0", "-vf", f"select=eq(n\\,{frame_index})", "-frames:v", "1", "-threads", "1", str(png)])
        if not png.is_file():
            raise ReferenceError(f"frame decode failed at {at_ms}ms")
        inputs.extend(["-i", str(png)])
        filters.append(f"[{index}:v]scale={tile_w}:{tile_h}:force_original_aspect_ratio=decrease:force_divisible_by=2,pad={tile_w}:{tile_h}:(ow-iw)/2:(oh-ih)/2,setsar=1,drawtext=text='{at_ms} ms | f{frame_index}':x=8:y=8:fontsize=16:fontcolor=white:box=1:boxcolor=black@0.6[v{index}]")
        cols = 3
        records.append({"at_ms": at_ms, "frame_index": frame_index, "frame_ms": frame_ms, "tile": [(index % cols) * tile_w, (index // cols) * tile_h, tile_w, tile_h]})
    rows = (len(records) + 2) // 3
    row_labels = []
    sheet_filters = list(filters)
    for row in range(rows):
        labels = []
        for col in range(3):
            index = row * 3 + col
            if index < len(records):
                labels.append(f"[v{index}]")
            else:
                blank = f"blank{row}_{col}"
                sheet_filters.append(f"color=c=black:s={tile_w}x{tile_h}[{blank}]")
                labels.append(f"[{blank}]")
        row_label = f"row{row}"
        sheet_filters.append("".join(labels) + f"hstack=inputs=3[{row_label}]")
        row_labels.append(f"[{row_label}]")
    sheet_filters.append("".join(row_labels) + (f"vstack=inputs={rows}" if rows > 1 else "null"))
    sheet = frame_dir / "sheet.png"
    cmd = ["ffmpeg", "-v", "error", *inputs, "-filter_complex_threads", "1", "-filter_complex", ";".join(sheet_filters), "-frames:v", "1", "-threads", "1", str(sheet)]
    _run(cmd)
    doc = {"video": str(video), "duration_ms": float(duration_ms), "columns": 3, "frames": records}
    out.mkdir(parents=True, exist_ok=True)
    (out / "sheet.png").write_bytes(sheet.read_bytes())
    (out / "frames.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return doc
