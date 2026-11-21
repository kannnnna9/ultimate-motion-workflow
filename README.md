# Ultimate Motion Workflow

Workflow produksi explainer 2D berbahasa Indonesia dalam format vertikal 9:16. Kamu bekerja dari HP lewat Hermes. Hermes menangani naskah, VO, caption, animasi HyperFrames, preview dan render akhir.

Repo ini menyimpan keputusan editorial, template episode dan tooling dalam satu tempat agar proyek bisa dipindahkan ke VM Linux lain. Naskah, caption dan cue disimpan sebagai data terpisah dari komposisi.

## Status

- **Versi:** `1.2.0` — lihat [CHANGELOG.md](CHANGELOG.md) untuk rilis ini dan aturan penomoran berikutnya.
- **Status:** workflow inti, acceptance gate, dan pipeline bertahap sudah di `main`. **18 preset style di registry, 17 `ready`**; botanical-paper masih `spec`. C1–C4 masih menunggu keputusan operator.

Style yang siap pakai: `dark-editorial`, `kinetic-bold`, `minimal-explainer`, `line-art`, `bauhaus`, `cyber-hud`, `flat-vector`, `collage`, `morph`, `paperclip`, `pixel`, `cel-boil`, `liquid`, `aurora-glass`, `synthwave`, `isometric`. Semuanya komposisi HyperFrames 1080×1920 yang datanya datang dari naskah, bukan template statis.

Dua style MG Styles 15 sengaja belum diadopsi karena butuh jalur render berbeda — `03-isometric` dan `04-3d-render`. Alasannya di [`docs/research/ump/expensive-styles.md`](docs/research/ump/expensive-styles.md) dan rencananya di ROADMAP bagian T10.

Yang ada sekarang: template episode, dua komposisi baseline, preset onboarding, plugin Hermes, demo CSW, verifier acceptance, dan bukti verifikasi di [`docs/verification/ump.md`](docs/verification/ump.md). Checkout riset eksternal tidak dihapus otomatis; status audit dan quarantine tercatat di manifest.

Perintah eksekusi tersedia di `scripts/README.md`; jalur acceptance dan artefak buktinya dicatat di `docs/verification/ump.md`.

## Prasyarat

Target Linux dengan Hermes, Node/npm, Chrome, FFmpeg/ffprobe dan Python/uv. HyperFrames dipatok di `0.9.72`. Preview memakai preset ringan dengan satu worker. Demo dirancang memakai VO lokal yang bisa didistribusikan dan dirender tanpa API key. Episode baru bisa memakai Edge TTS atau VO eksternal dengan transkripsi lokal.

## Alur satu episode

Langkah berjalan berurutan dan tidak bisa dilompati. Server menolak lompatan dengan `STAGE_ORDER`.

Persetujuan **bisa berupa kalimat biasa** ("ya setuju", "oke lanjut", "sip gas"). Kalau kalimatmu ambigu — pertanyaan, pengandaian, atau menyetujui dua tahap sekaligus — UMP meminta kamu menegaskan sekali lagi alih-alih menebak. Bentuk `UMP APPROVE <job_id> <rev> <stage> <artifact_id>` tetap ada sebagai alternatif untuk mengunci persetujuan ke artefak tertentu.

1. **Kirim topik**, bukan brief lengkap. Hermes menyusun brief (audiens, durasi, sudut cerita), lalu kamu menyetujuinya cukup dengan kalimat biasa — "ya setuju" atau "oke lanjut".
2. **Setujui riset.** Hermes mencari sumber nyata dan mencatatnya; naskah tidak bisa ditulis tanpa minimal satu sumber.
3. **Pilih style.** Hermes menawarkan preset yang renderable. Style dipilih **sebelum** naskah, karena naskah ditulis untuk mengisi register visual itu.
4. **Setujui naskah.** Setiap beat wajib punya `fact_refs` yang mengarah ke sumber yang sudah disetujui, dan panjang kalimatnya disesuaikan dengan style. Sudah punya naskah sendiri? Pakai jalur import — tetap wajib style, approval, dan sumber. Hermes lalu menyiapkan VO dan caption bertimestamp, dan mencocokkan ejaan serta timing dengan audio.
5. **Setujui style frame**, lalu tinjau rencana adegan. Setiap beat memuat aksi visual, durasi, transisi dan cue efek suara jika dipakai.
6. **Tonton preview ringan.** Beri revisi dengan timestamp, misalnya `00:04.2 — munculkan label setelah narator menyebut nama tempat`. Revisi naskah membatalkan approval hilir.
7. **Terima MP4 final** 1080×1920, 30 fps, H.264/AAC, beserta kredit dan catatan produksi. QA mencakup pemeriksaan HyperFrames, ffprobe, caption terhadap audio, dan tinjauan frame kunci.

## Mengembangkan workflow

- Tambah preset dengan mendefinisikan style frame, gerak utama/pendukung/latar, dan cue bersama untuk aksi serta SFX.
- Simpan file episode dengan path relatif. Uji render setelah direktori clone dipindah dan ulangi satu revisi bertimestamp untuk memastikan preview ikut berubah.
- Catat sumber, lisensi dan atribusi saat membawa kode atau aset dari luar. Simpan token, aset privat, cache dan render besar di luar Git.
- Catat rencana versi di [Roadmap](ROADMAP.md) dan perubahan yang sudah masuk di [Changelog](CHANGELOG.md).

## Referensi dan keputusan

- **[ROADMAP.md](ROADMAP.md)** — apa yang sudah diputuskan, apa yang masih menunggu keputusan, dan urutan milestone v1.
- **[CHANGELOG.md](CHANGELOG.md)** — perubahan per versi.
- **[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)** — enam sumber rujukan, commit yang diaudit, dan lisensinya.
- `styles/registry.json` — katalog preset style yang bisa dipilih per episode.
- `styles/<id>/STYLE.md` — kontrak visual, gerak, copy, dan checklist preset.
- `skills/motion-pipeline/SKILL.md` — alur Hermes dari pilihan style sampai render dan review.
- **[desain v1](docs/ultra/specs/2026-11-21-ultimate-motion-workflow-design.md)** — spesifikasi dan kriteria penerimaan.

Bang Motion (commit `c1aa65e`, MIT © 2026 Bang Tutorial) di-vendor di [`vendor/bang-motion/`](vendor/bang-motion/): `SKILL.md` dan empat referensi (`explainer`, `anti-ppt`, `techniques`, `architecture`) plus lisensinya. Empat repo rujukan lain (Reanimate, LottieFiles motion-design-skill, MG Styles 15, Huashu Art Motion, Anidoodle) **tidak** di-vendor — daftar commit, lisensi, dan batasan ukuran ada di [ROADMAP.md](ROADMAP.md) bagian E.

