# Aset lokal

## gsap.min.js

- **Versi:** 3.15.0
- **Sumber:** paket npm `gsap` (dist/gsap.min.js)
- **Lisensi:** GreenSock Standard License — https://gsap.com/standard-license
- **Alasan dibundel:** komposisi harus bisa dirender tanpa jaringan. Memuat GSAP dari CDN membuat render gagal di mesin offline dan menambah titik gagal yang tidak perlu.

Jangan memutakhirkan GSAP tanpa merender ulang demo dan memeriksa frame kunci: perubahan timing di GSAP menggeser animasi walau kode komposisi tidak berubah.
