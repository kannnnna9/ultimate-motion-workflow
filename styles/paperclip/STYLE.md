# Paperclip

Bahasa visual kanal sains hardcore: kanvas raksasa, kartu beroutline putih tebal seperti stiker, grafik yang presisi, dan kamera yang bergeser pelan menyusuri informasi. Kepadatan informasinya tinggi, tapi setiap beat hanya menambah satu elemen baru.

**Asal:** [MG Styles 15](https://github.com/Vincentwei1021/mg-styles-15) `demos/19-paperclip`, commit `8fab7eb`, MIT. Aturan visual diambil dari `prompts/19-paperclip.md`; kodenya ditulis ulang untuk HyperFrames.

## Kapan dipakai

Episode yang isinya data, perbandingan, atau fakta berlapis. Gaya ini menoleransi banyak teks justru karena susunannya disiplin — tetapi hanya kalau grafiknya benar.

## Yang harus ada

- **Kanvas raksasa.** Permukaan 6000×3000 px; kamera yang bergerak, bukan elemen yang masuk satu per satu.
- **Kartu stiker.** Outline putih 10 px, sudut membulat, bayangan lembut. Ini yang menyatukan elemen dari sumber berbeda.
- **Grafik berskala.** Batang tumbuh dari garis dasar dengan tinggi sesuai nilai. Tidak ada batang dekoratif yang angkanya karangan.
- **Angka menghitung naik** ke nilai akhir sebelum mengendap.
- **Leader line** yang menggambar lebih dulu, label menyusul.

## Palet

`#E9EEF2` biru-abu tenang, `#FFFFFF` kartu, `#26313B` tinta, `#6B7C8A` sekunder, `#FF5A36` aksen tunggal. Hanya satu aksen — begitu ada dua, kesan seriusnya hilang.

## Gerak

Ease panjang `power3.inOut`; kamera berhenti di tiap beat lalu bergerak lagi. Tidak ada gerak cepat, tidak ada overshoot. Kameranya yang lelah, bukan penontonnya.

## Jangan

- Jangan karang angka. Kalau nilai tidak diketahui, jangan tampilkan grafik.
- Jangan pakai lebih dari satu aksen.
- Jangan tambahkan dua elemen baru di satu beat.

## Signature

- `giantCanvas` — Permukaan 6000×3000 px; kamera yang bergerak, bukan elemen yang masuk.
- `sticker` — Kartu beroutline putih 10 px dengan bayangan lembut.
- `camera` — Kamera bergeser dan berhenti di tiap beat, ease panjang.
- `chartGrow` — Batang tumbuh dari garis dasar sesuai nilai sebenarnya.
- `countUp` — Angka menghitung naik ke nilai akhir sebelum mengendap.
- `leaderLine` — Garis penghubung menggambar lebih dulu, label menyusul.
