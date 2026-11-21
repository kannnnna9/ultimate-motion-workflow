# UMP staged pipeline — implementation plan

**Goal:** Ganti alur job UMP jadi state machine bertahap yang menolak lompatan langkah, dengan riset dan naskah sebagai gate terpisah sebelum style dipilih.
**Architecture:** Stage disimpan di tabel `jobs` + `stage_history`; setiap mutasi lewat `JobStore.advance` yang memvalidasi pasangan (stage, aksi). Approval jadi per stage dengan artifact turunan. Worker mendapat target dokumen baru selain tiga target media.
**Tech stack:** Python 3.11, sqlite3, unittest. Tanpa dependency baru.
**Spec:** `docs/ultra/specs/2026-11-21-ump-staged-pipeline-design.md`

## Global Constraints

- Kontrak approval Telegram `UMP APPROVE <job_id> <rev> <stage> <artifact_id>` tidak berubah bentuknya.
- Tidak ada dependency Python baru.
- Semua path artefak tetap di dalam `store.root`; validasi containment yang ada tidak dilonggarkan.
- Kode error stabil dan huruf besar: `STAGE_ORDER`, `RESEARCH_REQUIRED`, `INVALID_APPROVAL`, `STALE_REVISION`.
- Suite lama harus tetap hijau: `tests/test_jobs.py`, `tests/test_project.py`, `tests/test_worker.py`, `tests/test_adoptions.py`, `tests/test_acceptance.py`, `tests/test_cleanup.py`.

## Stage order (sumber kebenaran tunggal)

```
intake < brief_draft < brief_approved < research_draft < research_approved
      < naskah_draft < naskah_approved < style_approved < style_frame
      < preview < final_ready
```

## Task 1: Stage state machine di JobStore

**Files:** `ump_core/jobs.py` (modify), `tests/test_stages.py` (create)
**Consumes:** `JobStore.create/revise/claim/approve` yang sudah ada
**Produces:**
- `STAGES: tuple[str, ...]` — urutan di atas
- `JobStore.stage_of(job_id) -> str`
- `JobStore.advance(job_id, rev, action, *, expect: str, to: str) -> dict` — raise `DomainError("STAGE_ORDER", f"{expect} !< {to}")` bila `stage_of != expect`
- `JobStore.get` mengembalikan kunci tambahan `stage`, `origin`, `stage_history`

**Steps:**

- [ ] Step 1: Tulis test di `tests/test_stages.py`: `create` → `stage == "intake"`; `advance(job,"set_brief", expect="intake", to="brief_draft")` berhasil; memanggil ulang `set_brief` saat stage `brief_draft` → `STAGE_ORDER`; `get()["stage_history"][-1]["to"] == "brief_draft"`.
- [ ] Step 2: Jalankan → `python -m unittest tests.test_stages -v`. Expected: FAIL `ImportError: cannot import name 'STAGES'`.
- [ ] Step 3: Implement `STAGES`, kolom `stage`/`origin` di tabel `jobs`, tabel `stage_history`, dan method `stage_of`/`advance` di `ump_core/jobs.py`.
- [ ] Step 4: Jalankan lagi. Expected: PASS.
- [ ] Step 5: `python -m unittest tests.test_jobs -v`. Expected: PASS (regresi).

## Task 2: Migrasi job lama

**Files:** `ump_core/jobs.py` (modify), `tests/test_stages.py` (extend)
**Consumes:** `STAGES` dari Task 1
**Produces:** `_migrate_stage(db)` dipanggil dari `_init_db`; idempoten.

- [ ] Step 1: Tulis test: buat sqlite dengan skema lama tanpa kolom `stage`, isi satu baris job, buka `JobStore`, `stage_of` mengembalikan `style_approved` bila payload punya `style_id`, `intake` bila tidak. Buka dua kali → tidak error.
- [ ] Step 2: Jalankan. Expected: FAIL `sqlite3.OperationalError: no such column: stage`.
- [ ] Step 3: Implement `_migrate_stage` dengan `PRAGMA table_info(jobs)` lalu `ALTER TABLE`.
- [ ] Step 4: Jalankan. Expected: PASS.

## Task 3: Gate per aksi di `ump_job`

**Files:** `ump_core/tools.py` (modify), `tests/test_stages.py` (extend)
**Consumes:** `advance` dari Task 1
**Produces:** action `set_brief`, `research`, `write_naskah`, `import_naskah`, `select_style` di `_job()`, masing-masing dengan pasangan stage yang diizinkan.

- [ ] Step 1: Tulis test tabel kebenaran — untuk setiap action, satu test bahwa stage yang salah menghasilkan `STAGE_ORDER`, dan stage yang benar berhasil.
- [ ] Step 2: Jalankan. Expected: FAIL `DomainError: INVALID_ACTION`.
- [ ] Step 3: Implement `_STAGE_ACTIONS: dict[str, tuple[tuple[str, ...], str]]` (action → stage masuk yang diizinkan, stage keluar) dan pakai di `_job()`.
- [ ] Step 4: Jalankan. Expected: PASS.

## Task 4: Kunci `revise` per stage

**Files:** `ump_core/tools.py` (modify), `tests/test_stages.py` (extend)
**Consumes:** `stage_of` dari Task 1
**Produces:** `_check_patch` menolak `brand`/`naskah`/`cue` bila stage sebelum `research_approved` dengan `EPISODE_REQUIRED`; menyentuh `naskah` setelah `naskah_approved` menurunkan stage ke `naskah_draft` dan menghapus approval stage berikutnya pada rev itu.

- [ ] Step 1: Tulis test: `revise` dengan `naskah` saat stage `intake` → `EPISODE_REQUIRED`. Revisi `naskah` setelah `naskah_approved` → `stage_of == "naskah_draft"`, dan `claim(final)` → `APPROVAL_REQUIRED`.
- [ ] Step 2: Jalankan. Expected: FAIL.
- [ ] Step 3: Implement guard + penurunan stage + `DELETE FROM approvals WHERE job_id=? AND rev>?`.
- [ ] Step 4: Jalankan. Expected: PASS.

## Task 5: `select_style` dijaga naskah

**Files:** `ump_core/tools.py` (modify), `tests/test_stages.py` (extend)
**Consumes:** Task 3
**Produces:** `select_style` sebelum `naskah_approved` → `STAGE_ORDER`; sesudahnya menulis `style_id` dan lanjut `style_approved`; style tidak renderable → `INVALID_STYLE`.

- [ ] Step 1: Tulis test kedua arah plus style non-renderable.
- [ ] Step 2: Jalankan. Expected: FAIL.
- [ ] Step 3: Implement.
- [ ] Step 4: Jalankan. Expected: PASS.

## Task 6: Precondition `ump_render` per target

**Files:** `ump_core/tools.py` (modify), `ump_core/jobs.py` (modify), `tests/test_stages.py` (extend)
**Consumes:** Task 3, 5
**Produces:** `claim` menolak `style_frame` bila stage != `style_approved`; `preview` bila != `style_frame`; `final` bila != `preview`.

- [ ] Step 1: Tulis test tiga target × dua stage salah = enam assertion.
- [ ] Step 2: Jalankan. Expected: FAIL.
- [ ] Step 3: Implement di `JobStore.claim` dengan map `{target: required_stage}`.
- [ ] Step 4: Jalankan. Expected: PASS.

## Task 7: Approval dokumen dan artifact baru

**Files:** `ump_core/jobs.py` (modify), `ump_core/worker.py` (modify), `ump_core/tools.py` (modify), `tests/test_worker.py` (extend)
**Consumes:** Task 6
**Produces:** stage approval `brief`, `research`, `naskah`, `style`; artifact kind `brief`, `research`, `naskah`, `style_decision`; worker target baru menulis JSON snapshot di run workspace.

- [ ] Step 1: Tulis test: approve `brief` dengan artifact kind `brief` → stage `brief_approved`; approve dengan kind salah → `INVALID_APPROVAL`; target `brief` sebelum stage `brief_draft` → `STAGE_ORDER`.
- [ ] Step 2: Jalankan. Expected: FAIL.
- [ ] Step 3: Implement map stage→kind dan target worker baru.
- [ ] Step 4: Jalankan. Expected: PASS.

## Task 8: Research wajib dan `fact_refs` divalidasi

**Files:** `templates/episode/schema/naskah.schema.json` (modify), `templates/episode/validate.py` (modify), `ump_core/tools.py` (modify), `tests/test_stages.py` (extend)
**Consumes:** Task 3
**Produces:** `research` dengan `sources: []` dan `fiction` bukan `true` → `RESEARCH_REQUIRED`; `write_naskah` menolak beat tanpa `fact_refs` atau `fact_refs` yang tidak ada di `sources`.

- [ ] Step 1: Tulis tiga test: sources kosong non-fiksi, fic=true lolos, fact_refs menunjuk ID tidak dikenal → ditolak.
- [ ] Step 2: Jalankan. Expected: FAIL.
- [ ] Step 3: Implement validasi di `_check_patch` dan `write_naskah`.
- [ ] Step 4: Jalankan. Expected: PASS.

## Task 9: Jalur import naskah

**Files:** `ump_core/tools.py` (modify), `tests/test_stages.py` (extend)
**Consumes:** Task 3, 8
**Produces:** `import_naskah` diterima dari `intake`/`brief_draft`/`brief_approved`/`research_approved`, mendarat di `naskah_draft` dengan `origin: "imported"` dan `stage_history` mencatat `skipped: "imported"`.

- [ ] Step 1: Tulis test: import dari `intake` → stage `naskah_draft`, `origin == "imported"`, ada entri skipped. Import langsung ke `naskah_approved` mustahil (tidak ada action).
- [ ] Step 2: Jalankan. Expected: FAIL.
- [ ] Step 3: Implement.
- [ ] Step 4: Jalankan. Expected: PASS.

## Task 10: Skill mengikuti state machine

**Files:** `skills/motion-pipeline/SKILL.md` (modify)
**Consumes:** seluruh action dari Task 3–9
**Produces:** alur ditulis ulang dengan tabel stage → action → perintah operator.

- [ ] Step 1: Tulis test di `tests/test_stages.py`: baca `skills/motion-pipeline/SKILL.md`, pastikan memuat setiap action di `_STAGE_ACTIONS` dan memuat string `STAGE_ORDER`.
- [ ] Step 2: Jalankan. Expected: FAIL.
- [ ] Step 3: Tulis ulang bagian alur di SKILL.md.
- [ ] Step 4: Jalankan. Expected: PASS.

## Task 11: Verifikasi penuh

- [ ] Step 1: `python -m unittest discover -s tests -p 'test_*.py' -q` → semua lulus.
- [ ] Step 2: `scripts/verify_ump.py --checkout . --report ...` → PASS.
- [ ] Step 3: `scripts/probe_hermes_plugin.py --repo . --render` → semua PASS.
- [ ] Step 4: Review diff, lalu commit.

## Review Focus

1. **Lompatan stage** — setiap pasangan (stage, action) terlarang punya test.
2. **Approval basi** — revisi setelah approval membatalkan approval hilir.
3. **Import sebagai celah** — import tidak bisa mendarat melewati `naskah_draft`.
4. **Migrasi** — job lama tidak kehilangan artefak atau melempar error saat dibuka dua kali.
5. **Regresi render** — tiga target lama tetap jalan untuk job yang sudah `style_approved`.
