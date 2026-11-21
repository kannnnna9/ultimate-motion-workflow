#!/usr/bin/env python3
"""setup — siapkan lingkungan dari clone bersih.

Yang dilakukan:
  1. Buat .venv (kalau belum ada) dan pasang paket Python dari scripts/requirements.txt.
  2. Verifikasi prasyarat sistem lewat scripts/check.py.
  3. Panaskan cache HyperFrames pada versi terpatok (npx sekali).

Tidak memasang Chrome/FFmpeg/Node — itu prasyarat sistem; kalau kurang,
check.py menyebut perintah apt-nya.

Pemakaian:
    python3 scripts/setup.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"
REQUIREMENTS = ROOT / "scripts" / "requirements.txt"
HYPERFRAMES_VERSION = "0.9.72"


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    print(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, text=True, **kw)


def main() -> int:
    if shutil.which("uv"):
        if not (VENV / "bin" / "python").exists():
            print("membuat .venv dengan uv")
            run(["uv", "venv", str(VENV)], check=True)
        pip_cmd = ["uv", "pip", "install", "--python", str(VENV / "bin" / "python"),
                   "-r", str(REQUIREMENTS)]
    else:
        if not (VENV / "bin" / "python").exists():
            print("membuat .venv dengan venv")
            run([sys.executable, "-m", "venv", str(VENV)], check=True)
        pip_cmd = [str(VENV / "bin" / "python"), "-m", "pip", "install", "-r", str(REQUIREMENTS)]

    res = run(pip_cmd)
    if res.returncode != 0:
        print("gagal memasang paket Python — periksa jaringan/indeks paket", file=sys.stderr)
        return 1

    print("\nmemeriksa prasyarat")
    res = run([str(VENV / "bin" / "python"), str(ROOT / "scripts" / "check.py")])
    if res.returncode != 0:
        print("\nsetup belum lengkap: perbaiki baris FAIL di atas lalu ulangi", file=sys.stderr)
        return 1

    print(f"\nmenghangatkan cache HyperFrames {HYPERFRAMES_VERSION}")
    res = run(["npx", "--yes", f"hyperframes@{HYPERFRAMES_VERSION}", "--version"])
    if res.returncode != 0:
        print("gagal mengunduh HyperFrames — cek jaringan", file=sys.stderr)
        return 1

    print("\nSETUP SELESAI. Langkah berikut: lihat README.md bagian 'Alur satu episode'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
