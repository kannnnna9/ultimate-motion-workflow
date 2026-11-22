# UMP pipeline repair — Implementation Plan

**Goal:** satu job faktual dari topik sampai MP4 melalui dispatcher publik, aman direvisi dan dicoba ulang.
**Architecture:** SQLite JobStore memiliki kontrak tahap dan transaksi. Dispatcher menangani validasi dokumen dan bukti pesan; worker hanya menyelesaikan run dari snapshot immutable.
**Tech stack:** Python, unittest, SQLite, HyperFrames, ffmpeg/ffprobe.
**Spec:** docs/ultra/specs/2026-11-22-ump-pipeline-repair-design.md @ 137343a.

## Global Constraints
- Pertahankan API, ID, riwayat, preset, dan autentikasi pesan operator.
- Tidak ada persetujuan akhir tambahan.
- Tidak ada pemasangan, push, migrasi data operator, atau pesan Telegram saat pengembangan. Gunakan temporary store dan local VO; pisahkan playback Telegram dari bukti lokal.

## File map dan dependency
A: `ump_core/jobs.py`, `tests/test_jobs.py`, `tests/test_stage_gate.py`, `tests/test_stages.py`: transaksi dan aturan. B: `ump_core/tools.py`, `tests/test_approval_sentence.py`, `tests/test_acceptance.py`: dispatcher dan bukti review. C: `tests/test_halftone_motion_pixels.py`, `scripts/verify_ump.py`, `skills/motion-pipeline/SKILL.md`: media nyata dan dokumentasi. Publikasi artefak tetap melalui worker yang ada. A dan B menyepakati interface sebelum dibangun; C dapat dikerjakan paralel, verifikasi akhir sesudah integrasi.

## Review focus
Artefak salah DM/rev atau pesan dipakai dua kali (B); perubahan cue dan voice dalam patch gabungan (A); worker lama selesai sesudah edit (A); edit bersamaan lewat dua koneksi dan pemulihan DB lama (A); preview tampak sukses tetapi media/caption/VO tidak valid (C).

### A — State machine dan transaksi
1. RED: tambah tes `JobStore` termasuk `assertEqual(store.stage_of(job), 'naskah_draft')` setelah edit naskah disetujui; frame sukses harus memberi style_frame, approve script_style membuka preview, approve preview memberi final_ready. Uji tiap prasyarat negatif, rollback semua field, dua writer, failed/retry, stale finish, reopen DB lama. Jalankan `python3 -m unittest discover -s tests -p test_jobs.py`; rekam kegagalan sebelum edit.
2. Implementasikan aturan spec dalam jobs.py; interface baru `JobStore.revise_action(job_id, rev, patch, action, *, origin=None)` dan perluas `JobStore.approve(job_id, rev, stage, artifact_id, *, evidence=None)`; evidence memuat `message_id`, `chat_id`. Biarkan validasi asal pesan di dispatcher. Revise biasa tidak bergerak maju; action tervalidasi dalam satu transaksi. Catat bukti immutable dan rollback hanya bila nilai berubah. Pertahankan pembacaan format lama, jangan mengarang persetujuan.
3. GREEN: jalankan kembali tes jobs/stage; perbarui ekspektasi lama hanya bila digantikan spec. Commit hanya file A: `fix: unify job stages and revision evidence`.

### B — Dispatcher, brand, dan approval operator
1. RED: jalankan `python3 -m unittest discover -s tests -p test_acceptance.py` setelah menambahkan jalur publik. Contoh assert: `self.assertTrue(brief_response['ok'], brief_response)` untuk brief saja; setelah memilih preset assert snapshot memiliki brand valid; import dari intake harus STAGE_ORDER tanpa mutasi.
2. Di tools.py, gunakan interface A untuk edit bertahap; validasi per target, bentuk brand dari template + style terpilih + override tervalidasi, pertahankan URL dan tolak duplikat source ID. Uji pengaitan review artefak + pesan inbound satu DM, penolakan/replay/ambigu. Jangan mengartikan lookup sebagai review yang terkirim. Kalau API host kurang bukti, fail closed dan laporkan.
3. GREEN: tes dispatcher dari create sampai final memakai mock hanya di batas renderer dan konteks inbound terkontrol; tes edit setelah preview lalu selesai lagi, gagal/retry, dan pembukaan ulang. Jalankan acceptance dan approval_sentence. Commit hanya file B: `fix: validate snapshots and bind review decisions`.

### C — Verifikasi media dan panduan
1. RED: `python3 -m unittest discover -s tests` dalam interpreter tanpa NumPy; cek visual lane juga tidak boleh silently skip karena komposisi belum dibuat. Audit scripts/verify_ump.py agar input bukan job fixture dan tidak menyetel stage langsung.
2. Pindah import opsional ke visual setup; siapkan komposisi sendiri pada visual lane. Ubah runner verifikasi menjadi smoke test menggunakan store scratch, VO lokal, dispatcher dan renderer sungguhan; cocokkan durasi audio dengan preview/final, ffprobe stream 1080×1920, 30fps, H.264/AAC dan PNG. Selaraskan panduan dengan urutan final dan `ump_styles`.
3. GREEN: jalankan suite cepat, suite visual dan real smoke; simpan bukti per lane. Jika alat eksternal tidak tersedia nyatakan BLOCKED secara terpisah. Commit hanya file C: `test: exercise real episode and document production stages`.

## Self-review sebelum implementasi
Pemetaan: spec §1–2 → B, §3 → A+B+C, §4–5 → A+B; acceptance media/dokumen → C+B. Putusan kompatibilitas: autentikasi tetap di B; persisted evidence dan constraint unik di A, supaya dua proses tidak bisa menerima pesan yang sama. Peninjauan menemukan acceptance awal belum menguji run lama selesai dan parser approval ambigu; keduanya dimasukkan ke tes A/B di atas. Verifikasi akhir: full suite di checkout yang sama setelah tiga bagian terintegrasi, tinjau diff dan bandingkan checklist spec. Jangan menghitung tes yang skip sebagai render sukses.
