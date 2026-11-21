---
name: motion-pipeline
description: Use when producing an Indonesian vertical explainer with this repository's templates, style presets, HyperFrames render path, captions, and timestamped revisions.
---

# Motion Pipeline

Workflow satu episode dari brief sampai MP4 final. Jalankan dari root repo; semua path di bawah relatif terhadap root.

## 1. Pilih style dulu

Baca `styles/registry.json`, lalu buka `styles/<style-id>/STYLE.md`. Pilih satu preset sebelum menulis naskah.

Preset yang tersedia:

- `dark-editorial` — ready; demo `demo/explain-9x16/`
- `botanical-paper` — spec
- `kinetic-bold` — spec
- `minimal-explainer` — spec

Style **spec** adalah kontrak desain yang sudah ditulis tetapi belum punya komposisi demo siap-render. Jangan mengklaim style itu ready sebelum ada komposisi, snapshot, dan render yang lulus.

Tuliskan pilihan di `brand.json`:

```json
"style_id": "dark-editorial"
```

Validator menolak ID yang tidak ada di registry.

## 2. Buat brand sheet

Salin `templates/episode/episodes/_contoh/` ke `demo/<slug>/` atau lokasi episode lain. Ganti `episode` di `brand.json`, `naskah.json`, dan `cue.json` agar sama dengan nama folder. Isi brand sheet mengikuti STYLE.md:

- kanvas 1080×1920, 30 fps, H.264/AAC
- palette dan coverage
- display/body/caption typography
- safe area
- aturan motion dan end card
- daftar banned

Minta persetujuan style frame sebelum membuat banyak aset.

## 3. Tulis naskah

`naskah.json` menyimpan beat dan sumber faktual. Satu beat satu ide. Beat pertama harus hook yang berdiri sendiri. Setiap klaim faktual punya `fact_refs`. Caption per kata datang dari audio, bukan dari naskah.

Validasi:

```bash
python3 templates/episode/validate.py <folder-episode>
```

## 4. Buat VO dan caption

Edge TTS:

```bash
.venv/bin/python scripts/caption.py <folder-episode> --force
.venv/bin/python scripts/subs.py <folder-episode>
```

Untuk VO eksternal, letakkan file di `<folder-episode>/assets/audio/vo.mp3`, ubah engine menjadi `external`, lalu pasang faster-whisper sesuai `scripts/requirements.txt`.

Periksa durasi VO terhadap target. Jangan mempercepat animasi untuk memaksa narasi masuk durasi.

## 5. Buat cue

Satu `cue` punya satu `t_ms`. Aksi dan SFX membaca waktu yang sama; SFX hanya boleh punya `offset_frames` kecil. Jangan menambah `sfx.t_ms`.

Geser cue berdasarkan batas kata yang muncul di `work/captions.json`. Setelah mengubah cue, jalankan validator dan render preview.

## 6. Check, snapshot, render

Pastikan prasyarat:

```bash
.venv/bin/python scripts/check.py
```

Snapshot murah sebelum master:

```bash
.venv/bin/python scripts/render.py <folder-episode> --project <folder-hyperframes> --snapshot 1,4,8
```

Render master:

```bash
.venv/bin/python scripts/render.py <folder-episode> --project <folder-hyperframes>
```

`render.py` menjalankan validator, HyperFrames check/render, membakar ASS caption, memadukan audio, dan memeriksa ffprobe. Cache HyperFrames harus privat (`~/.hermes/hyperframes-cache`, mode 700). Jangan menghapus `out/` saat render masih berjalan.

## 7. Review sebelum kirim

Wajib periksa:

- frame 0: hook sudah aktif
- satu frame tiap beat: objek dominan dan caption berada di safe area
- frame kartu sumber: semua kredit terbaca
- ffprobe: 1080×1920, 30 fps, H.264/AAC
- audio: tidak sunyi dan berhenti di kartu sumber
- path episode relatif; tidak ada cache `.hyperframes/`, `work/`, atau output di Git

Catat revisi dengan timestamp. Jangan publish atau push sebelum operator menyetujui hasil.
