# Style: Dark Editorial

**ID:** `dark-editorial`  
**Status:** ready  
**Dipakai demo:** `demo/explain-9x16/`

Tipografi besar di atas latar gelap, dengan satu aksen warna dan satu garis/bentuk yang bergerak mengikuti logika beat. Frame terasa seperti editorial motion graphic: satu gagasan dominan, ruang kosong cukup, dan teks tidak menjadi slide presentasi.

## Kontrak visual

- Kanvas 1080×1920, 30 fps.
- Latar: `#101A22`; teks utama: `#F2EFE6`; aksen: `#FF7A45`; sekunder: `#4FB3A9`.
- Display: DejaVu Sans Bold, 88–96 px. Body: DejaVu Sans Mono, 38–46 px. Caption: DejaVu Sans Bold, 58 px.
- Satu headline maksimal tiga baris; maksimal 5 kata per baris.
- Safe area teks: x=190–890, y=380–1280. Caption di zona bawah dengan margin bawah 12% kanvas.
- Satu objek dominan per beat: headline, blok pertanyaan, atau garis.
- Hindari gradient dekoratif, kartu UI, pill, partikel acak, dan pergantian slide.

## Kontrak gerak

- Hook tampil atau mulai berubah pada 0–0,3 detik.
- Perubahan bermakna tiap 1–2 detik; easing `power2.out`, `power3.out`, atau `power2.inOut`.
- Gunakan reveal mask, fold, cut, move, scale, dan line draw.
- SFX menempel pada cue visual yang sama; offset default +1 frame.
- End card muncul setelah payoff dan diam minimal 6–8 detik.
- Timeline harus seekable dan deterministik: tanpa `Date.now()` atau `Math.random()`.

## Kontrak copy

- Beat 1: hook yang bisa dipahami tanpa konteks.
- Beat 2: masalah atau pertanyaan yang membuat penonton mengenali situasi.
- Beat 3: payoff satu kalimat.
- Teks layar menegaskan narasi; jangan menyalin seluruh VO ke layar.

## Checklist sebelum preview

- [ ] headline tidak menabrak safe area
- [ ] satu aksen dominan per frame
- [ ] frame 0 sudah punya alasan untuk ditonton
- [ ] tidak ada slide statis yang hanya mengganti teks
- [ ] cue punya aksi + SFX pada waktu yang sama
- [ ] kartu sumber terbaca penuh
