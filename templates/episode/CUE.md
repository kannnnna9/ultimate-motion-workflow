# Template episode — cue

Satu cue = satu perubahan visual + bunyi yang menempel padanya. Prinsipnya keras: **waktu hanya ditulis sekali**, di `t_ms`. Aksi dan SFX sama-sama membaca angka itu.

## Bentuk satu cue

```json
{
  "id": "c002",
  "t_ms": 1200,
  "beat": "b001",
  "action": { "type": "reveal", "target": "hook-label", "params": { "from": "fold" } },
  "sfx": { "name": "paper-fold-a", "offset_frames": 1, "gain_db": -6 },
  "transition": { "type": "fold", "duration_ms": 400 }
}
```

- `id` naik `c001`, `c002`, …; `t_ms` naik monoton. Validator menolak keduanya bila dilanggar.
- `beat` harus ada di `naskah.json` — begitulah cue tahu ia milik ide yang mana.
- `sfx.t_ms` **dilarang**. Skema menolak properti itu. Kalau butuh geser bunyi, ubah `t_ms` (aksi ikut bergerak) atau `offset_frames` (hanya bunyi, ±2 frame).
- `offset_frames: 1` = bunyi mendarat satu frame setelah aksi terlihat. Ini default yang disepakati; jangan diubah tanpa alasan karena sinkronisasi terasa berbeda di 30 fps.

## Jenis aksi

| `type` | Arti | `params` yang dipakai |
|---|---|---|
| `reveal` | elemen muncul | `from`: `fold`, `mask`, `scale` |
| `move` | elemen berpindah | `to`, `ease` |
| `fold` | lipatan/oklusi sebagai handoff | `into`: `frame`, `card`, `edge` |
| `cut` | potongan keras tanpa tween | — |
| `scale` | zoom in/out elemen | `factor`, `ease` |
| `fade` | opasitas | `to`, `ease` |
| `hold` | tahan diam | — |

`fade` satu layar penuh dilarang oleh brand sheet; pakai `fold` atau `cut`.

## Ritme

Brand sheet menetapkan `min_motion_change_s` / `max_motion_change_s` (default 1–2 detik). Artinya:

- jarak antar `t_ms` dalam satu beat sebaiknya 1000–2000 ms
- jangan dua cue bersamaan di `t_ms` sama kecuali memang satu gerakan gabungan
- setiap beat minimal satu cue; beat tanpa cue = slideshow

## Audio bed

```json
"audio_beds": [
  { "name": "bed-contoh", "file": "assets/audio/bed.mp3",
    "from_beat": "b001", "to_beat": "b003", "gain_db": -20,
    "license": "nama lisensi + pemilik + URL" }
]
```

- `from_beat`/`to_beat` harus ada di naskah.
- `license` wajib berisi atribusi nyata. Track tanpa lisensi jangan dipakai.
- Semua bed berhenti paling lambat di awal kartu sumber. Tidak ada ekor atau stinger di atas kartu akhir.

## Kartu sumber

```json
"end_card": { "start_beat": "b003", "hold_s": 8 }
```

`hold_s` harus sama dengan `brand.json` → `rules.end_card_hold_s`. Validator menolak bila berbeda.

## Menggeser waktu satu adegan

Ubah `t_ms` pada cue yang bersangkutan. Aksi bergerak **dan** SFX bergerak, karena tidak ada jam kedua. Tidak ada langkah lanjutan yang perlu diingat.

Kalau menggeser satu cue membuat `t_ms` melewati cue berikutnya, ubah kedua cue sekaligus — validator akan menolak urutan yang tidak monoton.

## Review

- [ ] setiap beat punya minimal satu cue
- [ ] `t_ms` naik monoton, id naik
- [ ] tidak ada `sfx.t_ms`
- [ ] bed punya lisensi, berhenti sebelum kartu sumber
- [ ] `end_card.hold_s` = `brand.rules.end_card_hold_s`
- [ ] `validate.py` lulus
