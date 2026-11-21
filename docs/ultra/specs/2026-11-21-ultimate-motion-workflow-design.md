# Ultimate Motion Workflow — v1 Design

Status: disetujui (draft ditinjau operator, 2026-11-21). Implementasi menyusul lewat implementation plan terpisah.

## Intent

Satu repo GitHub mandiri milik operator: workflow produksi explainer video vertikal (9:16, bahasa Indonesia) yang dijalankan lewat Hermes. Operator bekerja dari HP: kirim brief, approve naskah dan style frame, terima preview, kasih revisi bertimestamp, terima MP4 final. Repo harus bisa di-clone di device/VM lain dan dijalankan dengan prasyarat yang terdokumentasi.

Latar: workflow lokal di `/home/agentuser/shortform` sudah membuktikan jalur HyperFrames → MP4 (seri Blok M, dua episode, QA frame dan audio terukur). Lima repo publik dan satu seri tutorial Threads memberi pola proses yang diadaptasi ke sini. Workflow ini mengubah pengalaman itu jadi milik operator: terdokumentasi, bisa direproduksi, dan tidak terikat satu VM.

## Goals

- v1 mendukung satu jenis karya: explainer vertikal 2D bernarasi, caption sinkron, kartu sumber di akhir.
- Alur produksi mengikuti pola Threads Part 1–4: brand sheet → naskah → VO → caption bertimestamp → rencana scene → preview → revisi bertimestamp → render.
- Lima repo publik terintegrasi sebagai teknik yang bisa dipakai dan diuji, masing-masing dengan batas jelas (lihat Integrasi).
- Satu renderer di v1: HyperFrames. Jalur Remotion disiapkan datanya, bukan dibangun.
- Portabilitas: setup dari clean clone di Linux + Hermes, path relatif, prasyarat tercekik otomatis (Node, Python/uv, Chrome, FFmpeg).
- Semua klaim "selesai" punya bukti perintah: check HyperFrames, ffprobe, review frame.

## Non-goals (v1)

- Remotion Studio atau komponen React sebagai renderer aktif.
- Migration episode lama Blok M ke repo ini.
- Promo, opener, bumper, film panjang, 3D, karakter procedural berat.
- Publishing otomatis ke platform sosial.
- Dukungan Windows/macOS dijanjikan (target Linux; yang lain best-effort).

## Anti-goals (gagal walau jalan)

- Workflow yang cuma jalan di VM sekarang karena path atau config lokal di-hardcode.
- Klaim "video jadi" tanpa render dan pemeriksaan nyata.
- Menyalin kode/aset upstream tanpa catatan lisensi dan atribusi.
- Dua renderer aktif sebelum ada bukti kebutuhan (maintenace ganda, output tidak konsisten).

## Constraints

- VM: 2 vCPU, RAM ±7,4 GiB, disk kosong ±7,7 GiB saat audit (2026-11-21). Satu worker render, preview lebih ringan dari final, cek disk sebelum render berat.
- HyperFrames CLI di-pinn `0.9.72` dengan cache privat `HYPERFRAMES_CACHE_DIR="$HOME/.hermes/hyperframes-cache"`.
- Edge TTS gratis tapi bergantung jaringan/service; faster-whisper `small` lokal untuk transkrip VO eksternal.
- Lisensi: repo workflow ini MIT; kode/aset yang diambil upstream mengikuti lisensinya masing-masing dan dicatat di `THIRD_PARTY_NOTICES.md`.
- Bukan bagian dari repo: token, sesi Hermes, cache model/browser, aset privat, hasil render besar.

## Approaches considered

1. **HyperFrames dulu (dipilih).** Jalur terbukti di VM ini; hasil pertama menguji proses produksi, bukan migrasi renderer. Biaya: Studio Remotion dan ekosistem React belum tersedia di v1. Mitigasi: naskah, cue, dan timestamp caption disimpan terpisah dari komposisi sehingga jalur Remotion bisa datang tanpa mengulang kerja editorial.
2. **Remotion dulu.** Cocok kalau komponen React dan Studio jadi kebutuhan inti sejak hari pertama. Ditolak untuk v1: renderer + pipeline caption harus dibangun ulang sebelum ada bukti nilai tambahnya.
3. **Dua renderer sejak awal.** Menutup kebutuhan terbesar sekaligus, tapi biaya pemeliharaan dua kali dan output sulit disamakan. Ditolak.

## Keputusan operator

- v1 fokus explainer; promo/opener menyusul.
- Kelima repo diintegrasikan ke satu workflow, bukan dipasang mentah-mentah.
- Hasil akhir satu repo milik operator di GitHub untuk di-clone ke device lain.
- Hermes pengganti peran coding agent di tutorial; HyperFrames menjalankan fungsi render/preview di v1.

## Integrasi (dengan batas)

- **Threads Part 1–4** — alur proses: brand sheet, naskah, VO, caption per kata, rencana scene, preview, revisi timestamp, render. Hermes menggantikan Claude Code/Antigravity.
- **Reanimate** — mode opsional bedah video referensi: contact sheet + catatan timing/transisi yang bisa diperiksa. Untuk revisi output sendiri: bandingkan frame di timestamp sama. Tidak menilai kualitas adaptasi gaya via pixel-match terhadap video referensi.
- **LottieFiles motion-design-skill** — checklist koreografi per shot (primary/secondary/ambient, easing, stagger). Tanpa Creator MCP.
- **MG Styles 15** — kartu gaya flat vector/kolase + pola cue bersama: satu perubahan cue menggerakkan aksi dan SFX serentak. Aset besar upstream tidak dibawa ke v1.
- **Huashu Art Motion** — satu style frame disetujui dulu, shot list memetakan beat VO ke aksi visual. Engine dan konfigurasi suaranya tidak diadopsi di v1.
- **Anidoodle** — ilustrasi procedural deterministik (parameter + seed tetap); satu aset sederhana diuji bisa dirender ulang identik. Engine penuh tidak diadopsi di v1.
- **Bang Motion** (sudah tersimpan sebagai skill) — pegangan anti-slideshow, blocking kamera, pemeriksaan tampilan di frame kunci.

## Struktur repo (target)

```
ultimate-motion-workflow/
  README.md            setup, alur, bukti selesai
  ROADMAP.md           v1 → v2 → v3+ dan pemicunya
  CHANGELOG.md         perubahan per versi (Keep a Changelog)
  THIRD_PARTY_NOTICES.md
  LICENSE              MIT
  skills/motion-pipeline/SKILL.md    skill Hermes untuk workflow ini
  scripts/             setup, check, render, caption (dependency di-pinn)
  templates/episode/   brand sheet, naskah, cue, struktur folder episode
  demo/                explainer demo kecil dengan VO lokal, bisa dirender tanpa API key
  docs/ultra/specs/    dokumen desain (file ini) dan plan
```

## Proof of done (v1)

- Setup dari clean clone di direktori terisolasi; jalan dengan konfigurasi proyek itu sendiri.
- Satu explainer demo utuh: minimal dua adegan, VO, caption, kartu sumber; lulus check HyperFrames; dirender MP4; ffprobe benar (1080×1920, H.264/AAC); frame kunci ditinjau.
- Rename/move direktori clone tidak memutus aset (path relatif).
- Satu perubahan cue menggeser aksi dan SFX bersamaan di demo.
- Satu revisi bertimestamp menghasilkan preview baru yang memperlihatkan perubahan.
- Isi Git hanya source, contoh yang boleh dibagikan, dan docs.
- Setelah tahap publish: URL GitHub dan commit ter-push terverifikasi.

## Ditinggal ke builder

- Penamaan script, parameter CLI, dan struktur detail folder episode.
- Pemilihan voice default dan preset loudness.
- Urutan check di dalam script `check` (lint → snapshot → audio probe).
- Implementasi mode ref-render (Reanimate) kalau dijadwalkan di v1.x.

## Rujukan keputusan lanjutan

Arah v2, v3, dan seterusnya didokumentasikan di `ROADMAP.md` bersama pemicu/produk bukti untuk tiap versi. Roadmap bukan janji tanggal; versi berikutnya dibuka ketika pemicunya terpenuhi di dunia nyata.
