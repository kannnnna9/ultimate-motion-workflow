# Line Art

Satu garis kontinu, satu warna, banyak ruang kosong. Seluruh episode digambar oleh satu tarikan pena yang berpindah dari satu bentuk ke bentuk berikutnya.

**Asal:** [MG Styles 15](https://github.com/Vincentwei1021/mg-styles-15) `8fab7eb`, style `02-line-art`. Yang diambil adalah aturan visual dari `prompts/02-line-art.md` dan diimplementasikan ulang untuk komposisi HyperFrames vertikal UMP. Tidak ada file upstream yang disalin.

## Signature features

| Signature | Wujud di komposisi |
|---|---|
| `stroke-dashoffset` | setiap path digambar dengan `strokeDasharray` = panjang path, `strokeDashoffset` dari panjang → 0 |
| `pen-tip` | titik glow mengikuti ujung pena; menyala saat menggambar, mati saat berhenti |
| `line-to-fill` | bentuk terakhir mengisi dirinya (`fill-opacity` 0 → 0.92) dari anchor |
| `dolly` | kamera mendorong masuk 4% sepanjang durasi agar ujung pena tetap di bingkai |

## Palet

- ground `#0B1320` — midnight, dengan radial gradient tipis ke `#132133` di tengah
- ink `#E9D7A5` — champagne gold, satu-satunya warna garis
- dim `#4A5568` — segmen lama yang sudah dilalui (opacity 35%)

## Tipografi

Serif untuk display, tracking lebar (`0.16em`), uppercase. Wordmark menggunakan `0.62em`. Body sans, warna `#B8AE8B` agar tidak menyaingi garis.

## Motion

- ease `power2.inOut` untuk semua penggambaran — kecepatan melambat di sudut
- segmen yang sudah lewat diredupkan ke 18–35% saat kamera lanjut
- tidak ada overshoot; garis berhenti tepat di ujung path

## Kapan dipakai

Cerita arsitektur, tempat, perjalanan, atau penjelasan yang punya satu benang merah. Kurang cocok untuk daftar angka atau perbandingan kuantitatif — garis tidak bisa memuat tabel.

## Larangan

- lebih dari satu warna garis
- ketebalan garis berubah-ubah
- garis berhenti di tengah tanpa lanjut ke bentuk berikutnya
- background putih

## Checklist sebelum render

1. Setiap path punya koordinat yang menyambung dengan path sebelumnya.
2. Tidak ada `opacity: 0` permanen pada path yang seharusnya tergambar.
3. Kartu beat masuk di atas area garis, tidak menutupi fokus.
4. Frame terakhir memuat wordmark dan seluruh bentuk sudah stabil.
