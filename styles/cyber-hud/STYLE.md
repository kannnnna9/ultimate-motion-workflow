# Cyber HUD

Antarmuka fiksi: lapisan garis neon, radar yang menyapu, angka yang bergulir lalu mengendap, dan kunci target yang menutup. Rapat tapi tidak ramai.

**Asal:** [MG Styles 15](https://github.com/Vincentwei1021/mg-styles-15) `8fab7eb`, style `22-hud`. Aturan visual diambil dari `prompts/22-hud.md` dan diimplementasikan ulang untuk komposisi HyperFrames vertikal UMP. Tidak ada file upstream yang disalin.

## Signature features

| Signature | Wujud di komposisi |
|---|---|
| `conic-gradient` | sapuan radar berputar sebagai kerucut 60° dengan topeng cincin |
| `lock` | empat bracket menutup target, `scale` 1.4 → 1.0 dengan ease `power3.out` |
| `scan` | overlay garis pindai 2 px dan vignette gelap di seluruh bingkai |
| `typewriter` | baris status diketik karakter demi karakter diikuti kursor |

Tambahan dari sumber: hex grid 481 sel, data column yang bergulir 10 frame lalu berhenti di nilai akhir, dan state peringatan oranye saat target terkunci.

## Palet

- ground `#04141A` dengan tepi lebih gelap
- cyan `#00E5FF` — garis dan teks utama
- alert `#FF6A00` — hanya untuk state terkunci
- dim `#0B4A55` — label sekunder

## Tipografi

Monospace sepanjang episode. Label uppercase dengan tracking `0.4em`. Judul beat diberi text-shadow cyan tipis sebagai glow.

## Motion

- garis masuk dengan `power3.out`
- rotasi cincin linear, tiga kecepatan berbeda supaya tidak terasa mekanis seragam
- blip berdenyut `yoyo`, stagger 0.3 s
- seluruh elemen non-target diredupkan ke 35% saat kunci aktif
- kamera statis; semua gerak berasal dari lapisan

## Kapan dipakai

Topik teknologi, pemantauan, pemetaan, keamanan, atau apa pun yang butuh rasa "sedang diamati". Bagus untuk eyewitness angka dan data.

## Larangan

- glow berlebihan sampai teks tidak terbaca
- lebih dari dua state warna (cyan normal, oranye peringatan)
- animasi chaos atau glitch acak tanpa makna
- kamera bergerak — kesan HUD hilang kalau bingkai ikut goyang
- font proporsional berserif

## Checklist sebelum render

1. Teks beat masih terbaca di atas lapisan scanline.
2. Angka rolling berhenti di nilai akhir yang memang disebut narasi.
3. State terkunci muncul satu kali saja, tidak berulang.
4. Frame akhir tidak menyisakan ketikan di tengah karakter.
