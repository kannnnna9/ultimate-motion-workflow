# Template episode — naskah

Naskah adalah satu-satunya tempat kata-kata diputuskan. Caption per kata berasal dari audio, bukan dari file ini, supaya ejaan dan timing tidak bisa melenceng.

## Aturan beat

- Satu beat = satu ide. Kalau satu beat butuh dua kali "dan", pecah.
- Minimal dua beat (demo v1 wajib dua adegan).
- `id` berurutan `b001`, `b002`, … tanpa lompat. Validator menolak urutan bolong.
- `purpose` mengisi salah satu: `hook`, `konteks`, `bukti`, `payoff`, `penutup`. Beat pertama wajib hook yang berdiri sendiri tanpa konteks.

## Panjang dan durasi

`estimated_s` adalah perkiraan kasar untuk merencanakan; angka sebenarnya datang dari durasi VO terukur. Patokan bahasa Indonesia untuk `id-ID-*Neural` pada `rate=+0%`:

- ±2,5–3,0 kata per detik
- 1 kalimat sederhana ≈ 3–4 detik
- hook harus selesai di ≤ 3,5 detik

Kalau VO terukur lebih panjang dari `target_duration_s.max`, potong kata — **jangan** mempercepat gerak atau audio untuk memaksa masuk durasi.

## Narasi vs teks layar

`narration` diucapkan; `on_screen_text` dibaca mata. Keduanya boleh berbeda:

- narasi menjelaskan, teks layar menegaskan (maksimal 5 kata)
- jangan menampilkan seluruh narasi sebagai teks — itu slideshow
- angka dan nama tempat ditulis sekali di layar, tidak diulang tiap beat

## Sumber dan klaim

- Setiap klaim faktual di `narration` harus punya `fact_refs` ke `sources[]`.
- `sources[]` wajib berisi `id`, `title`, `url`; lengkapi `publisher` dan `accessed`.
- Sumber yang tidak bisa dibuka jangan dipakai.
- Klaim yang bergantung waktu (jam buka, harga, jadwal) tidak boleh masuk tanpa tanggal akses dan pernyataan berlaku kapan.
- Validator menolak `fact_refs` yang menunjuk ID tidak ada.

## Voice

```json
"voice": { "engine": "edge", "name": "id-ID-GadisNeural", "rate": "+0%" }
```

- `edge` — Edge TTS, butuh jaringan, gratis, memberi WordBoundary untuk caption sinkron.
- `external` — file VO buatan luar; transkrip lokal memakai faster-whisper `small`.

Satu episode satu voice. Jangan ganti voice di tengah episode tanpa alasan yang dicatat di `notes/`.

## Alur penulisan

1. Isi `target_duration_s`.
2. Tulis beat hook dulu; baru konteks, bukti, payoff.
3. Tempel `fact_refs` saat menulis kalimat, bukan setelahnya.
4. Jalankan `validate.py`.
5. Setelah VO jadi: cocokkan durasi nyata, sesuaikan `estimated_s`, dan pastikan `cue.json` mengikuti timing baru — bukan sebaliknya.

## Contoh satu beat

```json
{
  "id": "b002",
  "narration": "Tapi gagalnya bukan karena idenya, karena tidak pernah dijelaskan.",
  "purpose": "konteks",
  "on_screen_text": "BUKAN IDE SESULIT ITU",
  "fact_refs": ["s1"],
  "estimated_s": 5
}
```
