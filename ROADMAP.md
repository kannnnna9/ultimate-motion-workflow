# Roadmap

Baseline 2026-11-21. Dokumen ini memisahkan **yang sudah diputuskan operator** dari **yang masih menunggu keputusan**. Versi yang belum diputuskan tidak ditampilkan sebagai rencana — hanya sebagai usulan dengan pertanyaan yang harus dijawab dulu.

Sumber keputusan: `docs/ultra/specs/2026-11-21-ultimate-motion-workflow-design.md` (status: disetujui operator).

---

## A. Ditetapkan — v1.0

Satu-satunya versi yang sudah disetujui dan boleh dikerjakan.

**Hasil:** satu workflow Hermes untuk explainer 2D vertikal 9:16 berbahasa Indonesia, dari brief di HP sampai MP4 final. HyperFrames sebagai renderer tunggal. Naskah, caption, dan cue disimpan sebagai data terpisah dari komposisi agar bisa dipakai ulang oleh renderer lain di masa depan.

**Keputusan operator yang mengikat v1:**

- Fokus explainer saja; format lain menyusul di versi berikutnya.
- Kelima repo publik (Reanimate, LottieFiles motion-design-skill, MG Styles 15, Huashu Art Motion, Anidoodle) diintegrasikan sebagai *teknik* ke satu workflow — bukan dipasang mentah sebagai dependency penuh.
- Hasil akhir berupa satu repo GitHub milik operator, bisa di-clone ke device lain.
- Hermes menggantikan peran coding agent pada tutorial Threads.
- HyperFrames menjalankan render/preview; Remotion tidak dipakai di v1.

**Dependensi:** Linux, Node, Chrome, FFmpeg/ffprobe, Python/uv, HyperFrames `0.9.72`, serta VO dan aset demo yang boleh didistribusikan.

**Non-goals v1 (ditolak eksplisit):** Remotion Studio sebagai renderer aktif · migrasi episode Blok M · promo/opener/bumper · publishing otomatis ke platform sosial · dukungan resmi Windows/macOS (target Linux).

**Kriteria selesai (bukti, bukan klaim):**

1. Setup dari clean clone di direktori terisolasi berjalan.
2. Satu demo utuh: minimal dua adegan, VO, caption sinkron, kartu sumber, dirender tanpa API key.
3. `ffprobe` mengonfirmasi 1080×1920, 30 fps, H.264/AAC; frame kunci ditinjau.
4. Rename/move direktori clone tidak memutus aset (path relatif).
5. Satu perubahan cue menggeser aksi **dan** SFX bersamaan.
6. Satu revisi bertimestamp menghasilkan preview baru yang memperlihatkan perubahan.
7. Isi Git hanya source, contoh yang boleh dibagikan, dan docs.
8. Setelah publikasi: URL GitHub dan commit ter-push terverifikasi.

**Status sekarang:** workflow inti, plugin Hermes, acceptance gate, dan demo CSW sudah di-commit dan merge ke `main` (`edd6e63`). Pipeline job kini berjalan bertahap dan menolak lompatan langkah. Bukti aktual ada di `docs/verification/ump.md`; cleanup riset memakai quarantine reversible dan tidak menghapus checkout yang dirty atau partial.

**Kriteria 8 (publikasi):** repo sudah publik dan commit ter-push terverifikasi — PR #1 merge ke `main`. Kriteria 1–8 lulus untuk alur Hermes.

### Kontrak pipeline bertahap

Job tidak bisa melompati langkah; server menolak dengan `STAGE_ORDER`. Urutan yang berlaku:

| Stage | Masuk lewat | Keluar lewat |
|---|---|---|
| `intake` | `create` dengan topik | `set_brief` |
| `brief_draft` | `set_brief` | `approve brief` |
| `brief_approved` | approval brief | `research` |
| `research_draft` | `research` dengan `sources` | `approve research` |
| `research_approved` | approval riset | `write_naskah` / `import_naskah` |
| `naskah_draft` | `write_naskah` | `approve naskah` |
| `naskah_approved` | approval naskah | `select_style` |
| `style_approved` | `select_style` | `ump_render` → `style_frame` |
| `style_frame` | render style frame | `approve script_style` |
| `preview` | render preview | `approve preview` |
| `final_ready` | approval preview | — |

`import_naskah` adalah satu-satunya jalur alternatif: mendarat di `naskah_draft` dengan `origin: "imported"` dan tetap wajib approval naskah.

---

## B. Milestone v1 — status

M1–M6 selesai. T1–T7 selesai. Yang tersisa di v1.0 adalah C1–C4 yang masih menunggu keputusan operator, bukan pekerjaan terjadwal.

| # | Milestone | Bukti selesai |
|---|---|---|
| M1 | Struktur repo + `LICENSE` (MIT) + `THIRD_PARTY_NOTICES.md` + vendor Bang Motion | File ada, lisensi keenam sumber tercatat, `references/` Bang Motion masuk repo | ✅ `4a91286` |
| M2 | `templates/episode/` — brand sheet, naskah, cue, struktur folder | Template bisa dipakai untuk satu episode | ✅ `320e1f7` |
| M3 | `scripts/` — setup, check, render, caption (dependency di-pinn) | `setup` jalan dari clean clone | ✅ `6737deb`, `cd35a8e`, `37c51d9` |
| M4 | `skills/motion-pipeline/SKILL.md` — skill Hermes | Skill termuat, memandu alur satu episode | ✅ `8232e64` |
| M5 | `demo/` — explainer dua adegan, VO lokal, caption, kartu sumber | MP4 jadi tanpa API key, ffprobe benar | ✅ render demo 1080×1920, H.264/AAC |
| M6 | Verifikasi portabilitas | Clone dipindah, render ulang lulus | ✅ `scripts/verify_portability.py --render` → clean clone render PASS |

### T6/T7 integration status

Acceptance gate T6 sudah tersedia di `scripts/verify_ump.py`, preset `minimal-explainer` sudah bisa dibangun dan dirender, dan bukti aktual dicatat di `docs/verification/ump.md`. T7 sudah memiliki manifest dan dry-run cleanup. Lima checkout riset ditolak untuk quarantine karena dirty atau partial Git state; tidak ada penghapusan otomatis.

### T8 — pipeline bertahap (selesai, belum dirilis)

| Item | Bukti |
|---|---|
| State machine `STAGES` + `advance`/`claim` gate | `ump_core/jobs.py`, `tests/test_stages.py` |
| Gate aksi `ump_job` (`set_brief`, `research`, `write_naskah`, `import_naskah`, `select_style`) | `ump_core/tools.py`, `tests/test_stage_gate.py` |
| Validasi `fact_refs` terhadap sumber yang direkam | `tests/test_stage_gate.py` |
| Artifact dokumen (`brief`, `research`, `naskah`, `style_decision`) | `ump_core/worker.py`, `tests/test_worker.py` |
| Skill mengikuti alur baru | `tests/test_stage_gate.py::test_skill_documents_every_stage_route` |
| Migrasi database job lama | `tests/test_stages.py::test_legacy_database_is_migrated_idempotently` |

Design dan plan: `docs/ultra/specs/2026-11-21-ump-staged-pipeline-design.md` dan `docs/ultra/plans/2026-11-21-ump-staged-pipeline.md`.

**Belum tercakup:** riset belum otomatis mencari sumber sendiri — Hermes tetap memanggil `web_search`/`web_extract` lalu mengirim hasilnya lewat `research`. Tidak ada verifikasi silang fakta otomatis.

**Kriteria keluar T8:** satu episode penuh dari topik sampai final dijalankan lewat Telegram dengan seluruh approval nyata, dan buktinya dicatat di `docs/verification/ump.md`.

---

## C. Menunggu keputusan operator

Empat arah berikut **belum diputuskan**. Jangan dikerjakan sampai ada jawaban eksplisit.

### C1. Format promo, opener, bumper

- **Yang diusulkan:** preset kedua untuk format pendek — template brief, style frame, ritme, cue SFX, pemeriksaan output.
- **Pertanyaan yang harus dijawab:** *Apakah format ini dipakai untuk produksi nyata, dan format mana yang pertama?* Tanpa brief nyata, preset tidak bisa dirancang.
- **Pemicu:** v1.0 lulus dan ada brief promo/opener sungguhan.

### C2. Mode bedah referensi memakai Reanimate

- **Yang diusulkan:** input video referensi → contact sheet bertimestamp + catatan timing/transisi.
- **Status di spec:** tertulis "kalau dijadwalkan" — artinya **belum diputuskan**, bukan komit.
- **Pertanyaan:** *Punya video referensi berizin yang mau dibedah, atau tunda sampai kebutuhan muncul?*
- **Pemicu:** ada video referensi nyata untuk episode berikutnya.

### C3. Uji jalur Remotion

- **Yang diusulkan:** prototipe Remotion Studio untuk satu explainer, memakai data editorial yang sama dengan demo HyperFrames.
- **Pertanyaan:** *Kebutuhan spesifik apa yang tidak bisa dipenuhi HyperFrames?* Tanpa jawaban ini, uji Remotion hanya menambah beban pemeliharaan renderer kedua.
- **Pemicu:** kebutuhan nyata akan komponen React atau Studio.

### C4. Perpustakaan aset dan karakter berulang

- **Yang diusulkan:** preset seri dan aset yang dipakai ulang antar episode.
- **Pertanyaan:** *Setelah berapa episode kerja berulang mulai terasa mengganggu?*
- **Pemicu:** beberapa episode dengan format sama menunjukkan kerja ulang yang bisa dikurangi.

---

### D. Keputusan teknis yang belum diambil

Bukan roadmap, tapi menghambat M1–M5. Di spec disebut "ditinggal ke builder", artinya operator belum menetapkan.

- Voice default dan preset loudness untuk VO.
- Penamaan script dan parameter CLI.
- Struktur detail folder episode.
- Urutan check di dalam `scripts/check` (lint → snapshot → audio probe).

**Sudah ditetapkan:** Bang Motion dipakai sebagai sumber pegangan desain resmi (lihat E), bukan sekadar skill lepas.

---

## E. Referensi eksternal

**Bang Motion sudah di-clone penuh dan siap di-vendor.** Lima repo lain **belum ada di dalam repo** dan belum di-clone ke disk. Commit yang di-audit 2026-11-21:

| Sumber | Commit | Lisensi | Ukuran | Peran |
|---|---|---|---|---|
| [Bang Motion](https://github.com/bangtutorial/bang-motion) | `c1aa65e` (v1.19.0) | MIT — © 2026 Bang Tutorial | 4,5 MB | **Pegangan desain resmi.** `references/explainer.md` (579 baris) untuk explainer kartun + VO; `anti-ppt.md` (110) anti-slideshow; `techniques.md` (690); `architecture.md` (188) |
| [Reanimate](https://github.com/NiazMorshed2007/reanimate) | `3f7a55a` | MIT | ±60 MB | Bedah animasi frame-by-frame (C2) |
| [LottieFiles motion-design-skill](https://github.com/LottieFiles/motion-design-skill) | `d77a9fc` | MIT | kecil | Checklist koreografi: primary/secondary/ambient, easing, stagger |
| [MG Styles 15](https://github.com/Vincentwei1021/mg-styles-15) | `8fab7eb` | — | ±568 MB | Kartu gaya + pola cue bersama untuk aksi dan SFX |
| [Huashu Art Motion](https://github.com/alchaincyf/huashu-art-motion) | `a97c450` | MIT | ±78 MB | Setujui style frame dulu, petakan beat narasi ke aksi |
| [Anidoodle](https://github.com/alexgreensh/anidoodle) | `4ae27d2` | — | ±49 MB | Ilustrasi procedural deterministik (parameter + seed) |

Clone Bang Motion tersedia di dua tempat:

- `~/.hermes/cache/scratch/bang-motion` — full git clone, ada `.git`
- `~/.hermes/skills/creative/bang-motion` — salinan terpasang sebagai skill Hermes

Saat vendor, ambil `SKILL.md`, `references/explainer.md`, `references/anti-ppt.md`, `references/techniques.md`, `references/architecture.md`, dan `LICENSE`. Jangan bawa `icon.png` (178 KB) ke dalam repo workflow.

**Catatan ukuran:** sisa repo besar (mg-styles-15 ±568 MB, huashu ±78 MB, reanimate ±60 MB, anidoodle ±49 MB). Disk bebas hanya 7,6 GiB. Jangan clone penuh — ambil hanya `SKILL.md` dan `references/` yang relevan, catat commit di `THIRD_PARTY_NOTICES.md`.

Tutorial Threads Part 1–4 hanya tersedia sebagai ringkasan (brand sheet → naskah → VO → caption → rencana scene → preview → revisi timestamp → render), bukan file sumber.

---

## F. Catatan tentang roadmap sebelumnya

Versi terdahulu dokumen ini menampilkan v1.x, v3, dan v4+ sebagai fase terjadwal. Itu keliru: spec hanya menyebut mode Reanimate "kalau dijadwalkan", dan Remotion/perpustakaan aset sebagai eksplorasi. Ketiganya dipindahkan ke bagian C sebagai usulan yang menunggu keputusan, bukan komit.
