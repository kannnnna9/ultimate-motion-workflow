# Template episode — brand sheet

Bagian ini diputuskan **sebelum** aset dibuat, dan disetujui operator sekali per episode. Kalau style frame belum disetujui, jangan lanjut ke naskah final.

## 1. Kanvas (dipatok)

| Field | Nilai | Catatan |
|---|---|---|
| `width` × `height` | 1080 × 1920 | 9:16 vertikal, piksel persegi |
| `fps` | 30 | semua timing frame-aligned ke 30 fps |
| `codec` / `audio_codec` | `h264` / `aac` | diverifikasi `ffprobe` di akhir |
| `color_space` | `bt709` | default; ubah hanya bila ada alasan |

Skema menolak nilai lain. Alasan: semua bukti selesai di [ROADMAP.md](../../ROADMAP.md) mengasumsikan angka ini.

## 2. Palet — peran warna, bukan merek

Isi `ground`, `ink`, `accent` (wajib) dan `secondary` (opsional) dengan hex 6 digit. `coverage` menyatakan target porsi area di dunia ilustrasi, dipakai saat meninjau frame kunci.

```json
"palette": {
  "ground": "#B8D2C4",
  "ink": "#203A36",
  "accent": "#D27D62",
  "secondary": "#6D956B",
  "coverage": { "ground": 55, "ink": 20, "secondary": 18, "accent": 7 }
}
```

Nilai contoh di atas dipakai episode Blok M Part 2 dan **bukan** warna resmi tempat mana pun — jangan disalin ke episode lain tanpa alasan desain.

Aturan praktis yang ditegakkan di review, bukan oleh skema:

- teks memakai `ink` di atas `ground`; rasio kontras minimal `rules.min_contrast_ratio` (default 4.5:1)
- `accent` untuk satu hal per frame — label, angka, atau panah, jangan tiga sekaligus
- cakupan `accent` di atas ±10% mulai terasa seperti poster diskon

## 3. Tipografi

`display` (judul), `body` (teks pendukung), `captions` (caption ASS). Tiap entri: `family`, `weight` (100–900 kelipatan 100), `size_px`, `line_height`.

Font yang dibundel dicatat di `fonts[]` beserta **lisensinya**:

```json
"fonts": [
  { "family": "Archivo Black", "file": "fonts/ArchivoBlack-Regular.ttf", "weight": 900, "license": "OFL-1.1" }
]
```

Font tanpa lisensi yang jelas tidak boleh dibundel. Kalau memakai font sistem (`DejaVu Sans` untuk caption), tidak perlu entri `fonts`.

## 4. Kotak aman teks

`x`, `y`, `width`, `height` dalam piksel kanvas. Contoh: `{ "x": 120, "y": 288, "width": 660, "height": 960 }` — menyisakan ruang untuk UI platform di atas dan bawah.

- Tidak ada teks penting di luar kotak ini.
- Caption tidak memakai `\an` bebas; margin ASS dihitung dari nilai ini.
- Kartu sumber tetap di dalam kotak dan tidak mengecil di bawah `min_credit_px` (default 30 px).

## 5. Aturan terukur

| Field | Default | Dicek dengan |
|---|---|---|
| `max_words_per_line` | 5 | tinjauan frame |
| `min_credit_px` | 30 | ukuran font di kartu sumber |
| `min_motion_change_s` / `max_motion_change_s` | 1 / 2 | hitung cue per beat |
| `end_card_hold_s` | 8 | `cue.json` → `end_card.hold_s` (validator) |
| `min_contrast_ratio` | 4.5 | sampling warna teks vs latar |

## 6. Larangan (`banned`)

Daftar minimal yang diharapkan ada di setiap episode:

- kartu slide statis
- frame judul adegan yang berulang
- label berbentuk pil
- progress bar titik-titik
- ikon mengambang generik
- entrance fade atau slide-up satu layar penuh
- partikel acak
- filter gambar CSS
- teks mikro di bawah 30 px
- suara apa pun di atas kartu sumber akhir

Tambahkan yang khusus episode ini. Daftar ini dibaca manusia saat review; tidak ada pengecek otomatis di v1.

## 7. Checklist persetujuan

- [ ] kanvas sesuai tabel
- [ ] palet + coverage terisi, warna bukan warna merek pihak lain
- [ ] tiga gaya tipografi + font berlisensi (kalau dibundel)
- [ ] kotak aman di dalam 1080×1920 dengan ruang UI platform
- [ ] `rules` diisi, `end_card_hold_s` disepakati
- [ ] `banned` berisi minimal daftar di atas
- [ ] `validate.py` lulus untuk folder episode ini
