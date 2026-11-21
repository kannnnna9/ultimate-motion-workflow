# Template episode

Satu episode = satu folder di `episodes/<slug>/` berisi tiga file data. Komposisi HyperFrames **membaca** file ini; tidak ada nilai visual atau timing yang di-hardcode di HTML.

## Mulai episode baru

Dari akar repo:

```bash
cp -r templates/episode/episodes/_contoh templates/episode/episodes/ep01-judul-singkat
python3 templates/episode/validate.py templates/episode/episodes/ep01-judul-singkat
```

Ganti `"episode": "_contoh"` menjadi slug folder di ketiga file, isi datanya, jalankan validator sampai `PASS`. Validator menolak slug yang tidak sama dengan nama folder.

## Tiga file, tiga tanggung jawab

- **`brand.json`** — kebenaran visual: kanvas, palet, tipografi, kotak aman teks, aturan terukur, daftar larangan. Disetujui operator sebelum aset dibuat.
- **`naskah.json`** — narasi per beat plus kartu sumber. Satu beat satu ide. Caption per kata **tidak** ditulis di sini; caption diturunkan dari VO (WordBoundary Edge atau transkrip faster-whisper) supaya ejaan dan timing selalu cocok dengan audio.
- **`cue.json`** — kapan aksi terjadi, dan bunyi apa yang menempel padanya. Satu cue memegang satu `t_ms`. SFX hanya boleh menyimpang lewat `offset_frames` (default `1`, artinya bunyi mendarat satu frame setelah aksi terlihat).

Aturan cue bersama itu yang memenuhi kriteria selesai #5: mengubah satu `t_ms` menggeser aksi dan SFX bersamaan, karena keduanya membaca angka yang sama.

## Field yang paling sering dipakai

`brand.json`

- `canvas` — dipatok 1080×1920, 30 fps, `h264`/`aac`. Skema menolak nilai lain.
- `palette.coverage` — target cakupan area (persen) untuk cek frame kunci.
- `safe_area` — kotak aman teks di kanvas. Caption ASS memakai nilai ini untuk margin.
- `rules.end_card_hold_s` — harus sama dengan `cue.json` → `end_card.hold_s`; validator menolak bila berbeda.

`naskah.json`

- `beats[].id` — `b001`, `b002`, … berurutan tanpa lompat.
- `beats[].fact_refs` — ID sumber yang menopang klaim beat itu. Klaim tanpa sumber tidak boleh masuk narasi.
- `sources[]` — kartu sumber di akhir. Wajib ada kalau `fact_refs` dipakai.

`cue.json`

- `cues[].t_ms` — satu-satunya sumber waktu. Harus naik monoton.
- `cues[].action.type` — `reveal`, `move`, `fold`, `cut`, `scale`, `fade`, `hold`.
- `cues[].sfx` — `null` atau `{name, offset_frames, gain_db}`. `t_ms` di dalam `sfx` **ditolak** skema.
- `audio_beds[]` — rentang beat untuk musik/ambience, dengan lisensi. Semua audio harus berhenti sebelum kartu sumber.

## Struktur folder episode saat dipakai

Folder episode menampung data dan aset episode; hasil render masuk `out/` di akar repo (di-ignore Git).

```
episodes/<slug>/
  brand.json
  naskah.json
  cue.json
  assets/            audio VO, musik berlisensi, SFX
  fonts/             font yang dibundel (catat lisensinya di brand.json → fonts[])
  notes/             referensi, koreksi bertimestamp, catatan review
  README.md          opsional: keputusan editorial episode ini
```

Path aset di dalam file data selalu relatif terhadap folder episode. Jangan pakai path absolut: direktori clone harus boleh dipindah (kriteria selesai #4).

## Validasi

```bash
python3 templates/episode/validate.py templates/episode/episodes/<slug>
```

Yang dicek: skema ketiga file, kesamaan slug dengan nama folder, urutan dan kelengkapan beat, `fact_refs` menunjuk sumber nyata, urutan cue, `beat` pada cue dan `audio_beds` ada di naskah, SFX tanpa `t_ms` sendiri, dan `end_card.hold_s` konsisten dengan brand. Exit `0` = lulus.

Butuh `jsonschema` (`pip install 'jsonschema>=4.20'`); M3 memasangnya lewat `scripts/setup`.

## Yang bukan template

Template ini tidak berisi: naskah jadi dari episode lain, aset berhak cipta, musik tanpa lisensi, atau nilai merek milik orang lain. `_contoh/` adalah data fiktif untuk menguji validator — ganti seluruh isinya sebelum render.
