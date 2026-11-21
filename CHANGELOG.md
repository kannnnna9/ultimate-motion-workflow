# Changelog

Perubahan yang terlihat oleh pengguna dicatat di sini mengikuti [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) dan [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

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

### Changed

- **ROADMAP** — v1.x, v3, dan v4+ tidak lagi ditampilkan sebagai fase terjadwal. Ketiganya dipindahkan ke bagian "Menunggu keputusan operator" karena desain hanya menyebutnya bersyarat atau eksploratif, bukan komit.
- **README** — status implementasi: sebelumnya "implementasi belum dimulai" dengan daftar artefak hilang, sekarang "M1 selesai; M2–M6 belum" dengan daftar artefak yang sudah ada. Bagian referensi menautkan `THIRD_PARTY_NOTICES.md` dan lokasi vendor.

### Notes

Belum ada versi yang dirilis. Fasilitas repo (M1) sudah ada, tetapi belum ada satu pun bukti render. Entri versi ditambahkan saat v1.0 memenuhi kriteria selesai di [ROADMAP.md](ROADMAP.md) bagian A — bukan sebelum.

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
