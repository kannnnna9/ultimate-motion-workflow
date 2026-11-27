from __future__ import annotations

import copy
import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import stat
import threading
from pathlib import Path
from typing import Any


class DomainError(Exception):
    def __init__(self, code: str, message: str = ""):
        self.code = code
        super().__init__(f"{code}{': ' + message if message else ''}")


def read_bounded_regular_file(path: str | Path, max_bytes: int, *, error_code: str = "INVALID_SNAPSHOT") -> bytes:
    if type(max_bytes) is not int or max_bytes < 1:
        raise ValueError("max_bytes must be a positive integer")
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
                     | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1
                or info.st_mode & 0o002 or info.st_size < 1 or info.st_size > max_bytes):
            raise DomainError(error_code, "revision file is not a bounded user-owned regular file")
        stream = os.fdopen(fd, "rb")
        fd = None
        with stream:
            data = stream.read(max_bytes + 1)
        if len(data) != info.st_size or len(data) > max_bytes:
            raise DomainError(error_code, "revision file changed or exceeded its byte limit")
        return data
    except DomainError:
        raise
    except OSError as exc:
        raise DomainError(error_code, "revision file is unavailable or unsafe") from exc
    finally:
        if fd is not None:
            os.close(fd)


def _write_private_file(path: str | Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0), 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)


# The style is chosen before the script is written: a script can only be
# written to fit a visual register once that register is known, so approving
# the script first forced the writer to guess the look and patch it afterwards.
STAGES = (
    "intake",
    "brief_draft",
    "brief_approved",
    "research_draft",
    "research_approved",
    "style_approved",
    "naskah_draft",
    "naskah_approved",
    "storyboard",
    "style_frame",
    "preview",
    "final_ready",
)

_STAGE_INDEX = {name: index for index, name in enumerate(STAGES)}

# A render target may only start once the job has reached this stage.
TARGET_STAGE = {
    "brief": "brief_draft",
    "research": "research_draft",
    "naskah": "naskah_draft",
    "style_decision": "style_approved",
    "storyboard": "naskah_approved",
    "style_frame": "storyboard",
    "preview": "style_frame",
    "final": "final_ready",
}

APPROVAL_PREVIOUS_STAGE = {
    "brief": "brief_draft",
    "research": "research_draft",
    "naskah": "naskah_draft",
    "storyboard": "storyboard",
    "script_style": "style_frame",
    "preview": "preview",
}

# Approving a stage produces the artifact kinds listed here.
STAGE_KIND = {
    "brief": "brief",
    "research": "research",
    "naskah": "naskah",
    "style": "style_decision",
    "storyboard": "storyboard",
    "script_style": "style_frame",
    "preview": "preview",
}

# Approving a stage advances the job to this stage.
STAGE_NEXT = {
    "brief": "brief_approved",
    "research": "research_approved",
    "naskah": "naskah_approved",
    "style": "style_approved",
    "storyboard": "style_frame",
    "script_style": "style_frame",
    "preview": "final_ready",
}

# A document revision invalidates every approval at or after that stage.
DOC_REVISION_ROLLBACK = {
    "naskah": "naskah_draft",
    "cue": "naskah_draft",
    "cues": "naskah_draft",
    "voice_asset": "naskah_approved",
    "voice_words": "naskah_approved",
    "style_id": "style_approved",
    "brief": "brief_draft",
    "sources": "research_draft",
    # A new storyboard does not invalidate the script, but it does invalidate the
    # frames and preview rendered from the previous one.
    "storyboard": "storyboard",
}


def stage_index(stage: str) -> int:
    try:
        return _STAGE_INDEX[stage]
    except KeyError as exc:
        raise DomainError("INVALID_STAGE", stage) from exc


def _normalize_requirements(requirements):
    if requirements is None:
        return {}
    if not isinstance(requirements, dict):
        raise DomainError("INVALID_REQUIREMENTS", "expected an object")
    try:
        encoded = json.dumps(requirements, sort_keys=True, allow_nan=False)
        if len(encoded.encode("utf-8")) > 65_536:
            raise DomainError("INVALID_REQUIREMENTS", "object exceeds 64 KiB")
        result = json.loads(encoded)
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise DomainError("INVALID_REQUIREMENTS", "requirements must be JSON") from exc
    if "checkpoints" in result:
        checkpoints = result["checkpoints"]
        if (not isinstance(checkpoints, list)
            or any(not isinstance(item, str) or not item for item in checkpoints)
            or len(checkpoints) > 16 or len(set(checkpoints)) != len(checkpoints)):
            raise DomainError("INVALID_REQUIREMENTS", "checkpoints must be unique non-empty strings")
    return result


def _preview_approval_required(payload):
    mode = payload.get("mode", "collaborative")
    if mode == "autonomous":
        return False
    if mode == "directed":
        requirements = payload.get("requirements", {})
        checkpoints = requirements.get("checkpoints", ["preview"]) if isinstance(requirements, dict) else ["preview"]
        return "preview" in checkpoints
    return True


class JobStore:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "jobs.sqlite3"
        self._lock = threading.RLock()
        self._init_db()

    def _db(self):
        db = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def _init_db(self):
        with self._db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY, brief TEXT NOT NULL, rev INTEGER NOT NULL,
                status TEXT NOT NULL, error TEXT, payload TEXT NOT NULL,
                stage TEXT NOT NULL DEFAULT 'intake',
                origin TEXT NOT NULL DEFAULT 'generated'
            );
            CREATE TABLE IF NOT EXISTS stage_history (
                job_id TEXT NOT NULL, rev INTEGER NOT NULL, seq INTEGER NOT NULL,
                action TEXT NOT NULL, from_stage TEXT NOT NULL, to_stage TEXT NOT NULL,
                note TEXT,
                PRIMARY KEY(job_id, seq)
            );
            CREATE TABLE IF NOT EXISTS revisions (
                job_id TEXT NOT NULL, rev INTEGER NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(job_id, rev), FOREIGN KEY(job_id) REFERENCES jobs(job_id)
            );
            CREATE TABLE IF NOT EXISTS artifacts (
                id TEXT PRIMARY KEY, job_id TEXT NOT NULL, rev INTEGER NOT NULL,
                kind TEXT NOT NULL, path TEXT NOT NULL,
                FOREIGN KEY(job_id, rev) REFERENCES revisions(job_id, rev)
            );
            CREATE TABLE IF NOT EXISTS approvals (
                job_id TEXT NOT NULL, rev INTEGER NOT NULL, stage TEXT NOT NULL,
                artifact_id TEXT NOT NULL, message_id TEXT, chat_id TEXT,
                artifact_sha256 TEXT,
                PRIMARY KEY(job_id, rev, stage)
            );
            CREATE TABLE IF NOT EXISTS revision_impacts (
                job_id TEXT NOT NULL, rev INTEGER NOT NULL, impact TEXT NOT NULL,
                PRIMARY KEY(job_id, rev),
                FOREIGN KEY(job_id, rev) REFERENCES revisions(job_id, rev)
            );
            CREATE TABLE IF NOT EXISTS revision_locks (
                job_id TEXT NOT NULL, lock_rev INTEGER NOT NULL, scope TEXT NOT NULL,
                protected_sha256 TEXT NOT NULL,
                PRIMARY KEY(job_id, scope),
                FOREIGN KEY(job_id, lock_rev) REFERENCES revisions(job_id, rev)
            );
            CREATE TABLE IF NOT EXISTS notes (
                id TEXT PRIMARY KEY, job_id TEXT NOT NULL, rev INTEGER NOT NULL,
                t_ms INTEGER NOT NULL, cue_id TEXT, text TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, rev INTEGER NOT NULL,
                target TEXT NOT NULL, status TEXT NOT NULL, error TEXT,
                snapshot_path TEXT NOT NULL, artifact_id TEXT
            );
            """)
        self._migrate_stage()
        with self._db() as db:
            columns = {row["name"] for row in db.execute("PRAGMA table_info(approvals)")}
            for name in ("message_id", "chat_id", "artifact_sha256"):
                if name not in columns:
                    db.execute(f"ALTER TABLE approvals ADD COLUMN {name} TEXT")

    def _migrate_stage(self):
        """Add the stage columns to a pre-staged database; safe to run repeatedly."""
        with self._db() as db:
            columns = {row["name"] for row in db.execute("PRAGMA table_info(jobs)")}
            if "stage" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN stage TEXT NOT NULL DEFAULT 'intake'")
            if "origin" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN origin TEXT NOT NULL DEFAULT 'generated'")
                for row in db.execute("SELECT job_id, payload FROM jobs").fetchall():
                    try:
                        payload = json.loads(row["payload"])
                    except (TypeError, ValueError):
                        continue
                    if isinstance(payload, dict) and isinstance(payload.get("style_id"), str):
                        db.execute("UPDATE jobs SET stage='style_approved' WHERE job_id=?", (row["job_id"],))

    def _job(self, db, job_id):
        row = db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if not row:
            raise DomainError("NOT_FOUND", "job")
        return row

    def _check_rev(self, row, rev):
        if row["rev"] != rev:
            raise DomainError("STALE_REVISION", f"expected {row['rev']}")

    def _job_dir(self, job_id):
        return self.root / "jobs" / job_id

    def _safe(self, value: str | Path, *, allow_missing=False):
        p = Path(value)
        if not p.is_absolute():
            p = self.root / p
        p = p.resolve(strict=False)
        try:
            p.relative_to(self.root)
        except ValueError:
            raise DomainError("PATH_OUTSIDE_ROOT", str(value))
        if not allow_missing and not p.exists():
            raise DomainError("NOT_FOUND", "path")
        return p

    def _artifact_sha256(self, path: str | Path) -> str:
        safe = self._safe(path)
        fd = None
        try:
            fd = os.open(
                safe, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
            )
            before = os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
                    or before.st_nlink != 1 or before.st_mode & 0o002
                    or before.st_size < 0 or before.st_size > 2 * 1024 * 1024 * 1024):
                raise DomainError("INVALID_ARTIFACT", "approval requires a bounded owned regular file")
            digest = hashlib.sha256()
            total = 0
            with os.fdopen(fd, "rb") as stream:
                fd = None
                while chunk := stream.read(1024 * 1024):
                    total += len(chunk)
                    if total > 2 * 1024 * 1024 * 1024:
                        raise DomainError("INVALID_ARTIFACT", "approval artifact exceeds its byte limit")
                    digest.update(chunk)
                after = os.fstat(stream.fileno())
            if (total != before.st_size or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                    != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
                raise DomainError("INVALID_ARTIFACT", "approval artifact changed while hashing")
            return digest.hexdigest()
        except DomainError:
            raise
        except OSError as exc:
            raise DomainError("INVALID_ARTIFACT", "approval artifact is unavailable or unsafe") from exc
        finally:
            if fd is not None:
                os.close(fd)

    @staticmethod
    def _record_impact(db, job_id: str, rev: int, decision: dict) -> None:
        encoded = json.dumps(decision, sort_keys=True, allow_nan=False)
        db.execute("INSERT INTO revision_impacts VALUES (?,?,?)", (job_id, rev, encoded))

    @staticmethod
    def _check_locks(db, job_id: str, candidate: dict) -> None:
        from .revision import scope_hash

        locks = db.execute(
            "SELECT lock_rev, scope, protected_sha256 FROM revision_locks WHERE job_id=? ORDER BY scope",
            (job_id,),
        ).fetchall()
        for lock in locks:
            try:
                digest = scope_hash(candidate, lock["scope"])
            except DomainError as exc:
                raise DomainError("LOCK_CONFLICT", f"{lock['scope']} is locked from revision {lock['lock_rev']}") from exc
            if digest != lock["protected_sha256"]:
                raise DomainError("LOCK_CONFLICT", f"{lock['scope']} is locked from revision {lock['lock_rev']}")

    def _read_verified_custom_revision(self, job_id: str, source_rev: int, payload: dict):
        from .composition import (
            MAX_COMPOSITION_BYTES, MAX_PROJECT_BYTES, read_pinned_asset, read_pinned_audio,
            validate_asset_manifest, validate_audio_manifest,
        )

        composition = payload.get("composition")
        if not isinstance(composition, dict) or composition.get("html_path") != "composition/index.html" \
                or composition.get("project_path") != "project.json":
            raise DomainError("INVALID_SNAPSHOT", "historic composition paths are invalid")
        source_revision = self._job_dir(job_id) / "revisions" / str(source_rev)
        source_dir = source_revision / "composition"
        html_raw = source_dir / "index.html"
        project_raw = source_revision / "project.json"
        if source_revision.is_symlink() or source_dir.is_symlink() or not source_dir.is_dir():
            raise DomainError("INVALID_SNAPSHOT", "historic composition files are unavailable")
        for raw_path in (html_raw, project_raw):
            if self._safe(raw_path) != raw_path:
                raise DomainError("INVALID_SNAPSHOT", "historic composition files are unavailable")
        html_bytes = read_bounded_regular_file(html_raw, MAX_COMPOSITION_BYTES)
        project_bytes = read_bounded_regular_file(project_raw, MAX_PROJECT_BYTES)
        hashes = {
            "html_sha256": hashlib.sha256(html_bytes).hexdigest(),
            "project_sha256": hashlib.sha256(project_bytes).hexdigest(),
        }
        if any(composition.get(field) != digest for field, digest in hashes.items()):
            raise DomainError("INVALID_SNAPSHOT", "historic composition hashes do not match its revision")
        try:
            metadata = json.loads(project_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DomainError("INVALID_SNAPSHOT", "historic project metadata is malformed") from exc
        if not isinstance(metadata, dict) or metadata != composition.get("metadata"):
            raise DomainError("INVALID_SNAPSHOT", "historic project metadata differs from its revision")
        asset_manifest = validate_asset_manifest(metadata.get("assets", []))
        asset_blobs = [(record["path"], read_pinned_asset(source_dir, record)) for record in asset_manifest]
        audio_manifest = metadata.get("audio_tracks", [])
        if audio_manifest:
            if metadata.get("audio_enabled") is not True:
                raise DomainError("INVALID_SNAPSHOT", "historic audio manifest is disabled")
            audio_manifest = validate_audio_manifest(
                audio_manifest,
                video_duration_ms=metadata["duration_frames"] * 1000 // metadata["fps"],
            )
            audio_blobs = [(record["path"], read_pinned_audio(source_dir, record)) for record in audio_manifest]
        else:
            if metadata.get("audio_enabled") is not False:
                raise DomainError("INVALID_SNAPSHOT", "historic audio-enabled project has no pinned tracks")
            audio_blobs = []
        return html_bytes, project_bytes, [*asset_blobs, *audio_blobs]

    @staticmethod
    def _write_custom_revision(destination: Path, content) -> None:
        html_bytes, project_bytes, blobs = content
        copied_source = destination / "composition"
        copied_source.mkdir(mode=0o700)
        _write_private_file(copied_source / "index.html", html_bytes)
        _write_private_file(destination / "project.json", project_bytes)
        for relative_path, data in blobs:
            target = copied_source / relative_path
            target.parent.mkdir(mode=0o700, exist_ok=True)
            _write_private_file(target, data)

    def create(self, brief: str, *, mode="collaborative", requirements=None) -> dict:
        if not isinstance(brief, str) or not brief.strip():
            raise DomainError("INVALID_PAYLOAD", "brief")
        if not isinstance(mode, str) or mode not in {"autonomous", "collaborative", "directed"}:
            raise DomainError("INVALID_MODE", str(mode))
        requirements = _normalize_requirements(requirements)
        job_id = secrets.token_urlsafe(12)
        payload = {"brief": brief, "cues": {}, "mode": mode, "requirements": requirements}
        encoded = json.dumps(payload, sort_keys=True, allow_nan=False)
        rev_dir = self._job_dir(job_id) / "revisions" / "1"
        rev_dir.mkdir(parents=True, mode=0o700)
        _write_private_file(rev_dir / "input.json", encoded.encode("utf-8"))
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO jobs (job_id, brief, rev, status, error, payload, stage, origin) VALUES (?,?,?,?,?,?,?,?)",
                       (job_id, brief, 1, "draft", None, encoded, "intake", "generated"))
            db.execute("INSERT INTO revisions VALUES (?,?,?)", (job_id, 1, encoded))
            db.commit()
        return self.get(job_id)

    def write_composition(self, job_id: str, rev: int, value: dict) -> dict:
        from .composition import (
            MAX_AUDIO_TRACK_BYTES, MAX_PINNED_ASSET_BYTES, MAX_PROJECT_BYTES,
            validate_audio_library, validate_audio_manifest, validate_composition,
        )

        composition = validate_composition(value)
        with self._lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._job(db, job_id)
            self._check_rev(row, rev)
            payload = json.loads(row["payload"])
            original_payload = copy.deepcopy(payload)
            has_composition = isinstance(payload.get("composition"), dict)
            allowed = {"composition_draft", "preview", "final_ready"} if has_composition else {"intake", "naskah_approved"}
            if row["stage"] not in allowed:
                raise DomainError("STAGE_ORDER", "composition can only be written from intake, approved script, or its own route")
            requirements = payload.get("requirements", {})
            checkpoints = requirements.get("checkpoints", []) if isinstance(requirements, dict) else []
            if any(checkpoint != "preview" for checkpoint in checkpoints):
                raise DomainError("UNSUPPORTED_CHECKPOINT", "M1 composition supports the preview checkpoint")

            metadata = {key: item for key, item in composition.items() if key not in {"html", "assets", "audio_tracks"}}
            needs_timing = composition["subtitle_mode"] in {"ass", "web"} or bool(composition["speech_anchors"])
            naskah_hash = None
            naskah = payload.get("naskah")
            if isinstance(naskah, dict):
                try:
                    encoded_naskah = json.dumps(
                        naskah, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
                    ).encode("utf-8")
                except (TypeError, ValueError, UnicodeEncodeError) as exc:
                    raise DomainError("INVALID_NASKAH", "approved naskah is not canonical JSON") from exc
                naskah_hash = hashlib.sha256(encoded_naskah).hexdigest()
            if row["stage"] == "naskah_approved" and naskah_hash is not None:
                metadata["approved_naskah_sha256"] = naskah_hash
            elif needs_timing:
                if naskah_hash is None:
                    raise DomainError("TRANSCRIPT_REQUIRED", "speech timing requires an approved naskah")
                old_metadata = payload.get("composition", {}).get("metadata", {}) if has_composition else {}
                old_hash = old_metadata.get("approved_naskah_sha256") if isinstance(old_metadata, dict) else None
                if old_hash is None:
                    raise DomainError("TRANSCRIPT_REQUIRED", "timing requires a composition pinned from naskah_approved")
                if old_hash != naskah_hash:
                    raise DomainError("APPROVED_SCRIPT_CHANGED", "naskah differs from the approved speech source")
                metadata["approved_naskah_sha256"] = old_hash
            elif has_composition:
                old_metadata = payload.get("composition", {}).get("metadata", {})
                old_hash = old_metadata.get("approved_naskah_sha256") if isinstance(old_metadata, dict) else None
                if old_hash is not None and old_hash == naskah_hash:
                    metadata["approved_naskah_sha256"] = old_hash
            asset_blobs = []
            audio_blobs = []
            asset_refs = composition.get("assets", [])
            if asset_refs:
                from .assets import AssetStore

                asset_store = AssetStore(self.root)
                catalog = {(item["id"], item["version"]): item for item in asset_store.list()}
                expected_bytes = 0
                catalog_records = []
                for reference in asset_refs:
                    catalog_record = catalog.get((reference["id"], reference["version"]))
                    if catalog_record is None:
                        raise DomainError("ASSET_NOT_FOUND", f"{reference['id']} version {reference['version']}")
                    expected_bytes += catalog_record["size_bytes"]
                    if expected_bytes > MAX_PINNED_ASSET_BYTES:
                        raise DomainError("ASSET_BUNDLE_TOO_LARGE", "pinned assets exceed 40 MiB")
                    catalog_records.append(catalog_record)

                asset_manifest = []
                for reference, catalog_record in zip(asset_refs, catalog_records):
                    record, data = asset_store.resolve(reference["id"], reference["version"])
                    if record != catalog_record:
                        raise DomainError("ASSET_CATALOG_CHANGED", "asset metadata changed during resolution")
                    relative_path = f"assets/{record['id']}-v{record['version']}{Path(record['path']).suffix}"
                    pinned = {key: item for key, item in record.items() if key != "path"}
                    pinned["path"] = relative_path
                    asset_manifest.append(pinned)
                    asset_blobs.append((relative_path, data))
                metadata["assets"] = asset_manifest

            audio_refs = composition.get("audio_tracks", [])
            if audio_refs:
                audio_library = validate_audio_library(payload.get("audio_library", []))
                library_by_ref = {record["blob_ref"]: record for record in audio_library}
                audio_manifest = []
                audio_root = self._job_dir(job_id) / "audio_blobs"
                for reference in audio_refs:
                    record = library_by_ref.get(reference["blob_ref"])
                    if (record is None or record["track_id"] != reference["track_id"]
                            or record["kind"] != reference["kind"]):
                        raise DomainError("AUDIO_TRACK_NOT_FOUND", reference["track_id"])
                    blob_path = audio_root / f"{record['blob_ref']}{record['extension']}"
                    try:
                        if blob_path.is_symlink() or blob_path.resolve(strict=True) != blob_path:
                            raise DomainError("AUDIO_INTEGRITY", "audio blob path is not canonical")
                        data = read_bounded_regular_file(
                            blob_path, MAX_AUDIO_TRACK_BYTES, error_code="AUDIO_INTEGRITY",
                        )
                    except OSError as exc:
                        raise DomainError("AUDIO_INTEGRITY", "audio blob is missing or unsafe") from exc
                    if (len(data) != record["size_bytes"]
                            or hashlib.sha256(data).hexdigest() != record["sha256"]):
                        raise DomainError("AUDIO_INTEGRITY", "audio blob differs from its imported hash")
                    relative_path = f"audio/{record['track_id']}{record['extension']}"
                    pinned = {
                        **record,
                        "path": relative_path,
                        **{key: reference[key] for key in (
                            "offset_ms", "trim_start_ms", "trim_end_ms", "gain_db", "fade_in_ms", "fade_out_ms",
                        )},
                    }
                    audio_manifest.append(pinned)
                    audio_blobs.append((relative_path, data))
                metadata["audio_tracks"] = validate_audio_manifest(
                    audio_manifest, video_duration_ms=composition["duration_frames"] * 1000 // composition["fps"],
                )

            html_bytes = composition["html"].encode("utf-8")
            project_bytes = json.dumps(metadata, sort_keys=True, allow_nan=False).encode("utf-8")
            if len(project_bytes) > MAX_PROJECT_BYTES:
                raise DomainError("INVALID_ASSET_MANIFEST", "project metadata exceeds its byte limit")

            new_rev = rev + 1
            payload["composition"] = {
                "html_path": "composition/index.html",
                "project_path": "project.json",
                "html_sha256": hashlib.sha256(html_bytes).hexdigest(),
                "project_sha256": hashlib.sha256(project_bytes).hexdigest(),
                "metadata": metadata,
            }
            encoded = json.dumps(payload, sort_keys=True, allow_nan=False)
            from .revision import impact as revision_impact

            decision = revision_impact(original_payload, payload)
            self._check_locks(db, job_id, payload)
            rev_dir = self._job_dir(row["job_id"]) / "revisions" / str(new_rev)
            rev_dir.mkdir(parents=True, mode=0o700)
            try:
                source_dir = rev_dir / "composition"
                source_dir.mkdir(mode=0o700)
                _write_private_file(source_dir / "index.html", html_bytes)
                for relative_path, data in asset_blobs:
                    destination = source_dir / relative_path
                    destination.parent.mkdir(mode=0o700, exist_ok=True)
                    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(data)
                for relative_path, data in audio_blobs:
                    destination = source_dir / relative_path
                    destination.parent.mkdir(mode=0o700, exist_ok=True)
                    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(data)
                _write_private_file(rev_dir / "project.json", project_bytes)
                _write_private_file(rev_dir / "input.json", encoded.encode("utf-8"))
                db.execute("INSERT INTO revisions VALUES (?,?,?)", (job_id, new_rev, encoded))
                self._record_impact(db, job_id, new_rev, decision)
                seq = db.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM stage_history WHERE job_id=?",
                                 (job_id,)).fetchone()[0]
                db.execute("INSERT INTO stage_history VALUES (?,?,?,?,?,?,?)",
                           (job_id, new_rev, seq, "write_composition", row["stage"],
                            "composition_draft", "custom HTML composition"))
                db.execute("UPDATE jobs SET rev=?, payload=?, stage='composition_draft', status='draft', error=NULL WHERE job_id=?",
                           (new_rev, encoded, job_id))
                db.commit()
            except Exception:
                shutil.rmtree(rev_dir, ignore_errors=True)
                raise
        return self.get(job_id)

    def revise(self, job_id: str, rev: int, patch: dict) -> dict:
        if not isinstance(patch, dict):
            raise DomainError("INVALID_PAYLOAD", "patch")
        with self._lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._job(db, job_id)
            self._check_rev(row, rev)
            original_payload = json.loads(row["payload"])
            if "composition" in patch:
                raise DomainError("USE_WRITE_COMPOSITION", "provide the complete validated composition value")
            payload = copy.deepcopy(original_payload)
            # A new cue document supersedes the per-cue override map: keeping the
            # old overrides would silently re-apply timestamps the new document
            # just replaced, and the cards would drift away from the narration.
            if isinstance(patch.get("cue"), dict) and "cues" not in patch:
                payload.pop("cues", None)
                patch = {k: v for k, v in patch.items() if k != "cues"}
            payload = self._merge(copy.deepcopy(payload), patch)
            cues = payload.get("cues")
            if (not isinstance(payload.get("brief"), str) or not payload["brief"].strip()
                or (cues is not None and not isinstance(cues, dict))
                or any(not isinstance(cue, dict) or type(cue.get("t_ms")) is not int
                       or cue["t_ms"] < 0 for cue in (cues or {}).values())):
                raise DomainError("INVALID_PAYLOAD", "brief or cues")
            try:
                encoded = json.dumps(payload, sort_keys=True, allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise DomainError("INVALID_PAYLOAD", "revision must be JSON") from exc
            from .revision import impact as revision_impact

            decision = revision_impact(original_payload, payload)
            self._check_locks(db, job_id, payload)
            new_rev = rev + 1
            custom = isinstance(original_payload.get("composition"), dict)
            rollback = "composition_draft" if custom else None
            if not custom:
                for field, target in DOC_REVISION_ROLLBACK.items():
                    if field in patch and payload.get(field) != original_payload.get(field):
                        rollback = target if rollback is None else min(rollback, target, key=stage_index)
            rev_dir = self._job_dir(job_id) / "revisions" / str(new_rev)
            rev_dir.mkdir(parents=True, mode=0o700)
            try:
                if custom:
                    from .composition import (
                        MAX_COMPOSITION_BYTES, MAX_PROJECT_BYTES, read_pinned_asset, read_pinned_audio,
                        validate_asset_manifest, validate_audio_manifest,
                    )

                    old_dir = self._job_dir(job_id) / "revisions" / str(rev)
                    old_source_dir = old_dir / "composition"
                    old_html_raw = old_source_dir / "index.html"
                    old_project_raw = old_dir / "project.json"
                    if (old_dir.is_symlink() or old_source_dir.is_symlink()
                            or not old_source_dir.is_dir()):
                        raise DomainError("INVALID_SNAPSHOT", "composition revision files are unavailable")
                    for raw_path in (old_html_raw, old_project_raw):
                        if self._safe(raw_path) != raw_path:
                            raise DomainError("INVALID_SNAPSHOT", "composition revision files are unavailable")
                    html_bytes = read_bounded_regular_file(old_html_raw, MAX_COMPOSITION_BYTES)
                    project_bytes = read_bounded_regular_file(old_project_raw, MAX_PROJECT_BYTES)
                    original_composition = original_payload["composition"]
                    expected_hashes = {
                        "html_sha256": hashlib.sha256(html_bytes).hexdigest(),
                        "project_sha256": hashlib.sha256(project_bytes).hexdigest(),
                    }
                    for field, digest in expected_hashes.items():
                        if field in original_composition and original_composition[field] != digest:
                            raise DomainError("INVALID_SNAPSHOT", f"{field} differs from its revision payload")
                        payload["composition"][field] = digest
                    try:
                        project_metadata = json.loads(project_bytes)
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise DomainError("INVALID_SNAPSHOT", "project metadata is malformed") from exc
                    expected_metadata = original_payload.get("composition", {}).get("metadata")
                    if not isinstance(project_metadata, dict) or project_metadata != expected_metadata:
                        raise DomainError("INVALID_SNAPSHOT", "project metadata differs from its revision payload")
                    asset_manifest = validate_asset_manifest(project_metadata.get("assets", []))
                    asset_blobs = [(record["path"], read_pinned_asset(old_source_dir, record))
                                   for record in asset_manifest]
                    raw_audio_manifest = project_metadata.get("audio_tracks", [])
                    if raw_audio_manifest:
                        if project_metadata.get("audio_enabled") is not True:
                            raise DomainError("INVALID_SNAPSHOT", "audio manifest is disabled in project metadata")
                        audio_manifest = validate_audio_manifest(
                            raw_audio_manifest,
                            video_duration_ms=project_metadata["duration_frames"] * 1000 // project_metadata["fps"],
                        )
                        audio_blobs = [(record["path"], read_pinned_audio(old_source_dir, record))
                                       for record in audio_manifest]
                    else:
                        if project_metadata.get("audio_enabled") is not False:
                            raise DomainError("INVALID_SNAPSHOT", "audio-enabled project has no pinned tracks")
                        audio_blobs = []
                    new_source_dir = rev_dir / "composition"
                    new_source_dir.mkdir(mode=0o700)
                    _write_private_file(new_source_dir / "index.html", html_bytes)
                    _write_private_file(rev_dir / "project.json", project_bytes)
                    for relative_path, data in asset_blobs:
                        destination = new_source_dir / relative_path
                        destination.parent.mkdir(mode=0o700, exist_ok=True)
                        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                        with os.fdopen(fd, "wb") as stream:
                            stream.write(data)
                    for relative_path, data in audio_blobs:
                        destination = new_source_dir / relative_path
                        destination.parent.mkdir(mode=0o700, exist_ok=True)
                        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                        with os.fdopen(fd, "wb") as stream:
                            stream.write(data)
                encoded = json.dumps(payload, sort_keys=True, allow_nan=False)
                _write_private_file(rev_dir / "input.json", encoded.encode("utf-8"))
                db.execute("INSERT INTO revisions VALUES (?,?,?)", (job_id, new_rev, encoded))
                self._record_impact(db, job_id, new_rev, decision)
                if rollback is None:
                    rollback = row["stage"]
                db.execute("UPDATE jobs SET rev=?, payload=?, stage=?, status='draft', error=NULL WHERE job_id=?",
                           (new_rev, encoded, rollback, job_id))
                db.commit()
            except Exception:
                shutil.rmtree(rev_dir, ignore_errors=True)
                raise
        return self.get(job_id)

    def lock_scope(self, job_id: str, rev: int, scope: str) -> dict:
        from .revision import scope_hash

        with self._lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._job(db, job_id)
            self._check_rev(row, rev)
            snapshot = json.loads(row["payload"])
            protected_sha256 = scope_hash(snapshot, scope)
            existing = db.execute(
                "SELECT lock_rev, protected_sha256 FROM revision_locks WHERE job_id=? AND scope=?",
                (job_id, scope),
            ).fetchone()
            if existing is not None:
                if existing["lock_rev"] != rev or existing["protected_sha256"] != protected_sha256:
                    raise DomainError("LOCK_EXISTS", f"{scope} is already locked")
            else:
                db.execute(
                    "INSERT INTO revision_locks(job_id, lock_rev, scope, protected_sha256) VALUES (?,?,?,?)",
                    (job_id, rev, scope, protected_sha256),
                )
            db.commit()
        return {"job_id": job_id, "rev": rev, "scope": scope, "protected_sha256": protected_sha256}

    def unlock_scope(self, job_id: str, rev: int, scope: str, *, evidence=None) -> dict:
        if not isinstance(evidence, dict) or evidence.get("job_id") != job_id \
                or evidence.get("rev") != rev or evidence.get("scope") != scope \
                or not evidence.get("message_id") or not evidence.get("chat_id"):
            raise DomainError("UNLOCK_AUTHORIZATION_REQUIRED", "unlock must authorize this job, revision, and scope")
        with self._lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            lock = db.execute(
                "SELECT lock_rev, scope, protected_sha256 FROM revision_locks WHERE job_id=? AND scope=?",
                (job_id, scope),
            ).fetchone()
            if lock is None:
                raise DomainError("LOCK_NOT_FOUND", scope)
            if lock["lock_rev"] != rev:
                raise DomainError("UNLOCK_AUTHORIZATION_REQUIRED", "unlock must name the locking revision")
            db.execute("DELETE FROM revision_locks WHERE job_id=? AND scope=?", (job_id, scope))
            db.commit()
        return {"job_id": job_id, "rev": rev, "scope": scope, "unlocked": True}

    def rollback(self, job_id: str, rev: int, source_rev: int) -> dict:
        if type(source_rev) is not int or source_rev <= 0:
            raise DomainError("INVALID_REVISION", "source_rev must be a positive revision")
        with self._lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._job(db, job_id)
            self._check_rev(row, rev)
            if source_rev >= rev:
                raise DomainError("INVALID_ROLLBACK", "source_rev must be historic")
            historic = db.execute(
                "SELECT payload FROM revisions WHERE job_id=? AND rev=?", (job_id, source_rev),
            ).fetchone()
            if historic is None:
                raise DomainError("NOT_FOUND", "source revision")
            try:
                payload = json.loads(historic["payload"])
            except (TypeError, ValueError) as exc:
                raise DomainError("INVALID_SNAPSHOT", "historic database revision is malformed") from exc
            if not isinstance(payload, dict):
                raise DomainError("INVALID_SNAPSHOT", "historic database revision is not an object")
            from .composition import MAX_REVISION_BYTES

            source_snapshot = self._job_dir(job_id) / "revisions" / str(source_rev) / "input.json"
            source_snapshot_bytes = read_bounded_regular_file(source_snapshot, MAX_REVISION_BYTES)
            try:
                snapshot_payload = json.loads(source_snapshot_bytes)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise DomainError("INVALID_SNAPSHOT", "historic input snapshot is malformed") from exc
            if snapshot_payload != payload:
                raise DomainError("INVALID_SNAPSHOT", "historic input snapshot differs from the database revision")
            self._check_locks(db, job_id, payload)
            custom_content = None
            custom = isinstance(payload.get("composition"), dict)
            if custom:
                custom_content = self._read_verified_custom_revision(job_id, source_rev, payload)
            new_rev = rev + 1
            rev_dir = self._job_dir(job_id) / "revisions" / str(new_rev)
            rev_dir.mkdir(parents=True, mode=0o700, exist_ok=False)
            try:
                if custom_content is not None:
                    self._write_custom_revision(rev_dir, custom_content)
                encoded = json.dumps(payload, sort_keys=True, allow_nan=False)
                _write_private_file(rev_dir / "input.json", encoded.encode("utf-8"))
                db.execute("INSERT INTO revisions VALUES (?,?,?)", (job_id, new_rev, encoded))
                from .revision import impact as revision_impact

                decision = revision_impact(json.loads(row["payload"]), payload)
                self._record_impact(db, job_id, new_rev, decision)
                target_stage = "composition_draft" if custom else ("brief_draft" if payload.get("brief") else "intake")
                seq = db.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM stage_history WHERE job_id=?", (job_id,)).fetchone()[0]
                db.execute(
                    "INSERT INTO stage_history VALUES (?,?,?,?,?,?,?)",
                    (job_id, new_rev, seq, "rollback", row["stage"], target_stage, f"from revision {source_rev}"),
                )
                db.execute(
                    "UPDATE jobs SET rev=?, payload=?, stage=?, status='draft', error=NULL WHERE job_id=?",
                    (new_rev, encoded, target_stage, job_id),
                )
                db.commit()
            except Exception:
                shutil.rmtree(rev_dir, ignore_errors=True)
                raise
        return self.get(job_id)

    def set_stage(self, job_id, rev, stage, note=""):
        """Move a job to an explicit stage after a side-effect the caller already
        committed (e.g. attaching a storyboard image)."""
        stage_index(stage)
        with self._lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._job(db, job_id)
            self._check_rev(row, rev)
            seq = db.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM stage_history WHERE job_id=?",
                             (job_id,)).fetchone()[0]
            db.execute("INSERT INTO stage_history VALUES (?,?,?,?,?,?,?)",
                       (job_id, rev, seq, "set_stage", row["stage"], stage, note))
            db.execute("UPDATE jobs SET stage=? WHERE job_id=?", (stage, job_id))
            db.commit()
        return self.get(job_id)

    @staticmethod
    def _merge(base, patch):
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(base.get(key), dict):
                base[key] = JobStore._merge(base[key], value)
            else:
                base[key] = copy.deepcopy(value)
        return base

    def annotate(self, job_id, rev, t_ms, cue_id, text):
        if type(t_ms) is not int or t_ms < 0:
            raise DomainError("INVALID_TIMESTAMP", "t_ms")
        if not isinstance(text, str) or not text:
            raise DomainError("INVALID_PAYLOAD", "text")
        with self._db() as db:
            row = self._job(db, job_id); self._check_rev(row, rev)
            cues = json.loads(row["payload"]).get("cues", {})
            if cue_id is not None and cue_id not in cues:
                raise DomainError("UNKNOWN_CUE", cue_id)
            note_id = secrets.token_urlsafe(10)
            db.execute("INSERT INTO notes VALUES (?,?,?,?,?,?)", (note_id, job_id, rev, t_ms, cue_id, text))
        return {"id": note_id, "job_id": job_id, "rev": rev, "t_ms": t_ms, "cue_id": cue_id, "text": text}

    def add_artifact(self, job_id, rev, kind, path, create_empty=False):
        with self._db() as db:
            row = self._job(db, job_id); self._check_rev(row, rev)
        p = self._safe(path, allow_missing=create_empty)
        if create_empty:
            p.parent.mkdir(parents=True, exist_ok=True); p.touch(exist_ok=True)
        if not p.is_file():
            raise DomainError("INVALID_ARTIFACT", "file required")
        artifact_id = secrets.token_urlsafe(12)
        rel = p.relative_to(self.root).as_posix()
        with self._db() as db:
            db.execute("INSERT INTO artifacts VALUES (?,?,?,?,?)", (artifact_id, job_id, rev, kind, rel))
        return {"id": artifact_id, "job_id": job_id, "rev": rev, "kind": kind, "path": rel}

    def approve(self, job_id, rev, stage, artifact_id, *, evidence=None):
        if not isinstance(evidence, dict) or not evidence.get("message_id") or not evidence.get("chat_id"):
            raise DomainError("APPROVAL_EVIDENCE_REQUIRED", "inbound message evidence")
        with self._lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._job(db, job_id); self._check_rev(row, rev)
            art = db.execute("SELECT * FROM artifacts WHERE id=? AND job_id=? AND rev=?", (artifact_id, job_id, rev)).fetchone()
            if not art or not self._safe(art["path"]).is_file():
                raise DomainError("INVALID_ARTIFACT", "artifact binding")
            artifact_sha256 = self._artifact_sha256(art["path"])
            expected = STAGE_KIND.get(stage)
            if not expected or art["kind"] != expected:
                raise DomainError("INVALID_APPROVAL", stage)
            required = APPROVAL_PREVIOUS_STAGE.get(stage)
            if required is None or row["stage"] != required:
                raise DomainError("APPROVAL_STAGE", f"{stage} requires {required} (at {row['stage']})")
            db.execute(
                "INSERT OR REPLACE INTO approvals "
                "(job_id, rev, stage, artifact_id, message_id, chat_id, artifact_sha256) "
                "VALUES (?,?,?,?,?,?,?)",
                (job_id, rev, stage, artifact_id, evidence["message_id"], evidence["chat_id"], artifact_sha256),
            )
            target = STAGE_NEXT.get(stage)
            if target is not None:
                seq = db.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM stage_history WHERE job_id=?", (job_id,)).fetchone()[0]
                db.execute("INSERT INTO stage_history VALUES (?,?,?,?,?,?,?)", (job_id, rev, seq, f"approve_{stage}", row["stage"], target, None))
                db.execute("UPDATE jobs SET stage=? WHERE job_id=?", (target, job_id))
            db.commit()
        return {"job_id": job_id, "rev": rev, "stage": stage, "artifact_id": artifact_id,
                "job_stage": self.stage_of(job_id)}

    def _advance_after_approval(self, job_id, rev, stage):
        """Approving a stage may move the job forward, but never backwards."""
        target = {"brief": "brief_approved", "research": "research_approved",
                  "naskah": "naskah_approved", "style": "style_approved",
                  "script_style": "style_frame", "preview": "preview"}.get(stage)
        if target is None:
            return
        current = self.stage_of(job_id)
        if current == target:
            return
        with self._lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._job(db, job_id)
            if row["stage"] != current or stage_index(target) <= stage_index(current):
                db.commit()
                return
            seq = db.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM stage_history WHERE job_id=?", (job_id,)).fetchone()[0]
            db.execute("INSERT INTO stage_history VALUES (?,?,?,?,?,?,?)",
                       (job_id, rev, seq, f"approve_{stage}", current, target, None))
            db.execute("UPDATE jobs SET stage=? WHERE job_id=?", (target, job_id))
            db.commit()

    def claim(self, job_id, rev, target):
        if target not in TARGET_STAGE:
            raise DomainError("INVALID_TARGET", target)
        with self._lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._job(db, job_id); self._check_rev(row, rev)
            required = TARGET_STAGE[target]
            payload = json.loads(row["payload"])
            self._check_locks(db, job_id, payload)
            custom = isinstance(payload.get("composition"), dict)
            if custom and target == "preview":
                required = "composition_draft"
            elif custom and target == "final" and _preview_approval_required(payload) and row["stage"] == "preview":
                required = "preview"
            if row["stage"] != required:
                raise DomainError("STAGE_ORDER", f"{target} requires stage {required} (at {row['stage']})")
            if custom:
                stage_for_target = "preview" if target == "final" and _preview_approval_required(payload) else None
            else:
                stage_for_target = {"preview": "script_style", "final": "preview"}.get(target)
            if stage_for_target and not self._approved(db, job_id, rev, stage_for_target):
                raise DomainError("APPROVAL_REQUIRED", stage_for_target)
            active = db.execute("SELECT 1 FROM runs WHERE job_id=? AND status='running'", (job_id,)).fetchone()
            if active:
                raise DomainError("ALREADY_RUNNING", job_id)
            run_id = secrets.token_urlsafe(12)
            snap = self._job_dir(job_id) / "revisions" / str(rev) / "input.json"
            self._check_snapshot(snap)
            run_dir = self.root / "runs" / run_id
            run_dir.mkdir(parents=True)
            db.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?)", (run_id, job_id, rev, target, "running", None, snap.relative_to(self.root).as_posix(), None))
            db.execute("UPDATE jobs SET status='running', error=NULL WHERE job_id=?", (job_id,))
            db.commit()
        return self.get_run(run_id)

    def _approved(self, db, job_id, rev, stage):
        kind = {"script_style": "style_frame", "preview": "preview"}[stage]
        art = db.execute("""SELECT a.path, p.artifact_sha256 FROM approvals p JOIN artifacts a
            ON a.id=p.artifact_id AND a.job_id=p.job_id AND a.rev=p.rev
            WHERE p.job_id=? AND p.rev=? AND p.stage=? AND a.kind=?""",
            (job_id, rev, stage, kind)).fetchone()
        if art is None or not isinstance(art["artifact_sha256"], str):
            return False
        try:
            return self._artifact_sha256(art["path"]) == art["artifact_sha256"]
        except (DomainError, OSError, RuntimeError):
            return False

    def _check_snapshot(self, path):
        try:
            safe = self._safe(path)
            if safe == path and safe.is_file():
                return
        except (DomainError, OSError, RuntimeError):
            pass
        raise DomainError("INVALID_SNAPSHOT", "revision input unavailable")

    def stage_of(self, job_id: str) -> str:
        with self._db() as db:
            return self._job(db, job_id)["stage"]

    def advance(self, job_id, rev, action, *, expect, to, note=None, origin=None):
        """Move a job from `expect` to `to`, refusing any other current stage."""
        stage_index(to)
        with self._lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._job(db, job_id)
            self._check_rev(row, rev)
            current = row["stage"]
            if current != expect:
                raise DomainError("STAGE_ORDER", f"{expect} !< {to} (at {current})")
            seq = db.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM stage_history WHERE job_id=?", (job_id,)).fetchone()[0]
            db.execute("INSERT INTO stage_history VALUES (?,?,?,?,?,?,?)",
                       (job_id, rev, seq, action, current, to, note))
            if origin is None:
                db.execute("UPDATE jobs SET stage=? WHERE job_id=?", (to, job_id))
            else:
                db.execute("UPDATE jobs SET stage=?, origin=? WHERE job_id=?", (to, origin, job_id))
            db.commit()
        return self.get(job_id)

    def finish(self, run_id, artifact, error, *, additional_artifacts=None):
        with self._lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            run = db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if not run or run["status"] != "running":
                raise DomainError("INVALID_STATE", "run")
            artifact_row = None
            additional_rows = []
            status = "failed" if error is not None else "finished"
            if status == "finished":
                run_dir = (self.root / "runs" / run_id).resolve()
                if additional_artifacts is None:
                    additional_artifacts = {}
                if not isinstance(additional_artifacts, dict):
                    raise DomainError("INVALID_ARTIFACT", "additional outputs")
                outputs = [(run["target"], artifact)]
                for kind, name in additional_artifacts.items():
                    if not isinstance(kind, str) or not kind or kind == run["target"]:
                        raise DomainError("INVALID_ARTIFACT", "additional output kind")
                    outputs.append((kind, name))
                validated = []
                for kind, name in outputs:
                    relative = Path(name) if name else Path(".")
                    p = (run_dir / relative).resolve()
                    if relative.is_absolute() or not p.is_relative_to(run_dir) or not p.is_file():
                        raise DomainError("INVALID_ARTIFACT", "run output")
                    validated.append((kind, p))
                for kind, p in validated:
                    artifact_id = secrets.token_urlsafe(12)
                    rel = p.relative_to(self.root).as_posix()
                    db.execute("INSERT INTO artifacts VALUES (?,?,?,?,?)",
                               (artifact_id, run["job_id"], run["rev"], kind, rel))
                    row = {"id": artifact_id, "job_id": run["job_id"], "rev": run["rev"],
                           "kind": kind, "path": rel}
                    if kind == run["target"]:
                        artifact_row = row
                        db.execute("UPDATE runs SET artifact_id=? WHERE run_id=?", (artifact_id, run_id))
                    else:
                        additional_rows.append(row)
            elif additional_artifacts:
                raise DomainError("INVALID_ARTIFACT", "failed run cannot publish additional outputs")
            db.execute("UPDATE runs SET status=?, error=? WHERE run_id=?", (status, error, run_id))
            if status == "finished":
                next_stage = {"style_frame": "style_frame", "preview": "preview", "final": "final_ready"}.get(run["target"])
                from_stage = TARGET_STAGE.get(run["target"])
                if run["target"] == "preview":
                    job = db.execute("SELECT payload FROM jobs WHERE job_id=? AND rev=?",
                                     (run["job_id"], run["rev"])).fetchone()
                    if job:
                        payload = json.loads(job["payload"])
                        if isinstance(payload.get("composition"), dict):
                            from_stage = "composition_draft"
                            next_stage = "preview" if _preview_approval_required(payload) else "final_ready"
                if next_stage:
                    db.execute("UPDATE jobs SET stage=? WHERE job_id=? AND rev=? AND stage=?",
                               (next_stage, run["job_id"], run["rev"], from_stage))
            db.execute("UPDATE jobs SET status=?, error=? WHERE job_id=? AND rev=?",
                       (status, error, run["job_id"], run["rev"]))
            db.commit()
        result = self.get_run(run_id)
        if artifact_row:
            result["artifact"] = artifact_row
        if additional_rows:
            result["additional_artifacts"] = additional_rows
        return result

    def get_revision_payload(self, job_id: str, rev: int) -> dict:
        with self._db() as db:
            row = db.execute("SELECT payload FROM revisions WHERE job_id=? AND rev=?", (job_id, rev)).fetchone()
        if row is None:
            raise DomainError("NOT_FOUND", "revision")
        try:
            payload = json.loads(row["payload"])
        except (TypeError, ValueError) as exc:
            raise DomainError("INVALID_SNAPSHOT", "database revision payload is malformed") from exc
        if not isinstance(payload, dict):
            raise DomainError("INVALID_SNAPSHOT", "database revision payload is not an object")
        return payload

    def get_revision_impact(self, job_id: str, rev: int) -> dict | None:
        with self._db() as db:
            self._job(db, job_id)
            row = db.execute(
                "SELECT impact FROM revision_impacts WHERE job_id=? AND rev=?", (job_id, rev),
            ).fetchone()
        if row is None:
            return None
        try:
            value = json.loads(row["impact"])
        except (TypeError, ValueError) as exc:
            raise DomainError("INVALID_SNAPSHOT", "revision impact manifest is malformed") from exc
        if not isinstance(value, dict):
            raise DomainError("INVALID_SNAPSHOT", "revision impact manifest is not an object")
        return value

    def get_run(self, run_id):
        with self._db() as db:
            row = db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if not row: raise DomainError("NOT_FOUND", "run")
            result = dict(row)
            if result["status"] == "running":
                self._check_snapshot(self.root / result["snapshot_path"])
            if result.get("artifact_id"):
                a = db.execute("SELECT * FROM artifacts WHERE id=?", (result["artifact_id"],)).fetchone()
                result["artifact"] = dict(a) if a else None
            return result

    def get(self, job_id):
        with self._db() as db:
            row = self._job(db, job_id)
            try:
                payload = json.loads(row["payload"])
            except (TypeError, ValueError):
                payload = {}
            arts = [dict(a) for a in db.execute("SELECT id,rev,kind,path FROM artifacts WHERE job_id=? ORDER BY rowid", (job_id,))]
            history = [dict(h) for h in db.execute(
                "SELECT rev, seq, action, from_stage, to_stage, note FROM stage_history WHERE job_id=? ORDER BY seq",
                (job_id,))]
            result = {"job_id": row["job_id"], "rev": row["rev"], "status": row["status"],
                      "error": row["error"], "artifacts": arts,
                      "stage": row["stage"], "origin": row["origin"], "stage_history": history,
                      "mode": payload.get("mode", "collaborative"),
                      "requirements": payload.get("requirements", {})}
            if "audio_library" in payload:
                result["audio_library"] = payload["audio_library"]
            return result
