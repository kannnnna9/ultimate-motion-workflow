# Style: Kinetic Bold

**ID:** `kinetic-bold`  
**Status:** ready  
**Komposisi:** `styles/kinetic-bold/index.html`  
**Preview:** `styles/kinetic-bold/preview/`

Gaya energi tinggi untuk hook, daftar singkat, dan opini yang butuh ritme. Kekuatan datang dari ukuran, timing, dan benturan bentuk — bukan dari banyak elemen kecil.

## Kontrak visual

- Ground dan accent kontras tinggi; satu display face tebal 96–140 px.
- Satu sampai tiga kata per blok; maksimal dua blok teks bersamaan.
- Bentuk geometris besar menjadi partner kata, bukan dekorasi latar.

## Kontrak gerak

- Cut, scale, position, dan stagger pendek; satu perubahan tiap 0,5–1,5 detik.
- Setiap beat punya aksen suara yang menandai perubahan utama.
- Tidak boleh berubah menjadi slideshow dengan satu kartu per kalimat.

## Kontrak copy

- Hook selesai di dua detik pertama.
- Kalimat pendek, kata kerja aktif, payoff sebelum beat terakhir.
- Caption tetap terbaca; energi visual tidak boleh mengorbankan safe area.

## Terbukti render

- `hyperframes check`: 0 error (2026-11-21)
- Render: 1080×1920, 30 fps, H.264, 21.67 s, 650 frame
- Frame diperiksa: hook (2 s), payoff (11 s), kartu sumber (18 s)
- Safe area: semua teks di dalam 190–890 px setelah tipografi diturunkan dari 190→136 px

## Catatan implementasi

- `dark-editorial` bergerak dengan reveal dan bentuk geometris; gaya ini bergerak dengan blok warna yang menabrak frame dan cut tajam.
- Tipografi 136 px adalah batas aman untuk kata 7 huruf di safe area 700 px. Kata lebih panjang harus dipecah baris.
- GSAP disalin ke `assets/gsap.min.js`, bukan symlink, supaya clone tetap utuh.
