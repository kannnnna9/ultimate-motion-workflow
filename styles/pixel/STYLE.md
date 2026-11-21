# Pixel

Kanvas internal 180×320, diskalakan integer ×6 dengan nearest-neighbour. Palet terbatas, sprite ditulis sebagai array piksel, dan waktu dibulatkan per frame. Tidak ada antialias, tidak ada easing halus.

**Asal:** [MG Styles 15](https://github.com/Vincentwei1021/mg-styles-15) `demos/20-pixel`, commit `8fab7eb`, MIT. Aturan visual diambil dari `prompts/20-pixel.md`; kodenya ditulis ulang untuk HyperFrames.

## Kapan dipakai

Topik nostalgia, game, atau apa pun yang butuh nada bicara ringan dan sedikit jenaka. Juga sangat efektif sebagai gaya pembanding — setelah episode lain tampil mulus, lidah tajam piksel terasa seperti jeda yang disengaja.

## Yang harus ada

- **Resolusi internal kecil** (180×320 dalam preset ini) dengan skala integer. Penskalaan non-integer menghancurkan seluruh ilusi.
- **`image-rendering: pixelated`** di kanvas dan semua elemen yang menyertainya.
- **Posisi selalu bilangan bulat.** `Math.round()` pada setiap koordinat sprite.
- **Waktu dibulatkan** ke grid 12 fps; animasi memakai `steps(1)`.
- **Dither**, bukan gradien. Langit dibuat dari pola Bayer dua warna.

## Palet

Latar `#0E0A18`, `#1E1638`, `#3A2A6B` untuk kedalaman, aksen `#FF3D7F`, `#00D9C0`, `#FFC857`, dan `#F4F1E8` untuk sorotan. Tujuh warna; lebih dari itu dan paletnya berhenti terasa terbatas.

## Gerak

Parallax tiga lapis bergeser dalam piksel utuh. Sprite bergerak 1 px per langkah. Efek CRT opsional berupa garis scan 2 px; jangan sampai memblur piksel menjadi bubur.

## Jangan

- Jangan skala non-integer atau biarkan browser menginterpolasi.
- Jangan easing halus pada sprite.
- Jangan tambah warna di tengah episode.

## Signature

- `pixelated` — image-rendering pixellated pada kanvas; tidak ada antialias.
- `nearestNeighbor` — Skala integer dengan penskalaan nearest-neighbour.
- `steps(1)` — Animasi memakai langkah diskret, bukan easing halus.
- `dither` — Gradien dibuat dari pola Bayer dua warna, bukan interpolasi.
- `parallax` — Tiga lapis bergeser dengan kecepatan berbeda, dalam piksel utuh.
- `320` — Kanvas internal kecil (180×320), diskalakan ×6.
