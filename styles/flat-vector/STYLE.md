# Flat Vector

Bentuk datar, warna pekat, tanpa satu gradien pun. Seluruh episode bergerak seperti satu brand film yang rapi: satu hero bergerak pada satu waktu, dan transisinya adalah objek yang terbang keluar membawa potongan ke beat berikutnya.

**Asal:** [MG Styles 15](https://github.com/Vincentwei1021/mg-styles-15) `demos/01-flat-vector`, commit `8fab7eb`, MIT. Aturan visual diambil dari `prompts/01-flat-vector.md`; kodenya ditulis ulang untuk HyperFrames.

## Kapan dipakai

Penjelasan produk, tutorial, atau brand explainer yang butuh terlihat ramah dan cepat dipahami. Paling aman untuk topik yang punya urutan sebab-akibat yang jelas, karena setiap beat bisa diwakili satu bentuk besar.

## Yang harus ada

- **Tanpa gradien.** Warna hanya solid. Ini yang membuat gaya ini terbaca sebagai flat, bukan sekadar "minimal".
- **Squash & stretch ±10%.** Masuk lewat fase antisipasi → stretch → squash → overshoot → settle.
- **Stagger 2–4 frame.** Elemen tidak masuk bersamaan; gedung di skyline muncul satu per satu dengan durasi berbeda.
- **Satu hero bergerak.** Kalau dua hal bergerak serentak, perhatian pecah dan gayanya hilang.
- **Shape wipe** sebagai transisi. Bidang warna tertutup penuh lalu terbuka lagi, bukan crossfade.

## Palet

`#FFF6E9` krem sebagai dasar, `#151433` tinta, aksen `#2B2BFF` ultramarine, `#FF5A4E` koral, `#FFC62B` sunflower, `#2EE6A8` mint. Enam warna itu sudah cukup untuk seluruh episode; jangan menambah.

## Gerak

Kamera hanya dolly 1.00 → 1.04 sepanjang durasi. Semua energi datang dari objek, bukan dari kamera. Easing masuk memakai `back.out(2.1)`; keluar memakai `power2.in` supaya bersih.

## Jangan

- Jangan pakai blur atau bayangan jatuh — itu bahasa visual gaya lain.
- Jangan biarkan dua beat tampil bersamaan.
- Jangan ganti palet per episode kecuali brand memang punya warna sendiri.

## Signature

- `overshoot` — Tiga fase masuk: antisipasi → stretch → squash → overshoot → settle.
- `squash` — Squash & stretch ±10% pada setiap pendaratan atau perubahan arah.
- `stagger` — Elemen masuk berselang 2–4 frame, bukan serentak.
- `shapeWipe` — Transisi berupa bidang warna yang menutup lalu membuka penuh.
- `flatNoGradient` — Tidak ada gradien sama sekali; hanya warna solid.
