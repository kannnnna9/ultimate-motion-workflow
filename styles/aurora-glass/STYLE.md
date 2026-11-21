# Aurora Glass

Cahaya yang mengalir pelan di bawah kaca buram. Lima blob warna yang sangat diblur, kartu tembus pandang yang mengambang di atasnya, dan satu lensa yang bergerak memperbesar sesuatu. Semuanya lambat.

**Asal:** [MG Styles 15](https://github.com/Vincentwei1021/mg-styles-15) `demos/12-aurora-glass`, commit `8fab7eb`, MIT. Aturan visual diambil dari `prompts/12-aurora-glass.md`; kodenya ditulis ulang untuk HyperFrames.

## Kapan dipakai

Topik yang butuh terasa mahal dan tenang: produk AI, kebijakan, riset. Gaya yang paling menoleransi isi pendek — lima detik penuh aurora saja sudah terasa selesai.

## Yang harus ada

- **Aurora ber-blur besar.** `filter: blur(130px)` pada lima blob 540–820 px, di atas latar gelap. Ini tidak bisa diganti dengan gradien biasa; yang memberi kesan mesh adalah posisi blob yang saling menumpuk.
- **Kartu kaca.** `backdrop-filter: blur(34px)`, isi putih 7%, garis dalam putih 1 px 20%.
- **Warna bertetangga saja.** Violet 261° → biru 232° → cyan 189° → teal 173°. Warna yang bertabrakan membuat campurannya keruh.
- **Grain 4–5%** di atas semuanya untuk mencegah banding.
- **Lensa** dengan dispersi kromatis di tepinya sebagai satu-satunya momen "wow".

## Palet

Latar `#070A18`, `#8B7BFF`, `#3FC8E4`, `#C05BFF`, `#5A6BFF`, `#4FD1C5`. Teks `#EEF2FF` dengan kelembutan 70% untuk paragraf.

## Gerak

Ease `sine.inOut` di semua tempat. Teks masuk hanya dengan opacity + 8 px. Tidak ada satu pun gerakan yang harus ditunggu selesai.

## Jangan

- Jangan mempercepat apa pun. Kecepatan langsung membunuh gayanya.
- Jangan pakai warna komplementer di dua blob yang bertumpuk.
- Jangan lebih dari satu kartu yang menumpuk teks di area yang sama.

## Signature

- `backdrop-filter` — Kartu kaca memakai backdrop-filter blur.
- `blur(` — Blur besar pada blob aurora, bukan gradien.
- `noise` — Grain halus di atas semuanya untuk mencegah banding.
- `adjacentHue` — Hanya warna bertetangga pada roda warna, tidak ada komplemen.
- `breathing` — Easing mendekati sine; tidak ada gerakan yang menyentak.
