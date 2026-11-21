#!/usr/bin/env python3
"""Audit research checkouts; dry-run by default, quarantine is reversible."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True, stderr=subprocess.DEVNULL).strip()


def plan_cleanup(manifest_path: Path) -> dict:
    data = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    eligible, rejected = [], []
    for item in data.get("checkouts", []):
        source_id, root = item.get("source_id"), Path(item.get("path", "")).expanduser()
        reasons = []
        if not root.is_absolute() or not root.is_dir() or not (root / ".git").exists():
            reasons.append("not_git_root")
        else:
            try:
                if _git(root, "status", "--porcelain", "--untracked-files=all"):
                    reasons.append("dirty")
                if _git(root, "rev-parse", "HEAD") != item.get("sha"):
                    reasons.append("sha_changed")
                if item.get("remote") != _git(root, "remote", "get-url", "origin") if item.get("remote") else False:
                    reasons.append("remote_changed")
                if _git(root, "symbolic-ref", "-q", "HEAD") != item.get("ref"):
                    reasons.append("ref_changed")
            except subprocess.CalledProcessError:
                reasons.append("git_probe_failed")
        if reasons:
            rejected.append({"source_id": source_id, "reasons": reasons})
        else:
            eligible.append(source_id)
    return {"eligible": eligible, "rejected": rejected}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--quarantine", type=Path)
    parser.add_argument("--apply", action="store_true", help="move eligible roots into quarantine")
    args = parser.parse_args(argv)
    result = plan_cleanup(args.manifest)
    print(json.dumps(result, indent=2))
    if args.apply:
        if not args.quarantine:
            raise SystemExit("--apply requires --quarantine")
        data = json.loads(args.manifest.read_text(encoding="utf-8"))
        args.quarantine.mkdir(parents=True, exist_ok=True)
        for item in data.get("checkouts", []):
            if item.get("source_id") in result["eligible"]:
                source = Path(item["path"]).expanduser()
                target = args.quarantine / item["source_id"]
                if target.exists():
                    raise SystemExit(f"quarantine target exists: {target}")
                os.rename(source, target)
                print(f"QUARANTINED {item['source_id']} → {target}")
    else:
        print("DRY RUN: no checkout changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(__import__("sys").argv[1:]))
