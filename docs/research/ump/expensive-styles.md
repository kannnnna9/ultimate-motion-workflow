# Tiga style MG Styles 15 yang tidak pas di HyperFrames

T1–T9 mengadopsi 11 dari 15 style MG Styles 15 ke UMP. Dokumen ini menjelaskan mengapa tiga sisanya tidak masuk jalur yang sama, dan apa yang harus berubah agar bisa masuk.

Ini bukan penilaian kualitas. Ketiga film ini justru yang tertinggi skornya di sebelum repositori (`10-synthwave` 8.21, `04-3d-render` 8.14). Masalahnya murni arsitektural.

## Kontrak yang dipegang UMP

HyperFrames membatasi semua yang dirender ke satu bentuk:

- satu halaman HTML per komposisi
- satu root `[data-composition-id]` dengan `data-width`/`data-height`/`data-duration`
- satu `window.__timelines.root` yang bisa di-*seek* ke waktu berapa pun
- animasi mengubah **transform dan opacity**, bukan layout
- output deterministik: frame yang sama pada t=4.167 s harus identik di setiap render

Sebelas style yang sudah diadopsi semua lolos kelima syarat itu. Tiga sisanya gagal, dan setiap kegagalannya punya sebab berbeda.

## `03-isometric` — SUDAH DIADOPSI (lihat catatan koreksi di akhir)

**Upstream memakai:** SVG dengan proyeksi isometrik 30°, tile builder, dan kamera yang bergerak di ruang 3D.

**Kegagalan:** isometrik bukan efek visual, melainkan hasil dari *transformasi koordinat*. Setiap elemen harus diletakkan lewat `(x,y,z) → (x·cos30 − y·cos30, (x+y)·sin30 − z)`. Itu bisa ditulis manual di CSS dengan `matrix3d`, tetapi begitu kamera bergerak, setiap elemen membutuhkan transform terhitung ulang dari posisi kamera — bukan tampilan tetap.

`hyperframes check` akan menolak ini bukan karena tidak bisa diraser, tetapi karena kamera bergerak di ruang 3D menghasilkan property `matrix3d` yang berubah tiap frame dari JS, yang tidak bisa dioptimasi sebagai satu tween transform. Hasilnya bisa jalan, tetapi berat dan rawan jitter saat seek.

**Status: sudah dibuat.** `styles/isometric/` mengadopsi gaya ini dengan proyeksi iso sebenarnya.

Catatan koreksi: diagnosis awal di atas keliru pada satu titik. Proyeksi iso tidak perlu dihitung ulang per frame. Geometrinya dibekukan di JS saat halaman dimuat (`proj(x,y,z)` dengan `Math.cos(30)` / `Math.sin(30)`), lalu yang dianimasikan hanya `scaleY` (bangunan tumbuh) dan `x`/`y` pada grup pembungkus (kamera menyusur paralel). Tidak ada `matrix3d` per frame, tidak ada jitter, dan `seek(t)` tetap murni.

Yang hilang dibanding upstream: bayangan lembut, ambient occlusion, dan lapisan kehidupan (mobil, drone, turbin). Lihat `styles/isometric/STYLE.md`.

## `04-3d-render` — butuh renderer terpisah

**Upstream memakai:** Three.js untuk gambar, Blender untuk bagian yang butuh shading sungguhan, lalu halaman web hanya menambahkan tipografi dan grain di atasnya.

**Kegagalan:** ini satu-satunya dari 15 yang tidak dirender penuh oleh headless Chrome. Pipeline-nya dua tahap: Blender menghasilkan gambar, web menambahkan teks. UMP tidak punya tempat untuk Blender, dan menambahkannya berarti `ump_render` bukan lagi "buka halaman, jepret 30 fps".

Selain itu, WebGL di VM ini jalan lewat llvmpipe (software rendering). Determinisme antar-driver tidak dijamin — frame yang sama bisa berbeda beberapa piksel pada mesin lain. Untuk kanal satu orang itu tidak jadi masalah; sebagai preset yang dipublikasikan, itu bug.

**Bisa dibuat?** Sebagian: three.js di WebGL software bisa menghasilkan objek 3D sederhana tanpa Blender. Kualitasnya jauh di bawah upstream.

## `10-synthwave` — butuh compositing berlapis

**Upstream memakai:** WebGL untuk grid perspektif dan matahari, canvas untuk teks chrome, dan pass VHS berlapis (RGB split → scanline → wave jitter → masker 4:3).

**Kegagalan:** masing-masing lapisan bisa dibuat di CSS. Masalahnya adalah **pass VHS**: itu compositing antar-lapisan dengan distorsi per-scanline. Di browser, satu-satunya cara yang setia adalah `mix-blend-mode` bertumpuk atau SVG filter berlapis — dan SVG filter pada seluruh 1080×1920 dengan displacement 60 fps mudah jatuh ke jalur software di VM ini.

Yang lebih penting: aturan gaya ini mengharuskan *untuk sengaja tidak stabil* (frame drop, reverse 2 frame, RGB offset acak). UMP mensyaratkan seek deterministik; ketidakstabilan yang disengaja harus dibangkitkan dari fungsi murni atas waktu, bukan dari randomness nyata. Itu bisa dilakukan, tetapi setiap efek harus ditulis ulang sebagai fungsi `f(t)`, bukan sebagai loop.

**Bisa dibuat?** Ya, paling realistis dari ketiganya. Grid perspektif dan matahari berbanding sudah cukup untuk terbaca sebagai synthwave walau VHS pass-nya disederhanakan.

## Jadi bagaimana agar bisa masuk

Urutannya dari termurah ke termahal:

1. **`10-synthwave` lewat aproksimasi DOM.** Grid dibangun dari cuplikan SVG dengan `perspective` CSS; matahari berbanding dari 6 rect; teks chrome dari gradien multi-stop + `text-shadow` dua lapis. VHS pass dikurangi jadi scanline statis. Biaya: satu preset, tidak ada perubahan arsitektur. Risiko: kehilangan tekstur VHS yang justru ciri khasnya.
2. **`03-isometric` lewat proyeksi tetap.** Tile diletakkan dengan fungsi proyeksi di JS saat build (bukan saat render), sehingga HTML statis dan kamera diam. Biaya: dua kali preset biasa, karena butuh penulis tile. Risiko: tanpa kamera bergerak, hidupnya berkurang banyak.
3. **`04-3d-render` lewat renderer kedua.** Perubahan nyata: `ump_render` harus bisa mendelegasikan ke target non-HTML, dan `claim` harus tahu bahwa target ini tidak punya `style_frame` dari HyperFrames. Biaya: besar, menyentuh `worker.py`, `tools.py`, dan kontrak stage.

Untuk nomor 3, ada jalan yang lebih jujur: **jangan** adopsi. Kalau nanti dibutuhkan, buat sebagai target terpisah dengan nama jelas (`render3d`), bukan sebagai style ke-12 di registry. Mencampur dua renderer di satu daftar style akan membuat `select_style` berbohong — operator memilih "style" padahal yang berubah adalah pipeline.

## Rekomendasi

- `10-synthwave`: kerjakan kalau ada episode yang memang butuh. Termurah dan paling aman.
- `03-isometric`: **selesai** — diadopsi sebagai `styles/isometric/` tanpa WebGL.
- `04-3d-render`: tetap butuh renderer terpisah; lihat ADR di ROADMAP T10.
- `04-3d-render`: **jangan** diadopsi ke registry style. Kalau dibutuhkan, jadikan target render terpisah.

Keputusan final perlu ADR di `docs/adr/` sebelum kode apa pun ditulis untuk T10.
