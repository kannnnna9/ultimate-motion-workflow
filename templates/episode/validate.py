#!/usr/bin/env python3
"""Validasi satu folder episode: brand.json, naskah.json, cue.json.

Cek yang dijalankan:
  1. Skema JSON (schema/*.schema.json).
  2. Konsistensi lintas file: episode slug sama, beat di cue ada di naskah,
     fact_refs menunjuk sumber yang benar-benar ada.
  3. Urutan: cue id naik, t_ms monoton naik, beat narasi berurutan.
  4. Aturan cue bersama: setiap cue punya satu t_ms; SFX hanya lewat offset_frames.

Pemakaian:
    python3 templates/episode/validate.py templates/episode/episodes/_contoh

Exit 0 = lulus, 1 = ada pelanggaran. Tidak butuh paket di luar stdlib + jsonschema.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    import jsonschema
except ImportError:  # pragma: no cover - pesan jelas lebih baik daripada traceback
    sys.exit("jsonschema belum terpasang: pip install jsonschema>=4.20")

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
SCHEMA_DIR = HERE / "schema"
STYLE_REGISTRY = ROOT / "styles" / "registry.json"


def load(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path.name}: JSON tidak valid — {exc}") from exc


def check_schema(name: str, doc: dict, errors: list[str]) -> None:
    schema = json.loads((SCHEMA_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))
    validator = jsonschema.Draft7Validator(schema)
    for err in sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path)):
        where = "/".join(str(p) for p in err.absolute_path) or "(root)"
        errors.append(f"{name}.json → {where}: {err.message}")


def validate(episode_dir: Path) -> list[str]:
    errors: list[str] = []
    brand = load(episode_dir / "brand.json")
    naskah = load(episode_dir / "naskah.json")
    cue = load(episode_dir / "cue.json")

    missing = [
        name
        for name, doc in (("brand", brand), ("naskah", naskah), ("cue", cue))
        if doc is None
    ]
    if missing:
        return [f"{name}.json tidak ditemukan di {episode_dir}" for name in missing]
    assert brand is not None and naskah is not None and cue is not None

    return _check(episode_dir.name, brand, naskah, cue)


def _check(slug: str, brand: dict, naskah: dict, cue: dict) -> list[str]:
    errors: list[str] = []

    for name, doc in (("brand", brand), ("naskah", naskah), ("cue", cue)):
        check_schema(name, doc, errors)
    if errors:
        return errors

    if not STYLE_REGISTRY.exists():
        errors.append(f"registry style tidak ditemukan: {STYLE_REGISTRY}")
    else:
        registry = json.loads(STYLE_REGISTRY.read_text(encoding="utf-8"))
        style_ids = {style["id"] for style in registry.get("styles", [])}
        if brand["style_id"] not in style_ids:
            errors.append(
                f"brand.json style_id '{brand['style_id']}' tidak ada di styles/registry.json"
            )
    if errors:
        return errors

    for name, doc in (("brand", brand), ("naskah", naskah), ("cue", cue)):
        if doc["episode"] != slug:
            errors.append(f"{name}.json episode='{doc['episode']}' != nama folder '{slug}'")

    beat_ids = [b["id"] for b in naskah["beats"]]
    if beat_ids != sorted(beat_ids) or len(set(beat_ids)) != len(beat_ids):
        errors.append(f"naskah.json beat id harus unik dan naik: {beat_ids}")
    expected = [f"b{i:03d}" for i in range(1, len(beat_ids) + 1)]
    if beat_ids != expected:
        errors.append(f"naskah.json beat id harus berurutan tanpa lompat: {beat_ids} != {expected}")

    source_ids = {s["id"] for s in naskah.get("sources", [])}
    for beat in naskah["beats"]:
        for ref in beat.get("fact_refs", []):
            if ref not in source_ids:
                errors.append(f"naskah.json {beat['id']}: fact_refs '{ref}' tidak ada di sources")

    cue_ids = [c["id"] for c in cue["cues"]]
    if cue_ids != sorted(cue_ids) or len(set(cue_ids)) != len(cue_ids):
        errors.append(f"cue.json cue id harus unik dan naik: {cue_ids}")
    times = [c["t_ms"] for c in cue["cues"]]
    if times != sorted(times):
        errors.append("cue.json t_ms harus monoton naik")

    for c in cue["cues"]:
        if c["beat"] not in beat_ids:
            errors.append(f"cue.json {c['id']}: beat '{c['beat']}' tidak ada di naskah")
        sfx = c.get("sfx")
        if sfx and "t_ms" in sfx:
            errors.append(
                f"cue.json {c['id']}: sfx tidak boleh punya t_ms sendiri — pakai offset_frames"
            )

    for bed in cue.get("audio_beds", []):
        for key in ("from_beat", "to_beat"):
            if bed[key] not in beat_ids:
                errors.append(f"cue.json audio_beds '{bed['name']}': {key} '{bed[key]}' tidak ada di naskah")

    end = cue.get("end_card")
    if end:
        if end["start_beat"] not in beat_ids:
            errors.append(f"cue.json end_card: start_beat '{end['start_beat']}' tidak ada di naskah")
        if end["hold_s"] != brand["rules"]["end_card_hold_s"]:
            errors.append(
                "cue.json end_card.hold_s="
                f"{end['hold_s']} berbeda dengan brand rules.end_card_hold_s="
                f"{brand['rules']['end_card_hold_s']}"
            )

    return errors


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("pemakaian: validate.py <folder-episode>", file=sys.stderr)
        return 2
    episode_dir = Path(argv[1]).expanduser().resolve()
    if not episode_dir.is_dir():
        print(f"bukan direktori: {episode_dir}", file=sys.stderr)
        return 2
    errors = validate(episode_dir)
    if errors:
        print(f"FAIL  {episode_dir.name}  ({len(errors)} masalah)")
        for e in errors:
            print(f"  - {e}")
        return 1
    print(f"PASS  {episode_dir.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
