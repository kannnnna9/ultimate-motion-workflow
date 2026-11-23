from __future__ import annotations

import copy
import json
import os
import secrets
import sqlite3
import threading
from pathlib import Path
from typing import Any


class DomainError(Exception):
    def __init__(self, code: str, message: str = ""):
        self.code = code
        super().__init__(f"{code}{': ' + message if message else ''}")


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
                PRIMARY KEY(job_id, rev, stage)
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
            for name in ("message_id", "chat_id"):
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

    def create(self, brief: str) -> dict:
        if not isinstance(brief, str) or not brief.strip():
            raise DomainError("INVALID_PAYLOAD", "brief")
        job_id = secrets.token_urlsafe(12)
        payload = {"brief": brief, "cues": {}}
        rev_dir = self._job_dir(job_id) / "revisions" / "1"
        rev_dir.mkdir(parents=True)
        (rev_dir / "input.json").write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO jobs (job_id, brief, rev, status, error, payload, stage, origin) VALUES (?,?,?,?,?,?,?,?)",
                       (job_id, brief, 1, "draft", None, json.dumps(payload), "intake", "generated"))
            db.execute("INSERT INTO revisions VALUES (?,?,?)", (job_id, 1, json.dumps(payload)))
            db.commit()
        return self.get(job_id)

    def revise(self, job_id: str, rev: int, patch: dict) -> dict:
        if not isinstance(patch, dict):
            raise DomainError("INVALID_PAYLOAD", "patch")
        with self._lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._job(db, job_id)
            self._check_rev(row, rev)
            payload = json.loads(row["payload"])
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
            new_rev = rev + 1
            rollback = None
            for field, target in DOC_REVISION_ROLLBACK.items():
                if field in patch and payload.get(field) != json.loads(row["payload"]).get(field):
                    rollback = target if rollback is None else min(rollback, target, key=stage_index)
            rev_dir = self._job_dir(job_id) / "revisions" / str(new_rev)
            rev_dir.mkdir(parents=True)
            (rev_dir / "input.json").write_text(encoded, encoding="utf-8")
            db.execute("INSERT INTO revisions VALUES (?,?,?)", (job_id, new_rev, json.dumps(payload)))
            if rollback is None:
                rollback = row["stage"]
            db.execute("UPDATE jobs SET rev=?, payload=?, stage=?, status='draft', error=NULL WHERE job_id=?", (new_rev, json.dumps(payload), rollback, job_id))
            db.commit()
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
        with self._db() as db:
            row = self._job(db, job_id); self._check_rev(row, rev)
            art = db.execute("SELECT * FROM artifacts WHERE id=? AND job_id=? AND rev=?", (artifact_id, job_id, rev)).fetchone()
            if not art or not self._safe(art["path"]).is_file():
                raise DomainError("INVALID_ARTIFACT", "artifact binding")
            expected = STAGE_KIND.get(stage)
            if not expected or art["kind"] != expected:
                raise DomainError("INVALID_APPROVAL", stage)
            required = APPROVAL_PREVIOUS_STAGE.get(stage)
            if required is None or row["stage"] != required:
                raise DomainError("APPROVAL_STAGE", f"{stage} requires {required} (at {row['stage']})")
            db.execute("INSERT OR REPLACE INTO approvals VALUES (?,?,?,?,?,?)", (job_id, rev, stage, artifact_id, evidence["message_id"], evidence["chat_id"]))
            target = STAGE_NEXT.get(stage)
            if target is not None:
                seq = db.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM stage_history WHERE job_id=?", (job_id,)).fetchone()[0]
                db.execute("INSERT INTO stage_history VALUES (?,?,?,?,?,?,?)", (job_id, rev, seq, f"approve_{stage}", row["stage"], target, None))
                db.execute("UPDATE jobs SET stage=? WHERE job_id=?", (target, job_id))
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
            if row["stage"] != required:
                raise DomainError("STAGE_ORDER", f"{target} requires stage {required} (at {row['stage']})")
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
        art = db.execute("""SELECT a.path FROM approvals p JOIN artifacts a
            ON a.id=p.artifact_id AND a.job_id=p.job_id AND a.rev=p.rev
            WHERE p.job_id=? AND p.rev=? AND p.stage=? AND a.kind=?""",
            (job_id, rev, stage, kind)).fetchone()
        try:
            return art is not None and self._safe(art["path"]).is_file()
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

    def finish(self, run_id, artifact, error):
        with self._lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            run = db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if not run or run["status"] != "running":
                raise DomainError("INVALID_STATE", "run")
            artifact_row = None
            status = "failed" if error is not None else "finished"
            if status == "finished":
                run_dir = self.root / "runs" / run_id
                relative = Path(artifact) if artifact else Path(".")
                p = (run_dir / relative).resolve()
                if relative.is_absolute() or not p.is_relative_to(run_dir) or not p.is_file():
                    raise DomainError("INVALID_ARTIFACT", "run output")
                artifact_id = secrets.token_urlsafe(12)
                rel = p.relative_to(self.root).as_posix()
                db.execute("INSERT INTO artifacts VALUES (?,?,?,?,?)", (artifact_id, run["job_id"], run["rev"], run["target"], rel))
                artifact_row = {"id": artifact_id, "job_id": run["job_id"], "rev": run["rev"], "kind": run["target"], "path": rel}
                db.execute("UPDATE runs SET artifact_id=? WHERE run_id=?", (artifact_id, run_id))
            db.execute("UPDATE runs SET status=?, error=? WHERE run_id=?", (status, error, run_id))
            if status == "finished":
                next_stage = {"style_frame": "style_frame", "preview": "preview", "final": "final_ready"}.get(run["target"])
                if next_stage:
                    db.execute("UPDATE jobs SET stage=? WHERE job_id=? AND rev=? AND stage=?",
                               (next_stage, run["job_id"], run["rev"], TARGET_STAGE[run["target"]]))
            db.execute("UPDATE jobs SET status=?, error=? WHERE job_id=? AND rev=?",
                       (status, error, run["job_id"], run["rev"]))
            db.commit()
        result = self.get_run(run_id)
        if artifact_row: result["artifact"] = artifact_row
        return result

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
            arts = [dict(a) for a in db.execute("SELECT id,rev,kind,path FROM artifacts WHERE job_id=? ORDER BY rowid", (job_id,))]
            history = [dict(h) for h in db.execute(
                "SELECT rev, seq, action, from_stage, to_stage, note FROM stage_history WHERE job_id=? ORDER BY seq",
                (job_id,))]
            return {"job_id": row["job_id"], "rev": row["rev"], "status": row["status"],
                    "error": row["error"], "artifacts": arts,
                    "stage": row["stage"], "origin": row["origin"], "stage_history": history}
