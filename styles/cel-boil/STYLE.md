# Cel Boil

Garis yang bergetar. Setiap dua frame, seluruh ilustrasi digeser sedikit oleh noise, sehingga kelihatan seperti digambar ulang tangan — hidup, tidak stabil, disengaja. Ini yang membuat animasi vektor terasa buatan manusia alih-alih render.

**Asal:** [MG Styles 15](https://github.com/Vincentwei1021/mg-styles-15) `demos/05-cel-boil`, commit `8fab7eb`, MIT. Aturan visual diambil dari `prompts/05-cel-boil.md`; kodenya ditulis ulang untuk HyperFrames.

## Kapan dipakai

Cerita, esai pribadi, atau penjelasan yang ingin terasa hangat. Juga penawar yang baik di antara dua episode yang sangat digital.

## Yang harus ada

- **Boil.** `feTurbulence` + `feDisplacementMap` dengan `seed` berganti tiap dua frame. Jangan pakai seed tetap — garis akan diam dan gayanya hilang.
- **Animasi on twos.** Waktu dihitung `Math.floor(t / (2/24)) * (2/24)`, jadi gerakan efektifnya 12 fps.
- **Warna isi off-register.** Lingkaran warna digeser 5–7 px dari garisnya, seperti mewarnai keluar garis.
- **Smear frame** pada gerakan cepat: satu frame direntangkan 180%, hanya ada 1/24 s.
- **Grain kertas** 13% multiply di atas semuanya.

## Palet

`#F2E7D3` kertas, `#1A1512` tinta, `#E8462F` tomat, `#F4B942` kuning matahari. Empa warna saja; lebih dari itu terasa seperti komik, bukan cel.

## Gerak

Masuk dan keluar memakai `steps(2)` atau `steps(3)`, tidak pernah mulus. Spark muncul satu per satu di grid 12 fps.

## Jangan

- Jangan biarkan boiler diam walaupun frame sedang hold.
- Jangan gambar ulang semua garis tiap frame — cukup geser via displacement.
- Jangan tambah bayangan; kedalaman datang dari garis, bukan bayangan.

## Signature

- `boil` — Garis bergetar karena displacement noise yang seed-nya berganti.
- `turbulence` — feTurbulence + feDisplacementMap sebagai sumber getaran.
- `onTwos` — Gambar berganti tiap dua frame; gerakan efektif 12 fps.
- `smear` — Satu frame direntang 180% pada gerakan cepat, hanya 1/24 s.
- `paperGrain` — Grain kertas 13% multiply di atas seluruh frame.
- `Math.floor` — Waktu dibulatkan ke bawah ke grid frame.
