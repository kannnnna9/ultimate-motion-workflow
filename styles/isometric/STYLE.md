# Isometric 2.5D

## Asal
Diadaptasi ulang dari **MG Styles 15** `03-isometric` (commit `8fab7eb`, MIT). Upstream menamanya 「等轴 2.5D Isometric」, film peraga ISOPOLIS. Aturan visual diambil dari `prompts/03-isometric.md`; tidak ada HTML dari `demos/` yang disalin.

## Signature

- **Proyeksi isometrik sungguhan.** Kamera ortografis pada elevasi 35,264° dan azimut 45°, sehingga kedua sumbu tanah memendek sebesar `cos(30) = 0.866` dan satu langkah unit bergerak 0,5 tile di layar — lereng 2:1. Dihitung dengan `Math.cos(30)` dan `Math.sin(30)`, bukan sudut yang dikira-kira.
- **Tanpa titik hilang.** Tidak ada `perspective`, tidak ada `rotateX`. Sisi yang sejajar tetap sejajar.
- **Tiga sisi berbeda terang.** `--face_top` putih, `--face_left` lavender terang, `--face_right` lavender gelap. Kalau ketiganya sama, kubusnya membaca datar.
- **Bangunan tumbuh dari tanah** (`GROW`). Alas mendarat, dinding `scaleY` dari 0 dengan overshoot, jendela menyala berurutan.
- **Kamera menyusur paralel** (`PARALLAX`) dengan kecepatan lapisan `1:0.8:0.6`. Tidak ada rotasi — rotasi mematahkan proyeksi iso.
- **Urutan cat = koordinat y layar** (`depth`). Objek yang lebih jauh digambar lebih dulu; `zIndex` hanya cerminannya.

## Cara pakai
Pilih `isometric` di UMP. Cocok untuk: arsitektur, pusat data, alur sistem, penjelasan produk.

## Yang berbeda dari upstream
Upstream merender dengan Three.js `OrthographicCamera` plus bayangan lembut dan ambient occlusion. Di sini semuanya SVG dengan proyeksi yang dihitung sendiri, karena:

- `seek(t)` harus murni. Scene WebGL dengan `requestAnimationFrame` bisa memberi frame berbeda untuk waktu yang sama.
- Tanpa bayangan lembut dan AO. Upstream mendapat kedalaman dari kedua efek itu; di sini kedalaman datang dari kontras tiga sisi, garis tepi, dan urutan cat.

## Yang sengaja tidak diambil
Mobil, kereta, drone, turbin angin, dan aliran data. Itu lapisan "kehidupan" yang membutuhkan aset dan animasi jalan yang panjang; tidak termasuk dalam scope adopsi pertama ini.

## Batasan
`scaleY` pada grup SVG memakai `transform-box: fill-box` dan `transform-origin: 50% 100%`. Kalau sebuah bangunan terlihat mengapung alih-alih tumbuh dari alasnya, periksa dua properti itu lebih dulu.
