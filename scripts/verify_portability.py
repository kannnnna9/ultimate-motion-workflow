#!/usr/bin/env python3
"""Verifikasi bahwa repo dapat dipindah dan episode demo dapat dirender."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EPISODE = Path("demo/explain-9x16")
PROJECT = Path("demo/hyperframes")


def run(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> None:
    print(f"$ {' '.join(cmd)}   (cwd={cwd})")
    subprocess.run(cmd, cwd=cwd, check=True, env=env)


def check_relative_paths(clone: Path) -> None:
    index = (clone / PROJECT / "index.html").read_text(encoding="utf-8")
    if "cdn.jsdelivr.net" in index or "unpkg.com" in index:
        raise SystemExit("FAIL: komposisi masih memakai CDN")
    if not (clone / PROJECT / "assets/gsap.min.js").exists():
        raise SystemExit("FAIL: GSAP lokal hilang dari clone")
    for rel in ("brand.json", "naskah.json", "cue.json", "assets/audio/vo.mp3"):
        if not (clone / EPISODE / rel).exists():
            raise SystemExit(f"FAIL: aset demo hilang: {EPISODE / rel}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--render", action="store_true", help="render master dari clone sementara")
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="umw-portability-") as raw:
        clone = Path(raw) / "clone"
        clone.mkdir()
        archive = subprocess.run(
            ["git", "archive", "--format=tar", "HEAD"],
            cwd=ROOT, check=True, capture_output=True,
        )
        import io
        import tarfile
        with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:") as tar:
            tar.extractall(clone)

        check_relative_paths(clone)
        run([sys.executable, "templates/episode/validate.py", str(EPISODE)], clone)
        scripts = sorted(str(p) for p in (clone / "scripts").glob("*.py"))
        relative_scripts = [str(Path(p).relative_to(clone)) for p in scripts]
        run([sys.executable, "-m", "py_compile", "templates/episode/validate.py", *relative_scripts], clone)
        print("PORTABILITY CHECK: struktur, data, aset lokal, dan syntax lulus")

        if args.render:
            env = os.environ.copy()
            env["HYPERFRAMES_CACHE_DIR"] = str(Path.home() / ".hermes" / "hyperframes-cache")
            run([sys.executable, "scripts/render.py", str(EPISODE), "--project", str(PROJECT)], clone, env=env)
            output = clone / "out" / EPISODE.name / f"{EPISODE.name}.mp4"
            if not output.exists():
                raise SystemExit("FAIL: render clone tidak menghasilkan MP4")
            print(f"PORTABILITY RENDER: PASS {output}")

    print("PORTABILITY: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
