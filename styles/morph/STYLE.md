# Morph

Satu bentuk terus-menerus berubah menjadi bentuk lain, tanpa pernah berhenti di tengah jalan. Gayanya berdiri atau jatuh pada satu hal: apakah interpolasinya bersih. Satu persilangan garis saja membuat seluruh episode terlihat amatir.

**Asal:** [MG Styles 15](https://github.com/Vincentwei1021/mg-styles-15) `demos/08-morph`, commit `8fab7eb`, MIT. Aturan visual diambil dari `prompts/08-morph.md`; kodenya ditulis ulang untuk HyperFrames.

## Kapan dipakai

Rangkaian ide yang saling terhubung — "ini menjadi itu menjadi itu". Paling kuat untuk narasi urutan atau proses, karena penonton mengikuti satu objek yang terus berubah alih-alih menunggu kartu baru.

## Yang harus ada

- **Jumlah titik sama.** Setiap bentuk dibangun dari N titik yang sama (`N = 12`). Ini syarat mutlak agar tidak terbelit.
- **Titik pertama sejajar.** Semua bentuk dimulai dari sudut yang sama (jam 12), jadi titik 0 tidak melompat.
- **Bridge wajib.** A→B tidak pernah langsung. Rutenya A → lingkaran → B, masing-masing 8–12 frame. Ini yang menyembunyikan interpolasi yang mustahil.
- **Squash 10–15%** sepanjang morph, plus rotasi kecil, untuk menutupi kesan mekanis.
- **Bidang warna berganti per beat**, bukan crossfade.

## Palet

`#0E0E12` latar gelap, `#F3F2EE` bentuk, aksen `#7B5CFF` ungu, `#22C9A6` hijau laut, `#FF6B4A` koral. Bidang warna berganti di antara kelima nilai itu per beat.

## Gerak

Easing cepat di tengah, dengan bantalan 3 frame di awal dan akhir tiap kaki morph. Durasi tiap kaki 0,62 s. Jangan mempercepat di bawah 0,5 s — penonton kehilangan bentuknya.

## Jangan

- Jangan interpolasi langsung antar bentuk yang rumit.
- Jangan ubah jumlah titik "supaya lebih bagus"; itu justru penyebab kekusutan.
- Jangan taruh teks di atas bentuk yang sedang bergerak. Teks masuk setelah morph selesai.

## Signature

- `bridge` — A→B selalu lewat bentuk perantara (lingkaran), tidak pernah langsung.
- `squash` — Squash & stretch ±10% pada setiap pendaratan atau perubahan arah.
- `colourField` — Warna latar berganti per beat, bukan crossfade.
- `equalVertex` — Semua bentuk dibangun dari jumlah titik yang sama, titik pertama sejajar.
- `morphEase` — Cepat di tengah, dengan bantalan 3 frame di awal dan akhir.
