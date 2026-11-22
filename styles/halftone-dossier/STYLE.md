# Halftone Dossier

Berkas arsip cetak untuk video vertikal. Kertas krem, halftone dua lapis,
headline serif berat dengan bayangan misregister, dan stempel yang menghantam.

Port dari spesifikasi `lemo-opuscar/styles/halftone-dossier/STYLE.md`
(CC-BY-NC-4.0). Yang diambil adalah spesifikasi dan rumusnya; tidak ada kode
yang disalin.

## Kapan dipakai

Fakta sejarah, arsip, investigasi, dokumen, koreksi klaim. Juga untuk topik
yang butuh rasa "ini diverifikasi" — angka, tanggal, sumber.

Bukan untuk: tutorial ringan, promo, atau apa pun yang butuh nada ceria.

## Ciri yang harus ada di setiap frame

- **Kertas, bukan layar.** Krem hangat, mottling, grain, vignette, lipatan.
- **Halftone, bukan gradien.** Ukuran titik mengikuti medan densitas. Dua
  lapis dengan sudut layar berbeda, di-multiply.
- **Aparatur birokrasi.** Nomor berkas, plate bernomor, label exhibit, stempel.
- **Satu ide per kartu.** Satu headline, satu plate, satu baris caption.

## Warna

Kertas `#F2E6D0`, tinta `#1A1A2E`, aksen `#E23744`, sekunder `#F2C14E`,
overprint `#2E4DA7`. Tinta gelap memegang semua garis dan teks; warna lain
hanya mengisi.

## Gerak

Tidak ada yang muncul dengan fade. Semua masuk dengan overshoot.

- **Line boil** ~10x/detik lewat seed `feTurbulence`. Garis bernapas.
- **Per-glyph pop** untuk headline, stagger 45 ms, squash menjaga volume.
- **Stamp slam** 90 ms dari 2.6x, lalu memantul berdamper.
- **Grain** di-seed ulang tiap dua frame.
- **Idle life** tidak pernah berhenti: REC berkedip, angka mengapung.

Semua dihitung dari `t` lewat `window.__umpRender(t)`. Tidak ada tween yang
"selesai" — setiap frame bisa diproduksi tanpa tahu frame sebelumnya.

## Caption

Pill tinta gelap dengan bayangan overprint. Preset menandai
`draws_captions: true`, jadi renderer tidak membakar subtitle ASS di atasnya.

## Batas

Belum ada ilustrasi karakter. Plate yang ada adalah diagram: jalur, bagan,
tumpukan lantai, penampang. Untuk subjek yang butuh gambar, diagram ini
mungkin terlalu kering.
