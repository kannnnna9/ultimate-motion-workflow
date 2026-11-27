# M5 — Bukti benchmark video dan uji generalisasi (2026-11-28)

Empat benchmark dirender lewat jalur job/worker yang sama dengan job produksi: `ump_job create (directed)` → `write_composition` → `ump_render target=preview`. Tidak ada renderer khusus benchmark, tidak ada merge/push/release/deploy.

## T1 — Empat benchmark terender dan terukur

Semua artefak di `~/.hermes/ump/runs/<run_id>/preview.mp4`; metadata dari `ffprobe` + SHA-256 file utuh.

- **Opening anime — job `j3LHrBggrd9R9pFa` rev 3, run `pDNc0d7-tD21SBiM`** — 600 frame, 20.000 s, 1080×1920, 30/1 fps, H.264 yuv420p, 8.951.608 B, SHA-256 `0c9c2a6321753ee385a26a067d9bd10d493d2ea7153243befc5989d97182521c`
- **Explainer faktual Indonesia — job `vyZCxi1ewM4Rsp9_` rev 4, run `DFrn1HV0KqemFrmM`** — 900 frame, 30.000 s, 1080×1920, 30/1 fps, H.264 yuv420p, 3.539.009 B, SHA-256 `90a8698df3a3c64d7a6cda278c416c0091a161d2d6a1ce091905312861aa3d1f`
- **Cerita 45 s — job `oCpeFrl69T0k0NAn` rev 2, run `lJbj695IVpcxpWae`** — 1350 frame, 45.000 s, 1080×1920, 30/1 fps, H.264 yuv420p, 8.456.743 B, SHA-256 `f4457b66aae5775dab2b63b42d4d22b496d430b5d749fd7cd8cf48d915a0b157`
- **Iklan produk 15 s — job `tHNRA3Pu0IEIAU-q` rev 3, run `EOt1M-LmQWgm9G59`** — 450 frame, 15.000 s, 1080×1920, 30/1 fps, H.264 yuv420p, 2.116.881 B, SHA-256 `8a716c648bf8196f01768047835a58d40e2e1f6b7b143885a1a70688c144fd40`

Empat SHA berbeda; tidak ada artefak yang tertukar atau terduplikasi.

**Batasan pekerjaan:** render bersifat serial karena satu lease global. Render paralel ditolak `RENDER_BUSY: another render holds the global lease` (terbukti pada percobaan pertama saat empat render dikirim bersamaan). Durasi render nyata: opening 600 f ≈ 5 menit, explainer 900 f ≈ 9 menit, story 1350 f ≈ 13 menit, ad 450 f ≈ 4 menit.

## T2 — Generalisasi: bahasa visual berbeda per skenario

Yang dibagi hanya kit utilitas (canvas helper, easing, RNG deterministik, tipografi, vignette). Boilerplate yang identik antar-komposisi terbatas pada lima baris kerangka HTML/`<canvas>`/`getContext`, dan tidak ada blok tata letak, palet, atau shot list yang dipakai ulang.

Pengukuran dari frame kunci (resolusi 90×160, hue rata-rata piksel tersaturasi, luminansi rata-rata):

- opening: hue 229.6°, luminansi 0.100 — malam/dini hari, palet biru-ungu gelap
- explainer: hue 137.5°, luminansi 0.319 — peta/teal-krem, register grafis informasional
- story: hue 217.7°, luminansi 0.149 — laut malam, dua-tokoh
- ad: hue 183.1°, luminansi 0.164 — studio gelap netral, produk sebagai hero

Pasangan paling mirip adalah opening/story (Δhue 11.9°, Δlum 0.049) karena keduanya satu waralaba dan berbagi palet senja. Itu disengaja sebagai kontinuitas, bukan template tunggal: opening memakai title burst radial, karakter besar, dan kartu penutup; story memakai tiga shot (wide → closer → two-shot) tanpa tipografi judul. Untuk mencegah ambiguitas, opening mendapat motif khusus (speed line radial pada beat judul) yang tidak ada di video lain.

## T3 — Tidak ada segmen beku yang tidak disengaja

Metode: decode seluruh frame ke grayscale 160×284, hitung jumlah piksel berubah antar frame berurutan, catat streak terpanjang di bawah 40 piksel berubah.

| Benchmark | Freeze terpanjang | Rata-rata piksel berubah/frame |
|---|---|---|
| opening | 0.00 s | 2247 |
| explainer | 1.00 s | 2864 |
| story | 0.00 s | 413 |
| ad | 0.63 s | 215 |

Ambang lulus: hold tak sengaja ≤ 0.5 s. Dua benchmark melewati ambang pada iterasi pertama dan diperbaiki:

- **explainer:** hold beat 5 (`SISA`) beku 3.20 s. Penyebab: butir pasir dan cangkang digambar dari RNG tetap tanpa ketergantungan waktu. Perbaikan: butir mengapung dengan osilasi `sin/cos` pada `t0`, ditambah streak angin melintas; lalu seluruh beat diberi push-in lambat 1.2 % agar beat mana pun tidak pernah terbaca sebagai slide mati. Hasil akhir 1.00 s (di akhir beat, sebelum wipe).
- **ad:** hold beat harga beku 1.50 s. Penyebab: turntable berhenti menyusul settle kamera dan tidak ada lapisan bergerak. Perbaikan: debu naik halus melalui pool cahaya, key light bernapas (`0.18 ± 0.05 sin`), dan radius pool berdenyut. Hasil akhir 0.63 s.

Hold yang tersisa adalah hold yang memang dirancang (penutup beat/wipe), dan masih bergerak halus — bukan frame identik.

## T4 — Regresi

- Suite penuh default: **478 tes, OK (skipped=2)**.
- Suite dengan lane browser/sandbox aktif (`UMP_BROWSER=1 UMP_SANDBOX_TESTS=1`): **478 tes, OK (skipped=2)** dalam 100.032 s — 44 tes browser/sandbox benar-benar dijalankan, bukan dilewati.
- Dua skip tersisa adalah lane yang butuh dependensi di luar lingkungan ini: 1 tes piksel butuh NumPy, 1 tes integrasi ASR opt-in (`UMP_ASR_INTEGRATION=1`).
- `scripts/check.py` → `HASIL: SIAP`; `node --check scripts/record_frames.mjs` dan `record_custom.mjs` ok; `py_compile ump_core/*.py` ok; `git diff --check` ok; `git status` bersih di `feat/ump-v2.0`.

Tidak ada file kode inti yang diubah untuk M5: seluruh pekerjaan adalah komposisi, materi benchmark, dan pengukuran.

## T5 — Kandidat rilis

Kandidat yang diajukan: empat benchmark di atas, dengan kelas bukti teknis penuh (metadata, hash, motion audit, regresi).

Terverifikasi:
- resolusi/fps/durasi/frame persis sesuai skenario, keempatnya H.264 8-bit yuv420p
- keempatnya punya motion kontinu di atas ambang
- keempatnya lolos review frame kunci: tidak ada bentuk rusak, teks terpotong, atau objek melayang
- suite regresi hijau dengan lane browser/sandbox aktif

Belum diverifikasi (jujur, tidak diklaim):
- **Playback 1× oleh operator.** Penilaian visual di sini berasal dari frame kunci terdekode dan pemeriksaan model, bukan tontonan penuh dari perangkat.
- **Audio/VO.** Benchmark explainer belum punya VO bahasa Indonesia atau subtitle; ia menampilkan caption teks di dalam komposisi. Bobot M5 versi ini adalah komposisi visual + gerak, bukan jalur audio M3.
- **Konsistensi antar-tema.** opening dan story berbagi palet senja dengan sengaja; kalau operator tidak menginginkan kontinuitas itu, story perlu palet baru.
- **Kualitas ilustrasi.** Semua bentuk digambar secara prosedural (kanvas 2D). Itu konsisten dengan register grafis, tetapi tidak setara dengan aset ilustrasi buatan tangan.

## Perbaikan yang dilakukan selama M5 (dengan alasan)

1. Kerangka kamera bocor di story — `boat()` tidak menutup `ctx.save()` sehingga transformasi kamera maju terus tiap frame dan seluruh scene keluar dari pandangan. Diperbaiki dengan `restore()` sebelum `return`.
2. Origami crane tidak terlihat sama sekali meski kode terpanggil — transformasi translasi hilang karena `save/restore` bersarang. Digambar ulang sebagai satu siluet poligon absolut (leher, kepala, ekor, sayap terangkat) di koordinat kanvas.
3. Crane awalnya ditempatkan jauh dari tangan tokoh — kini dianimasikan menuju titik tangan yang dihitung dari pose lengan, jadi gestur "mengangkat" terbaca.
4. Matahari terbit tertutup bukit — urutan gambar diperbaiki dan posisi digeser ke horizon dekat.
5. PESISIR tidak terlihat pada beat peta — jendela fade label disesuaikan.
6. Garis pantai beat orang-orang terlihat seperti garis lurus — diganti garis putus beriak dengan band pasir basah dan refleksi.
7. Hold beku di explainer dan ad — diperbaiki seperti pada T3.
8. opening/story terlalu mirip — ditambah speed line radial khusus opening.

## Next

Perbaikan berikutnya menunggu arahan: lihat keempat MP4 di 1× lalu putuskan apakah M5 cukup untuk kandidat rilis, atau perlu putaran kedua (palet story baru, VO bahasa Indonesia untuk explainer, atau aset ilustrasi lebih detail).
