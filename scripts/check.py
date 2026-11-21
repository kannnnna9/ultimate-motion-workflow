#!/usr/bin/env python3
"""Pemeriksa prasyarat untuk workflow ini.

Memeriksa: node/npm, ffmpeg/ffprobe, Chrome, python3, jsonschema, edge-tts,
dan HyperFrames pada versi yang dipatok. Tidak memasang apa pun; melaporkan
apa yang kurang beserta perintah pemasangnya.

Pemakaian:
    python3 scripts/check.py
    python3 scripts/check.py --json

Exit 0 = semua prasyarat wajib ada. 1 = ada yang kurang.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HYPERFRAMES_VERSION = "0.9.72"

REQUIRED = "wajib"
OPTIONAL = "opsional"


def run(cmd: list[str], timeout: int = 120) -> tuple[bool, str]:
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    out = (res.stdout or res.stderr).strip().splitlines()
    return res.returncode == 0, out[0] if out else ""


def probe_binary(name: str, args: list[str], version_hint: str, level: str, install: str) -> dict:
    path = shutil.which(name)
    if not path:
        return {"name": name, "ok": False, "level": level, "found": None,
                "version": "", "detail": f"tidak ditemukan. Pasang: {install}"}
    ok, out = run([path, *args])
    return {"name": name, "ok": ok, "level": level, "found": path,
            "version": out, "detail": version_hint if ok else f"gagal menjalankan {name}"}


def main(argv: list[str]) -> int:
    as_json = "--json" in argv
    results: list[dict] = []

    results.append(probe_binary("node", ["--version"], "Node 20+ disarankan", REQUIRED, "nvm/nodejs.org"))
    results.append(probe_binary("npm", ["--version"], "ikut bersama Node", REQUIRED, "ikut bersama Node"))
    results.append(probe_binary("ffmpeg", ["-version"], "FFmpeg 6+", REQUIRED, "sudo apt install ffmpeg"))
    results.append(probe_binary("ffprobe", ["-version"], "ikut bersama FFmpeg", REQUIRED, "sudo apt install ffmpeg"))

    chrome = None
    for cand in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        chrome = shutil.which(cand)
        if chrome:
            break
    results.append({
        "name": "chrome", "ok": bool(chrome), "level": REQUIRED, "found": chrome,
        "version": "", "detail": "" if chrome else "tidak ditemukan. Pasang: google-chrome",
    })

    # Python + paket
    results.append({"name": "python3", "ok": sys.version_info >= (3, 10),
                    "level": REQUIRED, "found": sys.executable, "version": sys.version.split()[0],
                    "detail": "" if sys.version_info >= (3, 10) else "butuh Python 3.10+"})

    try:
        import jsonschema  # noqa: F401
        from importlib.metadata import version as _pkg_version
        js_ok, js_ver = True, _pkg_version("jsonschema")
    except ImportError:
        js_ok, js_ver = False, ""
    except Exception:
        js_ok, js_ver = True, "terpasang"
    results.append({"name": "jsonschema", "ok": js_ok, "level": REQUIRED, "found": "python",
                    "version": js_ver,
                    "detail": "" if js_ok else "pip install 'jsonschema>=4.20'"})

    try:
        import edge_tts  # noqa: F401
        et_ok, et_ver = True, getattr(edge_tts, "__version__", "")
    except ImportError:
        et_ok, et_ver = False, ""
    results.append({"name": "edge-tts", "ok": et_ok, "level": OPTIONAL, "found": "python",
                    "version": et_ver,
                    "detail": "" if et_ok else "pip install edge-tts (hanya bila VO_ENGINE=edge)"})

    # HyperFrames versi terpatok — dicek lewat npx, lambat tapi pasti.
    if shutil.which("npx"):
        ok, out = run(["npx", "--yes", f"hyperframes@{HYPERFRAMES_VERSION}", "--version"], timeout=300)
        detail = "" if ok and out.strip() == HYPERFRAMES_VERSION else (
            f"versi tidak cocok: '{out}' != {HYPERFRAMES_VERSION}" if ok else "gagal menjalankan npx hyperframes")
        results.append({"name": "hyperframes", "ok": ok and out.strip() == HYPERFRAMES_VERSION,
                        "level": REQUIRED, "found": "npx", "version": out, "detail": detail})
    else:
        results.append({"name": "hyperframes", "ok": False, "level": REQUIRED, "found": None,
                        "version": "", "detail": "npx tidak ditemukan"})

    # Struktur repo
    for rel in ("templates/episode/validate.py", "vendor/bang-motion/SKILL.md"):
        p = ROOT / rel
        results.append({"name": rel, "ok": p.exists(), "level": REQUIRED, "found": str(p),
                        "version": "", "detail": "" if p.exists() else "file hilang dari clone"})

    hard_fail = [r for r in results if r["level"] == REQUIRED and not r["ok"]]
    soft_fail = [r for r in results if r["level"] == OPTIONAL and not r["ok"]]

    if as_json:
        print(json.dumps({"ok": not hard_fail, "results": results}, indent=2, ensure_ascii=False))
    else:
        for r in results:
            status = "OK  " if r["ok"] else ("FAIL" if r["level"] == REQUIRED else "WARN")
            ver = f" {r['version']}" if r["version"] else ""
            print(f"{status} {r['name']:<34}{ver}")
            if r["detail"]:
                print(f"     {r['detail']}")
        print()
        if hard_fail:
            print(f"HASIL: GAGAL — {len(hard_fail)} prasyarat wajib kurang")
        else:
            note = f" ({len(soft_fail)} opsional kurang)" if soft_fail else ""
            print(f"HASIL: SIAP{note}")

    return 0 if not hard_fail else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
