# Changelog

Perubahan yang terlihat oleh pengguna dicatat di sini mengikuti [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) dan [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **Pipeline bertahap yang tidak bisa dilompati** — job UMP sekarang berjalan `intake → brief_draft → brief_approved → research_draft → research_approved → naskah_draft → naskah_approved → style_approved → style_frame → preview → final_ready`. Lompatan ditolak server dengan `STAGE_ORDER`, bukan hanya dilarang di prompt.
- **Brief disusun UMP dari topik** — `ump_job create` menerima topik, lalu `set_brief` menulis brief kerja (audiens, durasi, sudut cerita) yang harus disetujui operator sebelum riset dimulai.
- **Riset sebagai tahap wajib** — aksi `research` merekam `sources`; tanpa minimal satu sumber naskah tidak bisa ditulis (`RESEARCH_REQUIRED`).
- **Validasi `fact_refs`** — setiap beat wajib mengutip sumber yang sudah direkam; referensi asing atau kosong ditolak dengan `FACT_REFS`.
- **Jalur import naskah** — operator yang sudah punya naskah sendiri memakai `import_naskah`, mendarat di `naskah_draft` dengan `origin: "imported"`, dan tetap wajib melewati approval naskah serta menyertakan sumber.
- **Pemilihan style setelah naskah disetujui** — `select_style` hanya boleh dipanggil di `naskah_approved`, sehingga preset dipilih berdasarkan ritme dan struktur beat yang sudah final.
- **Artifact dokumen dari worker** — target `brief`, `research`, `naskah`, dan `style_decision` menerbitkan JSON reviewable lewat `ump_render` tanpa menjalankan HyperFrames.
- **Approval bertahap** — `UMP APPROVE <job_id> <rev> <stage> <artifact_id>` kini menerima stage `brief`, `research`, `naskah`, `style`, `script_style`, dan `preview`.
- **Migrasi database job** — database job lama ditambahi kolom `stage` dan `origin` secara idempoten; job lama tetap terbaca.

- **Acceptance gate dan cleanup audit** — `minimal-explainer`, `scripts/verify_ump.py`, `tests/test_acceptance.py`, `scripts/cleanup_research.py`, `tests/test_cleanup.py`, serta bukti render dan manifest riset di `docs/verification/`.

- **Style `kinetic-bold` naik ke `ready`** — komposisi `styles/kinetic-bold/index.html` (blok warna, kinetic typography, cut tajam), preview di `styles/kinetic-bold/preview/`, sudah lolos `hyperframes check` 0 error dan dirender 1080×1920 30 fps.
- **`render.py` memilih komposisi dari `style_id`** — tanpa `--project`, komposisi diambil dari `styles/registry.json`. Style berstatus `spec` ditolak dengan pesan jelas.

- **Desain v1** — spesifikasi explainer vertikal 9:16 berbahasa Indonesia lewat Hermes dan HyperFrames, status disetujui operator.
- **README** — status proyek, prasyarat, alur satu episode lima langkah, cara mengembangkan workflow.
- **ROADMAP** — pemisahan tegas antara v1.0 yang ditetapkan, milestone M1–M6, empat arah yang menunggu keputusan (C1–C4), keputusan teknis terbuka (D), dan referensi eksternal yang belum di-vendor (E).
- **CHANGELOG** — dokumen ini.
- **LICENSE** — MIT, © 2026 kannnnna9.
- **THIRD_PARTY_NOTICES.md** — enam sumber rujukan dengan commit, lisensi, file yang disalin dan yang sengaja dilewati. MG Styles 15 dan Anidoodle dicatat tanpa lisensi di upstream.
- **Vendor Bang Motion** — `vendor/bang-motion/`: `SKILL.md`, `references/{explainer,anti-ppt,techniques,architecture}.md`, dan `LICENSE` dari commit `c1aa65e` (v1.19.0). Tanpa `icon.png`, aset, script, atau `.git/`.
- **`.env.example`** — variabel HyperFrames `0.9.72`, VO, caption, output.
- **`.gitignore`** — OS, `node_modules`, `.env` (kecuali `.env.example`), cache, `out/`, media hasil render, dan metadata git upstream di vendor.
- **Folder placeholder** — `templates/episode/`, `scripts/`, `demo/`, `skills/` masing-masing dengan `.gitkeep`.
- **`templates/episode/`** — brand sheet, naskah, dan cue sebagai data JSON terpisah dari komposisi, dengan skema JSON, validator lintas berkas, dan episode contoh `_contoh` yang lulus validasi.
- **`scripts/`** — `setup`, `check`, `caption` (VO + caption per kata), `subs` (ASS), dan `render` (HyperFrames → caption terbakar + audio → verifikasi ffprobe). HyperFrames dipatok `0.9.72`.
- **Demo explainer 9:16** — `demo/explain-9x16/` (data episode) dan `demo/hyperframes/` (komposisi). Tiga beat, VO Edge TTS, caption sinkron, kartu sumber. MP4 1080×1920 30 fps H.264/AAC terukur lewat `ffprobe`.
+- **Portability verifier** — `scripts/verify_portability.py` menguji archive `HEAD` sebagai clean clone; `--render` menjalankan render master dari clone tersebut.
- **Style registry** — `styles/registry.json` mencatat preset `dark-editorial`, `kinetic-bold`, dan `minimal-explainer` (ready), serta `botanical-paper` (spec). Detail aturan tiap preset ada di `styles/<id>/STYLE.md`; brand sheet memilihnya lewat `style_id`.
- **`skills/motion-pipeline/SKILL.md`** — alur Hermes dari pilih style → brand sheet → naskah → VO/caption → cue → preview → render → review.

### Changed

- **`skills/motion-pipeline/SKILL.md`** — alur diubah dari "pilih style dulu" menjadi topik → brief → riset → naskah → style → style frame → preview → final, dengan tabel stage dan penegasan bahwa langkah tidak boleh dilewati.
- **`ump_render`** — target diperluas ke dokumen review (`brief`, `research`, `naskah`, `style_decision`) selain `style_frame`, `preview`, dan `final`.
- **Revisi menurunkan stage** — revisi naskah setelah approval membatalkan approval hilir; style frame tidak bisa di-claim ulang tanpa approval baru.

- **ROADMAP** — v1.x, v3, dan v4+ tidak lagi ditampilkan sebagai fase terjadwal. Ketiganya dipindahkan ke bagian "Menunggu keputusan operator" karena desain hanya menyebutnya bersyarat atau eksploratif, bukan komit.
- **README** — status implementasi: sebelumnya "implementasi belum dimulai" dengan daftar artefak hilang, sekarang "M1 selesai; M2–M6 belum" dengan daftar artefak yang sudah ada. Bagian referensi menautkan `THIRD_PARTY_NOTICES.md` dan lokasi vendor.

### Notes

Belum ada versi yang dirilis. T1–T7 sudah selesai dan 56 test lulus, tetapi entri versi ditambahkan saat v1.0 memenuhi seluruh kriteria selesai di [ROADMAP.md](ROADMAP.md) bagian A — bukan sebelum.

Format saat rilis:

```
## [X.Y.Z] - YYYY-MM-DD

### Added
### Changed
### Deprecated
### Removed
### Fixed
### Security
```

Satu baris per perubahan yang terlihat pengguna. Hapus kategori yang kosong.
