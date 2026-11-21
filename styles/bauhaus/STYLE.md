# Bauhaus

Poster kinetik: tiga warna primer di atas kertas krem, bentuk dasar di grid ketat, setiap gerak satu modul. Rasional dan musikal, bukan lembut.

**Asal:** [MG Styles 15](https://github.com/Vincentwei1021/mg-styles-15) `8fab7eb`, style `09-bauhaus`. Aturan visual diambil dari `prompts/09-bauhaus.md` dan diimplementasikan ulang untuk komposisi HyperFrames vertikal UMP. Tidak ada file upstream yang disalin.

## Signature features

| Signature | Wujud di komposisi |
|---|---|
| `modular` | grid 180 px; semua posisi shape ada di garis atau titik temu modul |
| `rotation` | setiap shape berputar 90° dengan `transformOrigin` bergantian: tengah diri, sudut, tengah bingkai |
| `power2.inOut` | easing seluruh gerak; tidak ada overshoot, tidak ada bounce |
| `vertical` | judul diset vertikal (`writing-mode: vertical-rl`) seperti poster Swiss |

## Palet

- paper `#F1E9DA`
- black `#111111`
- red `#E03C31`
- blue `#1E4FA3`
- yellow `#F2B705`

Tidak ada warna di luar kelima ini.

## Tipografi

Display heavy geometric sans, uppercase, `line-height` di bawah 1. Label monospace dengan tracking `0.34em`. Tidak ada serif, tidak ada italic.

## Motion

- 120 BPM, satu ketukan `0.5 s`, satu aksi per ketukan
- grid muncul lebih dulu (stagger tipis), shape menyusul
- fase gelombang: shape bergeser satu modul dengan stagger
- kamera statis — bingkai tidak bergerak, komposisi yang bergerak

## Kapan dipakai

Penjelasan yang butuh ketegasan: tahapan, struktur, hitungan, perbandingan biner. Bagus untuk topik kebijakan, proses, dan arsitektur informasi.

## Larangan

- gradien atau bayangan lembut
- overshoot, elastic, back ease
- bentuk lengkung selain busur seperempat dan setengah lingkaran
- lebih dari tiga warna primer dalam satu frame
- foto atau ilustrasi raster

## Checklist sebelum render

1. Setiap posisi shape jatuh di kelipatan 180 px.
2. Tidak ada shape yang keluar bingkai saat rotasi.
3. Label beat terang melawan kertas krem (kontras minimum tetap terjaga).
4. Frame akhir terkunci jadi poster utuh, bukan komposisi setengah jalan.
