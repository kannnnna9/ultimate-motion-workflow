# UMP v2.0.0 — catatan rilis

**Tanggal:** 2026-11-28 · **Dari:** v1.6.2 (`c98abae`) · **Ke:** v2.0.0

## Yang berubah

**Jalur utama sekarang komposisi kustom per revisi.** `write_composition` menyimpan HTML komposisi yang di-pin ke satu revisi; renderer offline membuka scene itu, melakukan seek deterministik per frame, dan meng-encode 1080×1920 sesuai fps yang diminta. Preset v1 (`styles/<id>/ump.html`) tetap tersedia lewat jalur lama — bukan dihapus, bukan ditingkatkan diam-diam.

Empat milestone yang membentuk rilis ini:

- **M1 — inti kreatif.** Komposisi HTML per revisi, renderer seekable, preview MP4, source bundle ber-manifest, halaman player. Semua artifact terikat ke run, bukan ke folder bersama.
- **M2 — aset dan gerak.** Katalog aset reusable (`ump_assets`) dengan provenance, license, seed, parameters, SHA-256, version pinning, dan creative lock. Bytes yang disalin ke source bundle diverifikasi hashe-nya.
- **M3 — audio dan subtitle.** Track audio opsional, timing kata yang terikat ke SHA audio, WhisperX dengan fallback `faster-whisper-small` terpin, anchor ucapan-ke-gambar, subtitle ASS editable atau web-karaoke per kata dengan satu pemilik. Jalur tanpa kebutuhan timing tidak memanggil ASR sama sekali.
- **M4 — QA dan revisi.** Review gerak dari MP4 **terdekode**, bukan dari still: kalibrasi terkunci, kontrol hold, deteksi bug gerak, dan repair loop maksimal tiga percobaan yang terikat job/revisi/run. Revision lock, invalidasi dependen, rollback, cache tervalidasi, recovery capture terputus.
- **M5 — benchmark.** Empat video (opening anime 20 s, explainer Indonesia 30 s dengan VO dan subtitle, cerita 45 s, iklan produk 15 s) dirender lewat jalur job biasa, ditonton operator dua putaran, tiga cacat visual dan VO yang hilang diperbaiki lalu diverifikasi ulang.

## Batasan yang masih terbuka

Jangan baca rilis ini sebagai "semuanya beres":

1. **G10 rantai penuh belum dieksekusi end to end.** `research` butuh `brief_approved` dan `select_style` butuh `research_approved`; approval hanya sah dari pesan masuk operator. Directed lock dan autonomous brief sudah terbukti pada subjek baru (Telaga Warna); rantai brief → research → style → naskah → render belum.
2. **VO explainer benchmark tidak terdaftar di JobStore.** `import_audio` mensyaratkan lampiran audio Telegram sebagai provenance; VO itu dibuat lokal dengan Piper, jadi track audio di-mux di level file. Stream video identik byte-per-byte dengan render senyap, tapi audio-nya tidak ada di store.
3. **Renderer butuh Chrome/Chromium + ffmpeg di host.** Bukan dependensi Python murni.
4. **Preset `spec`** (satu entri registry) belum renderable dan tetap begitu.

## Upgrade

```bash
hermes plugins install kannnnna9/ultimate-motion-workflow --enable --force
```

Data lama tidak dimigrasi: `~/.hermes/ump` tetap dibaca apa adanya. Job v1 masih bisa dibuka; komposisinya lewat jalur preset. `revision_locks`, `quality_gates`, dan `repair_chains` adalah tabel baru yang dibuat otomatis saat store pertama kali dibuka v2 — tidak ada backfill, jadi job lama tidak punya lock atau review history.

**Sebelum upgrade, backup dulu** — lihat `docs/MAINTENANCE.md`. Minimum: bundle repo dan arsip `~/.hermes/ump`.

## Rollback

```bash
cd ~/.hermes/plugins/ump && git checkout v1.6.2   # atau install ulang tag lama
hermes plugins install kannnnna9/ultimate-motion-workflow@v1.6.2 --enable --force
```

Kalau harus balik ke v1.6.2: job yang dibuat v2 akan tetap ada di store, tapi komposisi kustomnya tidak bisa dirender preset v1 — sumber HTML-nya tidak dikenali jalur lama. Render ulang job seperti itu butuh v2. Tabel baru diabaikan oleh v1.6.2 (tidak drop, tidak error).

Data di `~/.hermes/ump` **tidak** disentuh oleh upgrade maupun rollback. Yang berubah hanya kode di `~/.hermes/plugins/ump`.

## Verifikasi rilis

- Suite penuh: 478 tes, OK (2 skip) dengan `UMP_BROWSER=1 UMP_SANDBOX_TESTS=1`.
- `scripts/check.py` READY; `node --check` pada recorder; `py_compile` bersih; `git diff --check` bersih.
- Bukti per milestone: `docs/verification/ump-v2-progress.md`.
- Benchmark dan motion audit: `docs/verification/ump-v2-m5-benchmarks.md`.
- Generalisasi G10: `docs/verification/ump-v2-g10-generalization.md`.
