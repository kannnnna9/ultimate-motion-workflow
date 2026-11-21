from __future__ import annotations

import html
import json
import shutil
from pathlib import Path
from typing import Any

from templates.episode.validate import validate as validate_episode


class DomainError(Exception):
    pass


def frame_at(t_ms: int) -> int:
    if not isinstance(t_ms, int) or t_ms < 0:
        raise DomainError("INVALID_TIMESTAMP")
    return (t_ms * 30 + 500) // 1000


def _safe_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DomainError(f"INVALID_MANIFEST: {path}") from exc
    if not isinstance(value, dict):
        raise DomainError("INVALID_MANIFEST")
    return value


def _inside(child: Path, parent: Path, code: str) -> Path:
    resolved = child.resolve()
    try:
        resolved.relative_to(parent.resolve())
    except ValueError as exc:
        raise DomainError(code) from exc
    return resolved


def load_preset(repo_root: Path, style_id: str) -> dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    registry = _safe_json(repo_root / "styles" / "registry.json")
    entries = [x for x in registry.get("styles", []) if isinstance(x, dict) and x.get("id") == style_id]
    if len(entries) != 1:
        raise DomainError("MANIFEST_ID")
    ref = entries[0].get("manifest", f"styles/{style_id}/preset.json")
    if not isinstance(ref, str) or Path(ref).is_absolute():
        raise DomainError("MANIFEST_PATH")
    manifest_path = _inside(repo_root / ref, repo_root, "PATH_OUTSIDE_ROOT")
    manifest = _safe_json(manifest_path)
    required = ("id", "version", "entry", "assets", "palette", "typography", "layout", "motion", "caption_safe_area", "sources")
    if manifest.get("id") != style_id or manifest.get("version") != 1 or any(key not in manifest for key in required):
        raise DomainError("MANIFEST_SCHEMA")
    if not isinstance(manifest["entry"], str) or not isinstance(manifest["assets"], list):
        raise DomainError("MANIFEST_SCHEMA")
    package = manifest_path.parent.resolve()
    entry_path = _inside(package / manifest["entry"], package, "PATH_OUTSIDE_PRESET")
    if entry_path.is_symlink():
        raise DomainError("SYMLINK_ESCAPE")
    for asset in manifest["assets"]:
        if not isinstance(asset, str):
            raise DomainError("MANIFEST_SCHEMA")
        asset_path = _inside(package / asset, package, "PATH_OUTSIDE_PRESET")
        if asset_path.is_symlink():
            raise DomainError("SYMLINK_ESCAPE")
        if not asset_path.is_file():
            raise DomainError("ASSET_NOT_FOUND")
    if not entry_path.is_file():
        raise DomainError("ENTRY_NOT_FOUND")
    deps = manifest.get("dependencies", [])
    if not isinstance(deps, list) or any(isinstance(dep, str) and dep.startswith(("http://", "https://")) for dep in deps):
        raise DomainError("EXTERNAL_DEPENDENCY")
    manifest["_path"] = str(manifest_path)
    manifest["_entry_path"] = str(entry_path)
    return manifest


def _episode(repo_root: Path, episode_dir: Path) -> tuple[dict, dict, dict]:
    episode_dir = Path(episode_dir)
    docs = [_safe_json(episode_dir / name) for name in ("brand.json", "naskah.json", "cue.json")]
    brand, naskah, cue = docs
    validation_errors = [e for e in validate_episode(episode_dir) if "nama folder" not in e]
    if validation_errors:
        raise DomainError("EPISODE_SCHEMA:" + validation_errors[0])
    if not isinstance(brand.get("episode"), str) or brand.get("episode") != episode_dir.name or cue.get("episode") != brand.get("episode"):
        raise DomainError("EPISODE_MISMATCH")
    if not isinstance(naskah.get("beats"), list) or not isinstance(cue.get("cues"), list):
        raise DomainError("EPISODE_SCHEMA:beats/cues")
    beats = {}
    for index, beat in enumerate(naskah["beats"]):
        if not isinstance(beat, dict) or not isinstance(beat.get("id"), str):
            raise DomainError(f"EPISODE_SCHEMA:beats[{index}].id")
        beats[beat["id"]] = beat
    if not beats:
        raise DomainError("NO_BEATS")
    for index, item in enumerate(cue["cues"]):
        if not isinstance(item, dict):
            raise DomainError(f"EPISODE_SCHEMA:cues[{index}]")
        if not isinstance(item.get("id"), str):
            raise DomainError(f"EPISODE_SCHEMA:cues[{index}].id")
        if item.get("beat") not in beats:
            raise DomainError(f"EPISODE_SCHEMA:cues[{index}].beat")
        if not isinstance(item.get("t_ms"), int) or isinstance(item.get("t_ms"), bool) or item["t_ms"] < 0:
            raise DomainError(f"EPISODE_SCHEMA:cues[{index}].t_ms")
    return brand, naskah, cue


def _duration(naskah: dict, cues: list[dict]) -> float:
    estimated = sum(float(b.get("estimated_s", 0)) for b in naskah["beats"])
    cue_end = (max((c["t_ms"] for c in cues), default=0) / 1000) + 4.8
    return max(estimated, cue_end)


def _cards(naskah: dict, cue: dict) -> str:
    cards = []
    for beat in naskah["beats"]:
        beat_cues = [c for c in cue["cues"] if c["beat"] == beat["id"]]
        at = min((c["t_ms"] for c in beat_cues), default=0)
        bid = html.escape(str(beat["id"]), quote=True)
        text = html.escape(str(beat.get("on_screen_text", "")), quote=False)
        narration = html.escape(str(beat.get("narration", "")), quote=False)
        cards.append(f'<article class="beat" data-beat="{bid}" data-at-ms="{at}"><div class="label">{bid.upper()}</div><h2>{text}</h2><p>{narration}</p></article>')
    return "".join(cards)


def _timeline(cue: dict) -> str:
    return "\n".join(f"  addCue({json.dumps(item['id'])}, {frame_at(item['t_ms'])});" for item in cue["cues"])


def _html(manifest: dict, brand: dict, naskah: dict, cue: dict) -> str:
    entry = Path(manifest["_entry_path"]).read_text(encoding="utf-8")
    duration = _duration(naskah, cue["cues"])
    payload_cues = [{**item, "visual_frame": frame_at(item["t_ms"])} for item in cue["cues"]]
    payload = json.dumps({"brand": brand, "cues": payload_cues}, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    replacements = {
        "__UMP_CARDS__": _cards(naskah, cue),
        "__UMP_EPISODE__": payload,
        "__UMP_DURATION__": f"{duration:.2f}".rstrip("0").rstrip("."),
        "__UMP_TIMELINE__": _timeline(cue),
    }
    for token, value in replacements.items():
        entry = entry.replace(token, value)
    return entry


def build_project(repo_root: Path, episode_dir: Path, style_id: str, dst: Path) -> Path:
    manifest = load_preset(repo_root, style_id)
    brand, naskah, cue = _episode(repo_root, episode_dir)
    dst = Path(dst).resolve()
    dst.mkdir(parents=True, exist_ok=True)
    package = Path(manifest["_path"]).parent
    (dst / "index.html").write_text(_html(manifest, brand, naskah, cue), encoding="utf-8")
    stage = dst / "assets"
    stage.mkdir(exist_ok=True)
    for asset in manifest["assets"]:
        shutil.copy2(package / asset, stage / Path(asset).name)
    for source in (episode_dir / "assets").rglob("*"):
        if source.is_file():
            target = stage / source.relative_to(episode_dir / "assets")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    artwork = next((c.get("action", {}).get("params", {}).get("illustration") for c in cue["cues"] if isinstance(c.get("action"), dict)), None)
    if isinstance(artwork, dict) and artwork.get("path"):
        source = episode_dir / artwork["path"] if artwork["path"].startswith("assets/") else None
        if source and source.is_file():
            target = dst / artwork["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    staged_cues = []
    for c in cue["cues"]:
        has_sfx = bool(c.get("sfx"))
        staged_cues.append({"id": c["id"], "beat": c["beat"], "cue_ms": c["t_ms"], "visual_frame": frame_at(c["t_ms"]), "sfx_ms": c["t_ms"] if has_sfx else None, "sfx_offset_frames": c["sfx"].get("offset_frames") if has_sfx else None})
    staging = {"style_id": style_id, "episode": brand["episode"], "beats": naskah["beats"], "cues": staged_cues, "layout": manifest["layout"], "motion": manifest["motion"]}
    (dst / "staging.json").write_text(json.dumps(staging, ensure_ascii=False, indent=2), encoding="utf-8")
    (dst / "meta.json").write_text(json.dumps({"version": 1, "fps": 30, "width": 1080, "height": 1920, "duration_s": _duration(naskah, cue["cues"])}), encoding="utf-8")
    return dst
