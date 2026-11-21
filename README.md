# Ultimate Motion Workflow

Workflow produksi explainer 2D berbahasa Indonesia dalam format vertikal 9:16. Kamu bekerja dari HP lewat Hermes. Hermes menangani naskah, VO, caption, animasi HyperFrames, preview dan render akhir.

Repo ini menyimpan keputusan editorial, template episode dan tooling dalam satu tempat agar proyek bisa dipindahkan ke VM Linux lain. Naskah, caption dan cue disimpan sebagai data terpisah dari komposisi.

## Status

**Desain v1 disetujui. Implementasi belum dimulai.**

Yang ada sekarang: dokumen desain, roadmap, changelog. Yang belum ada: `scripts/`, `templates/`, `demo/`, skill Hermes, `LICENSE`, `THIRD_PARTY_NOTICES.md`. Urutan pengerjaan ada di [ROADMAP.md](ROADMAP.md) bagian B.

Selama implementasi belum jalan, belum ada perintah untuk dijalankan. Petunjuk eksekusi ditambahkan setelah alur berhasil dijalankan dari clone bersih.

## Prasyarat

Target Linux dengan Hermes, Node/npm, Chrome, FFmpeg/ffprobe dan Python/uv. HyperFrames dipatok di `0.9.72`. Preview memakai preset ringan dengan satu worker. Demo dirancang memakai VO lokal yang bisa didistribusikan dan dirender tanpa API key. Episode baru bisa memakai Edge TTS atau VO eksternal dengan transkripsi lokal.

## Alur satu episode

1. Kirim brief berisi topik, audiens, durasi, sumber fakta dan bahan yang boleh dipakai.
2. Setujui naskah dan satu style frame. Hermes menyiapkan VO dan caption bertimestamp, lalu mencocokkan ejaan dan timing dengan audio.
3. Tinjau rencana adegan. Setiap beat memuat aksi visual, durasi, transisi dan cue efek suara jika dipakai.
4. Tonton preview ringan. Beri revisi dengan timestamp, misalnya `00:04.2 — munculkan label setelah narator menyebut nama tempat`.
5. Terima MP4 final 1080×1920, 30 fps, H.264/AAC, beserta kredit dan catatan produksi. QA mencakup pemeriksaan HyperFrames, ffprobe, caption terhadap audio, dan tinjauan frame kunci.

## Mengembangkan workflow

- Tambah preset dengan mendefinisikan style frame, gerak utama/pendukung/latar, dan cue bersama untuk aksi serta SFX.
- Simpan file episode dengan path relatif. Uji render setelah direktori clone dipindah dan ulangi satu revisi bertimestamp untuk memastikan preview ikut berubah.
- Catat sumber, lisensi dan atribusi saat membawa kode atau aset dari luar. Simpan token, aset privat, cache dan render besar di luar Git.
- Catat rencana versi di [Roadmap](ROADMAP.md) dan perubahan yang sudah masuk di [Changelog](CHANGELOG.md).

## Referensi dan keputusan

- **[ROADMAP.md](ROADMAP.md)** — apa yang sudah diputuskan, apa yang masih menunggu keputusan, dan urutan milestone v1.
- **[CHANGELOG.md](CHANGELOG.md)** — perubahan per versi.
- **[desain v1](docs/ultra/specs/2026-11-21-ultimate-motion-workflow-design.md)** — spesifikasi dan kriteria penerimaan.

Lima repo rujukan (Reanimate, LottieFiles motion-design-skill, MG Styles 15, Huashu Art Motion, Anidoodle) **belum** di-vendor ke repo ini. Daftar commit, lisensi, dan batasan ukuran ada di [ROADMAP.md](ROADMAP.md) bagian E.
