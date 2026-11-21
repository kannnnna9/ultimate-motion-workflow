# Third-Party Notices

Repo ini berlisensi MIT (lihat [LICENSE](LICENSE)). Kode dan bahan yang diambil dari sumber luar tetap mengikuti lisensi masing-masing. Commit di bawah diperiksa pada audit T1; lihat [laporan per sumber](docs/research/ump/README.md).

Hanya **Bang Motion** yang saat ini disalin ke dalam repo (`vendor/bang-motion/`). Lima sumber lain tercatat di sini karena memberi pola kerja yang diadaptasi, tetapi **tidak** di-vendor dan tidak menjadi dependency — lihat [ROADMAP.md](ROADMAP.md) bagian E.

## Ringkasan

- Bang Motion — `c1aa65e` — MIT, © 2026 Bang Tutorial — **di-vendor**
- Reanimate — `3f7a55a` — MIT — tidak di-vendor
- LottieFiles motion-design-skill — `d77a9fc` — MIT — tidak di-vendor
- MG Styles 15 — `8fab7eb` — MIT (lisensi root; hak aset pihak ketiga belum diverifikasi) — tidak di-vendor
- Huashu Art Motion — `a97c450` — MIT — tidak di-vendor
- Anidoodle — `4ae27d2` — Apache-2.0 (software orisinal; aset berlisensi terpisah) — tidak di-vendor

## 1. Bang Motion — di-vendor

- **Sumber:** https://github.com/bangtutorial/bang-motion
- **Commit:** `c1aa65e` (tag `v1.19.0`, "shot sizes — camera moves to the element so videos don't feel static")
- **Lisensi:** MIT, © 2026 Bang Tutorial. Salinan lisensi lengkap ada di [`vendor/bang-motion/LICENSE`](vendor/bang-motion/LICENSE).
- **Peran:** pegangan desain resmi — explainer kartun + VO, anti-slideshow, blocking kamera, pemeriksaan frame kunci.
- **Yang disalin:**

  | File di repo | Baris |
  |---|---|
  | `vendor/bang-motion/SKILL.md` | 551 |
  | `vendor/bang-motion/references/explainer.md` | 579 |
  | `vendor/bang-motion/references/anti-ppt.md` | 110 |
  | `vendor/bang-motion/references/techniques.md` | 690 |
  | `vendor/bang-motion/references/architecture.md` | 188 |
  | `vendor/bang-motion/LICENSE` | 21 |

- **Yang tidak disalin:** `icon.png` (178 KB), `icon.svg`, `assets/`, `scripts/`, `docs/`, `README.md`, `CHANGELOG.md`, `.claude-plugin/`, `.git/`. Isi vendor tidak diubah; checksum sesuai sumber.

## 2. Reanimate — tidak di-vendor

- **Sumber:** https://github.com/NiazMorshed2007/reanimate
- **Commit:** `3f7a55a`
- **Lisensi:** MIT
- **Peran:** bedah animasi frame-by-frame untuk mode referensi (ROADMAP C2, masih menunggu keputusan).
- **Status:** checkout riset terpisah dari UMP, belum disalin. Tidak ada kode Reanimate di repo ini.

## 3. LottieFiles motion-design-skill — tidak di-vendor

- **Sumber:** https://github.com/LottieFiles/motion-design-skill
- **Commit:** `d77a9fc`
- **Lisensi:** MIT
- **Peran:** checklist koreografi per shot — primary/secondary/ambient, easing, stagger. Tanpa Creator MCP.
- **Status:** checkout riset terpisah dari UMP, belum disalin. Polanya diadaptasi ulang, bukan file upstream.

## 4. MG Styles 15 — tidak di-vendor, dua belas style diadopsi ulang

- **Sumber:** https://github.com/Vincentwei1021/mg-styles-15
- **Commit:** `8fab7eb`
- **Lisensi:** MIT untuk materi yang diliput `LICENSE` pada commit pinned. Hak aset pihak ketiga belum diverifikasi per file.
- **Peran:** kartu gaya + pola cue bersama (satu cue menggerakkan aksi dan SFX serentak). Lima belas style tersedia di `prompts/`; dua belas diadopsi, dua ditunda (lihat `docs/research/ump/expensive-styles.md`).
- **Status:** repo riset Git terfilter, media tidak diambil.
- **Style yang diadopsi sebagai preset UMP:**

  | Preset UMP | Upstream | Yang diambil | Yang tidak diambil |
  |---|---|---|---|
  | `styles/line-art/` | `prompts/02-line-art.md` | aturan visual, palet, aturan stroke-dashoffset, urutan ease | `demos/02-line-art/*` (HTML, `film.js`, `cues.json`, `audio.py`) |
  | `styles/bauhaus/` | `prompts/09-bauhaus.md` | grid modular, palet primer, aturan rotasi, easing tanpa overshoot | `demos/09-bauhaus/*` |
  | `styles/cyber-hud/` | `prompts/22-hud.md` | lapisan FUI, palet cyan/oranye, pola radar, lock bracket, typewriter | `demos/22-hud/*`, engine Three.js |
  | `styles/flat-vector/` | `prompts/01-flat-vector.md` | tanpa gradien, squash 10%, stagger, shape wipe | `demos/01-flat-vector/*` |
  | `styles/cel-boil/` | `prompts/05-cel-boil.md` | on-twos, boil via displacement, smear, grain | `demos/05-cel-boil/*`, brush renderer |
  | `styles/collage/` | `prompts/06-collage.md` | grid 12 fps, jitter 2 px, tepi potong, halftone | `demos/06-collage/*`, foto raster |
  | `styles/liquid/` | `prompts/07-liquid.md` | metaball blur+threshold, waveFront, drip | `demos/07-liquid/*`, shader WebGL |
  | `styles/morph/` | `prompts/08-morph.md` | vertex sejajar, bridge, bidang warna | `demos/08-morph/*`, flubber |
  | `styles/aurora-glass/` | `prompts/12-aurora-glass.md` | blob blur, kartu backdrop-filter, grain | `demos/12-aurora-glass/*`, shader WebGL |
  | `styles/paperclip/` | `prompts/19-paperclip.md` | kanvas raksasa, kartu stiker, grafik presisi | `demos/19-paperclip/*`, foto bertopeng |
  | `styles/pixel/` | `prompts/20-pixel.md` | kanvas 180x320, nearest-neighbour, dither | `demos/20-pixel/*`, sprite sheet Aseprite |
  | `styles/synthwave/` | `prompts/10-synthwave.md` | grid perspektif CSS, matahari berbanding, krom, scanline | `demos/10-synthwave/*`, renderer WebGL, canvas chrome, pass VHS bergoyang |

  Komposisi UMP ditulis ulang untuk kanvas 1080×1920 dengan kontrak token `__UMP_*__` milik UMP. Tidak ada kode atau aset upstream yang disalin; yang ditransfer adalah aturan gaya (palet, easing, larangan, checklist) dari file prompt.

## 5. Huashu Art Motion — tidak di-vendor

- **Sumber:** https://github.com/alchaincyf/huashu-art-motion
- **Commit:** `a97c450`
- **Lisensi:** MIT
- **Peran:** setujui style frame dulu, lalu petakan beat VO ke aksi visual. Engine dan konfigurasi suaranya tidak diadopsi.
- **Status:** repo riset Git terfilter. Tidak ada file upstream di repo ini.

## 6. Anidoodle — tidak di-vendor

- **Sumber:** https://github.com/alexgreensh/anidoodle
- **Commit:** `4ae27d2`
- **Lisensi:** Apache-2.0 untuk software orisinal. Aset/dependensi punya ketentuan terpisah; lihat laporan Anidoodle.
- **Peran:** ilustrasi procedural deterministik (parameter + seed tetap). Engine penuh tidak diadopsi di v1.
- **Status:** repo riset Git terfilter. Tidak ada file upstream di repo ini.

## Sumber proses non-kode

Pola alur produksi (brand sheet → naskah → VO → caption → rencana scene → preview → revisi timestamp → render) diadaptasi dari seri tutorial Threads Part 1–4. Yang tersedia hanya ringkasan, bukan file sumber, jadi tidak ada kode atau aset tutorial yang disalin.

## Aturan untuk kontribusi berikutnya

Menyalin kode, aset, atau teks dari sumber luar wajib menambah entri di sini berisi URL, commit, lisensi, file yang disalin, dan file yang sengaja dilewati. Sumber tanpa lisensi tidak boleh disalin sebelum izin tertulis dari pemiliknya.

- **`03-isometric`** → `styles/isometric/` — MIT, commit `8fab7eb`. Diimplementasi ulang dari `prompts/03-isometric.md` sebagai SVG dengan proyeksi iso terhitung; tidak ada HTML `demos/` yang disalin.
