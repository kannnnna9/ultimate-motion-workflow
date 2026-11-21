# Synthwave

Matahari berbanding di atas grid neon yang mengejar cakrawala, judul logam yang memantulkan cahaya ungu, dan garis scanline yang menandai bahwa ini semua rekaman pita lama. Nada bicaranya nostalgia, dan itu disengaja.

**Asal:** [MG Styles 15](https://github.com/Vincentwei1021/mg-styles-15) `demos/10-synthwave`, commit `8fab7eb`, MIT. Aturan visual diambil dari `prompts/10-synthwave.md`; kodenya ditulis ulang untuk HyperFrames.

## Kapan dipakai

Topik yang butuh momentum dan kesan megah: peluncuran, montase, pembuka seri. Kuat sebagai episode pertama karena dalam satu detik penonton langsung tahu ini bukan video penjelasan biasa.

## Yang harus ada

- **Grid perspektif.** Bidang diberi `rotateX(74deg)` di dalam kotak `perspective: 520px`. Tanpa perspektif, gridnya jadi wallpaper — bukan jalan yang membentang.
- **Matahari berbanding.** Gradien mulus, lalu dipotong `repeating-linear-gradient` 34 px on / 10 px off lewat `mask-image`. Bandnya bukan hiasan; itu yang membuatnya terbaca synthwave.
- **Judul krom.** Isi gradien putih → biru → ungu → putih lewat `background-clip: text`, lalu dua `drop-shadow`: 4 px tajam dan 30 px lebar.
- **Glow horizon** yang menempel di garis cakrawala, bukan di tengah layar.
- **Scanline statis.**

## Palet

`#0A0320` ungu malam, `#FF2D95` magenta, `#22D3EE` cyan, matahari `#FFE066` → `#FF9F1C` → `#FF2D95`, teks `#F3E9FF`.

## Gerak

Grid bergerak lewat `y` dalam siklus 1,6 s yang mulus (satu petak penuh), dan `rotateX` dipegang elemen induk terpisah supaya keduanya tidak saling menimpa. Judul masuk dengan `power4.out` dan blur 18 px → 0 px. Semua easing cepat di awal lalu mengendap.

## Yang sengaja tidak diambil dari upstream

Upstream memakai **WebGL** untuk grid dan matahari, lalu sebagian **Canvas** untuk teks. Ketiganya diganti primitif CSS karena HyperFrames hanya bisa memotret satu halaman DOM.

Efek VHS upstream punya tiga tahap berlapis: RGB split, gelombang bergoyang, dan glitch acak. Di sini hanya scanline yang diambil. Alasannya keras: goyangan dan glitch di upstream dibangkitkan dari jam waktu nyata, sedangkan UMP mensyaratkan frame pada detik yang sama selalu identik. Keacakan yang tersisa — posisi bintang — dihitung dari indeks, bukan dari `Math.random`.

## Jangan

- Jangan hilangkan band matahari; itu penanda gaya paling kuat.
- Jangan animasikan `background-position` grid.
- Jangan pakai `Math.random()`; bintang dan bintik harus deterministik.
- Jangan tambah warna di luar palet — magenta dan cyan yang bertabrakan dengan ungu akan jadi lumpur.

## Signature

- `perspective` — bidang lantai diberi perspektif 520 px agar garis bertemu di cakrawala.
- `rotateX` — grid dimiringkan 74° supaya terbaca sebagai jalan, bukan dinding.
- `repeating-linear-gradient` — dipakai dua kali: garis grid dan potongan band matahari.
- `mask-image` — memotong matahari menjadi band horizontal.
- `background-clip:text` — gradien krom hanya mengisi huruf.
- `drop-shadow` — dua lapis glow, 4 px tajam dan 30 px lebar.
- `30px` — lebar bloom luar, sesuai resep upstream.
- `translate-only` — grid bergerak lewat `y`, tidak pernah `background-position`.
