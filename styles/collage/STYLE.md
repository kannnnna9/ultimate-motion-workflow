# Collage

Potongan kertas koran, guntingan bersisi putih, halftone, dan gerak yang sengaja tidak halus. Semuanya bergerak di grid 12 fps dengan jitter ±2 px seolah ditempel tangan.

**Asal:** [MG Styles 15](https://github.com/Vincentwei1021/mg-styles-15) `demos/06-collage`, commit `8fab7eb`, MIT. Aturan visual diambil dari `prompts/06-collage.md`; kodenya ditulis ulang untuk HyperFrames.

## Kapan dipakai

Topik yang butuh nada bicara editorial, satir, atau sengaja berantakan tetapi terkontrol. Kuat untuk hook pembuka karena pecahan visualnya menahan mata lebih lama daripada teks biasa.

## Yang harus ada

- **Gerak 12 fps.** Waktu dibulatkan ke grid (`Math.round(t / STEP) * STEP`); tidak ada tween mulus. Ini inti gaya ini.
- **Jitter tangan ±2 px** tiap langkah, supaya terasa ditempel, bukan diprogram.
- **Tepi potong putih** 6 px di setiap guntingan — tanda gunting.
- **Halftone** berupa pola titik, dipakai sebagai overlay multiply, bukan gradien.
- **Kertas koran** sebagai dasar: dua lembar berotasi sedikit.

## Palet

`#E4DAC6` kraft, `#F4EEDF` kertas, `#141210` tinta, `#C1272D` merah konstruktivis, `#1B3A63` biru koran. Merah hanya untuk aksen — kalau dipakai di area besar, gaya ini berubah jadi propaganda poster.

## Gerak

Setiap guntingan masuk dengan rotasi acak pada langkah 2-frame, lalu diam dengan jitter. Transisi keluar memakai lembar kertas yang terlempar, bukan fade.

## Jangan

- Jangan foto asli. Semua elemen dibuat dari bidang dan tipe, jadi tidak ada aset biner yang harus diunduh.
- Jangan easing halus. Satu tween mulus saja langsung merusak ilusi kertas.
- Jangan lebih dari dua warna aksen dalam satu beat.

## Signature

- `halftone` — Pola titik sebagai overlay multiply, menggantikan gradien.
- `whiteEdge` — Tepi potong putih 6 px di setiap guntingan.
- `stepped12` — Waktu dibulatkan ke grid 12 fps; tidak ada tween mulus.
- `handJitter` — Offset ±2 px di setiap langkah, meniru tangan yang menempel.
- `jitter` — Posisi bergetar kecil di setiap langkah, bukan diam sempurna.
- `newsprint` — Dasar kertas koran berlapis dengan rotasi kecil.
