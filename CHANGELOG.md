# Changelog

Perubahan yang terlihat oleh pengguna dicatat di sini mengikuti [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) dan [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.6.1] - 2026-11-23

### Fixed
- `ump_render` sekarang merekam preset WebGL dengan `scripts/record_frames.mjs`, bukan HyperFrames. Preset menandai dirinya `renderer: "webgl"` dan `ump_core/worker.py` memilih jalur itu, lalu meng-encode PNG hasil rekaman menjadi `frames.mp4` biasa sehingga sisa pipeline (mux audio, cutoff end card, artifact) tidak berubah. Sebelumnya satu-satunya cara mendapat video WebGL yang benar adalah menjalankan recorder manual di luar plugin.
- Rekaman tidak lagi membersihkan `framesraw` lebih dulu: recorder yang memilikinya, dan pembersihan ganda membuat pengecekan hasil balapan dengan proses capture saat run diulang.

### Notes
- `edge-tts` perlu >= 7.3.1. Versi 7.2.7 mengirim `Sec-MS-GEC` yang sudah kedaluwarsa dan Edge TTS membalas 403.

## [1.6.0] - 2026-11-23

### Added
- Stage `storyboard` di antara `naskah_approved` dan `style_frame`. Operator menyusun naskah dulu, lalu mengirim gambar storyboard (dibuat di ChatGPT atau alat lain) lewat aksi `attach_storyboard`; frame dan video diturunkan dari gambar itu. Storyboard opsional: tanpa gambar, jalur lama tetap berjalan.
- Aksi `ump_job` `attach_storyboard` dengan validasi lampiran gambar dari runtime cache (regular file, milik user, `O_NOFOLLOW`, batas 20 MiB).
- `scripts/record_frames.mjs`: perekam frame deterministik untuk preset WebGL. Seek → tunggu GPU → screenshot → encode. Dipakai karena HyperFrames di mesin ini tidak bisa merekam WebGL dengan benar (`BeginFrame did not run`, jatuh ke mode screenshot yang menangkap sebelum canvas selesai).
- Test `tests/test_storyboard.py`: urutan stage, gerbang frame, dan approval kalimat biasa.

### Changed
- Approval di chat tidak lagi memerlukan `UMP APPROVE <job> <rev> <stage> <artifact>`. Kalimat eksplisit — "setuju", "ya setuju", "lanjut", "gas", "sip", "mantap", "oke" — menyetujui stage yang sedang menunggu. Bentuk perintah tetap diterima untuk skrip, tetapi tidak pernah diminta ke operator.
- Permintaan perubahan ("ganti", "revisi", "perbaiki", "kurang") dibaca sebagai penolakan, bukan approval.
- Kata approval dicocokkan sebagai whole word: "pas" tidak lagi menyala di dalam "napas".

### Fixed
- `window.__hf` sekarang diekspos preset WebGL (`duration` + `seek`), sehingga recorder melakukan seek alih-alih merekam frame nol berulang kali.
- `refreshSpans` dipanggil sebelum draw pertama; sebelumnya `window.__SHOT_SPAN__` tetap `null` dan `drawScene` keluar lebih awal, jadi seluruh video memutar satu scene statis.
- Seek menunggu GPU selesai (`gl.readPixels` + `gl.finish`) agar frame hasil tangkapan cocok dengan timestamp-nya.
- `worker.py` jalur mux menghormati `draws_captions`; sebelumnya subtitle ASS selalu dibakar meskipun preset menggambar caption sendiri, sehingga teks muncul dua kali.
- Kartu beat menjadi satu-satunya lapisan teks di preset `webgl-3d-stage`.

## [1.5.0] - 2026-11-22

### Added
- Plate exhibit hidup: setiap plate adalah fungsi `(t, t0)` dan menggerakkan bagiannya sendiri tiap frame. Penumpang berjalan dari kerumunan ke pintu bus, mengecil saat naik, lalu hilang; pintu terbuka dan menutup lagi. Kerumunan bergoyang sebagai satu badan. Jendela gedung menyala-padam dengan fase berbeda. Pita bayangan melintas di dinding kaca. Lampu jalan berdenyut. Bus dan empat mobil melintas di plate 5.
- Test gerak dari piksel (`tests/test_halftone_motion_pixels.py`) plus `tests/support/still.mjs`: merender dua momen per beat dengan grain dan boil dimatikan, lalu menuntut perbedaan rata-rata di atas ambang. Test unit tetap cepat karena suite ini hanya jalan dengan `UMP_BROWSER=1`.

### Fixed
- Jam masuk plate memakai `beatIn()` (1,1 detik pertama beat), bukan progres beat. Dengan progres beat, bus bergerak 0,46 px dari 230 px dan plate terbaca diam — tanpa satu pun error.
- Sosok plate 3 digambar setelah dek lantai. Sebelumnya setiap sosok tertimpa slab di atasnya: DOM melaporkan 21 sosok, layar kosong.
- Pintu bus diberi lubang berlatar kertas di belakang daun. Daun gelap di atas bodi gelap yang bergeser 16 px sama saja dengan pintu yang tidak pernah terbuka.

## [1.4.0] - 2026-11-22

### Added
- Preset `halftone-dossier`: berkas arsip cetak — kertas krem, halftone dua lapis dengan sudut layar 30°/15°, headline serif berat dengan bayangan misregister, stempel yang menghantam, dan lima plate exhibit. Port dari spesifikasi `lemo-opuscar/styles/halftone-dossier/STYLE.md` (CC-BY-NC-4.0, hanya spesifikasi dan rumus; tidak ada kode yang disalin).
- Field preset `draws_captions`: style yang menggambar caption bar sendiri menandainya `true`, dan `scripts/render.py` berhenti membakar subtitle ASS di atasnya. Tanpa ini baris yang sama muncul dua kali dan tidak ada yang terbaca.

### Changed
- `halftone-dossier` memakai model waktu, bukan tween: `window.__umpRender(t)` menghitung setiap frame dari `t`, sehingga tidak bisa "berhenti menunggu tween selesai" seperti animasi slide.
- Line boil: `feTurbulence` dengan seed `1 + floor(t*10) % 40`, jadi gambar digambar ulang ~10x/detik dan garis tepi bernapas. Diterapkan ke kartu dan plate saja, tidak pernah ke teks.
- Per-glyph headline: tiap huruf jatuh, mengempis dengan volume terjaga (`scale(1/sq, sq)`), lalu memantul.
- Grain per-frame: bintik kertas dan tinta di-seed ulang setiap dua frame, jadi tidak ada dua frame yang identik.

- Ilustrasi pada plate: sosok manusia (jas, kepala, penutup kepala, lengan berayun), kerumunan dengan tinggi berbeda, bus Transjakarta, pohon, dan gedung dengan jendela menyala. Semua digambar dari geometri murni dengan seed tetap.

### Fixed
- Headline dirender dari satu lapis teks dengan `text-shadow`, bukan dua lapis DOM. Lapis bayangan yang absolut menumpuk semua huruf di satu titik dan menyisakan huruf yatim seperti "W" di bawah headline.
- Glyph dibungkus per kata. Glyph `inline-block` yang lepas membuat baris patah di tengah kata ("CU MA", "BANY AK").
- `el()` kembali menerima induk. Tanpa itu setiap bentuk dibuat tapi tidak pernah ditempelkan: plate tampil sebagai kotak kosong tanpa satu pun error.
- `figure()` menerapkan `translate(x, y)`. Sebelumnya semua sosok digambar di titik nol, menumpuk di pojok plate, dan tidak terlihat di frame.
- `scripts/render.py` membaca `draws_captions` dari `styles/<style_id>/preset.json`; `style_id` diambil dari `brand.json` atau `--style`. Saat merender proyek dari `--project`, style yang dibangunkan bisa berbeda dari `brand.json`, jadi `--style` harus disebut eksplisit.

## [1.3.0] - 2026-11-22

### Changed
- `ump_core/worker.py` memisahkan `CODE_ROOT` (kode) dari `DATA_ROOT` (data). Fixture kini tinggal di `~/.hermes/ump/fixtures/`, bukan di dalam pohon kode plugin, sehingga reinstall tidak lagi menimpa fixture yang dikoreksi operator.
- Fixture disemai sekali saat plugin didaftarkan lewat `seed_fixtures()`; direktori yang sudah ada tidak pernah ditimpa.

### Added
- `$UMP_DATA_ROOT` untuk menempatkan data di lokasi lain.
- `docs/MAINTENANCE.md` — dua salinan, pemisahan kode/data, prosedur rilis, aturan versi, dan cara menangkap bug yang lolos test.

## [1.2.0] - 2026-11-22

### Added
- Persetujuan operator dapat berupa kalimat biasa ("ya setuju", "oke lanjut", "sip gas"), tidak lagi hanya bentuk `UMP APPROVE <job> <rev> <stage> <artifact>`.

### Changed
- **Urutan pipeline:** style kini dipilih setelah riset dan **sebelum** naskah ditulis, sehingga naskah disusun untuk mengisi register visual yang sudah pasti. Urutan baru: `intake → brief → research → style → naskah → style_frame → preview → final`.
- Merevisi `style_id` sekarang menjatuhkan job ke `style_approved`, bukan `naskah_approved`, karena naskah yang sudah ada ditulis terhadap style lama.
- Bentuk `UMP APPROVE` tetap berlaku sebagai jalan alternatif untuk mengunci persetujuan ke artefak tertentu.

### Security
- Klasifikasi persetujuan menolak pertanyaan ("setujui?"), kalimat pengandaian atau yang mendelegasikan penilaian ke model ("anggap saja setuju", "bisa dianggap setuju", "menurutku setuju"), dan kalimat yang menyetujui dua tahap sekaligus. Semuanya menghasilkan `APPROVAL_UNCLEAR` agar ditanyakan ulang, bukan ditebak.

## [1.1.0] - 2026-11-22

### Added
- Preset `isometric` (2.5D isometrik) — proyeksi 30° sungguhan di SVG: `Math.cos(30)`/`Math.sin(30)` untuk foreshortening, tiga sisi dengan kontras berbeda, bangunan tumbuh dari alas, dan kamera menyusur paralel dengan rasio `1:0.8:0.6`. Katalog jadi 18 preset, 17 `ready`.
- Sepuluh test kontrak baru untuk `isometric`, termasuk pengunci durasi timeline, larangan child timeline yang paused, dan cakupan teks beat.

### Fixed
- Kartu episode hilang dari layar selama beberapa detik: tiap kartu kini memegang layar sampai beat berikutnya mengambil alih, dan kartu terakhir bertahan sampai akhir.
- Komposisi bisa melaporkan durasi tak terhingga saat sebuah tween berulang tanpa batas berada di dalamnya; durasi kini ditambatkan eksplisit ke durasi episode.

### Changed
- `docs/research/ump/expensive-styles.md` mengoreksi diagnosis `03-isometric`: proyeksi iso tidak perlu dihitung ulang per frame, sehingga gaya ini dapat diadopsi tanpa WebGL dan tanpa renderer terpisah.

## [1.0.0] - 2026-11-21

### Added

- **Sembilan preset style tambahan dari MG Styles 15** — `flat-vector`, `collage`, `morph`, `paperclip`, `pixel`, `cel-boil`, `liquid`, `aurora-glass`, `synthwave`. Total preset `ready` sekarang dua belas, semuanya ditulis dari aturan visual di `prompts/`, bukan disalin dari `demos/`.
- **`styles/flat-vector/`** — tanpa gradien, squash & stretch ±10%, stagger 3 frame, shape wipe, skyline tumbuh per gedung.
- **`styles/collage/`** — grid 12 fps dengan jitter ±2 px, tepi potong putih, halftone, kertas koran berlapis.
- **`styles/morph/`** — semua bentuk 12 titik sejajar, morph selalu lewat bridge lingkaran sehingga tidak pernah terbelit.
- **`styles/paperclip/`** — kanvas 6000×3000 yang digeser kamera, kartu stiker outline putih, grafik berskala dengan count-up.
- **`styles/pixel/`** — kanvas internal 180×320 skala ×6 nearest-neighbour, dither Bayer, sprite sebagai array piksel, langkah diskret.
- **`styles/cel-boil/`** — line boil dari `feTurbulence` yang seed-nya berganti tiap dua frame, animasi on twos, smear 1/24 s, grain kertas.
- **`styles/liquid/`** — metaball tanpa WebGL (blur 22 px + threshold `feColorMatrix`), front gelombang berfase, drip dengan squash.
- **`styles/aurora-glass/`** — blob ber-blur 130 px yang mengalir pelan, kartu `backdrop-filter`, palet warna bertetangga, lensa pembesar.
- **`styles/synthwave/`** — grid perspektif `rotateX(74deg)` dalam `perspective: 520px`, matahari berbanding lewat `mask-image`, judul krom `background-clip:text` dengan glow 4 px + 30 px, scanline statis. Pengganti murni CSS untuk WebGL upstream.
- **`docs/research/ump/expensive-styles.md`** — diagnosis mengapa `03-isometric` dan `04-3d-render` tidak bisa masuk jalur HyperFrames, beserta tiga jalur adopsi dan rekomendasinya.
- **Kontrak style diperketat di test** — komposisi wajib mengekspor `window.__timelines={root:tl}`, dilarang menyisakan `__UMP_TIMELINE__`/`addCue(`, wajib membaca `__UMP_CARDS__` dari DOM (`#cards .beat`) dan memasang `data-width`/`data-height`/`data-duration` di root.

### Changed

- **`scripts/render.py` mengambil nama file komposisi dari registry** — sebelumnya selalu mencari `index.html`, sehingga preset `ump.html` gagal sebelum render dimulai.
- **Animasi yang menyentuh `top` diganti `transform`** di `liquid`; `hyperframes check` menolak animasi layout karena menghasilkan stutter saat seek per frame.

### Fixed

- **Delapan style baru tidak menyala di HyperFrames** — `window.__timelines` hilang (frame diam di 0 s), `addCue is not defined` dari token `__UMP_TIMELINE__` yang tertinggal, dan `__UMP_CARDS__` yang diperlakukan sebagai array JSON padahal berisi markup. Ketiganya kini dikunci test.
- **Kontras teks di bawah WCAG 3:1** pada `aurora-glass`, `liquid`, `morph`, dan `paperclip`; diperbaiki dengan scrim dan palet label.
- **Teks keluar kanvas / tertutup elemen lain** pada `collage`, `paperclip`, dan `morph`.
- **`synthwave` render tanpa matahari.** Variabel `--sun_top`/`--sun_mid`/`--sun_bottom` hanya ada di `preset.json`, tidak pernah dideklarasikan di CSS, sehingga Chrome membuang seluruh deklarasi gradien dan kotak transparan yang tersisa lolos semua pemeriksaan. Test baru menolak `var()` yang tidak pernah dideklarasikan.
- **Grid `synthwave` diam.** `rotateX` dan scroll transform berada di satu elemen sehingga translasi menimpa kemiringan; `background-size` juga tertelan shorthand. Kini dipisah: `#tilt` memegang kemiringan, `#grid` bergerak di dalamnya, dan test mengunci satu elemen tidak boleh memegang keduanya.
- **Animasi `letterSpacing`** di judul synthwave ditolak sebagai motion non-transform; diganti opacity + transform.

- **Tiga preset style dari MG Styles 15** — `line-art`, `bauhaus`, `cyber-hud`. Ditulis dari aturan visual di `prompts/02-line-art.md`, `prompts/09-bauhaus.md`, dan `prompts/22-hud.md`, bukan disalin dari `demos/`.
- **`styles/line-art/`** — garis kontinu champagne di atas midnight; draw-on lewat `stroke-dashoffset`, pen-tip glow, line-to-fill, kamera dolly 4%.
- **`styles/bauhaus/`** — kertas krem, tiga warna primer, grid modular 180 px, rotasi pivot bergantian, 120 BPM tanpa overshoot, judul vertikal.
- **`styles/cyber-hud/`** — lapisan FUI cyan/oranye, radar `conic-gradient`, lock bracket, scanline, typewriter, hex grid 481 sel, data rolling yang mengendap.
- **`tests/test_new_styles.py`** — menguji registrasi, signature benar-benar dipakai di komposisi, palet dan kamera tidak menduplikasi style lama, dan build nyata lewat `build_project`.
- **`signatures` di preset.json** — setiap preset menyatakan token signature-nya, dan test menolak preset yang mendeklarasikan fitur yang tidak ada di komposisinya.

- **Pipeline bertahap yang tidak bisa dilompati** — job UMP sekarang berjalan `intake → brief_draft → brief_approved → research_draft → research_approved → naskah_draft → naskah_approved → style_approved → style_frame → preview → final_ready`. Lompatan ditolak server dengan `STAGE_ORDER`, bukan hanya dilarang di prompt.
- **Brief disusun UMP dari topik** — `ump_job create` menerima topik, lalu `set_brief` menulis brief kerja (audiens, durasi, sudut cerita) yang harus disetujui operator sebelum riset dimulai.
- **Riset sebagai tahap wajib** — aksi `research` merekam `sources`; tanpa minimal satu sumber naskah tidak bisa ditulis (`RESEARCH_REQUIRED`).
- **Validasi `fact_refs`** — setiap beat wajib mengutip sumber yang sudah direkam; referensi asing atau kosong ditolak dengan `FACT_REFS`.
- **Jalur import naskah** — operator yang sudah punya naskah sendiri memakai `import_naskah`, mendarat di `naskah_draft` dengan `origin: "imported"`, dan tetap wajib melewati approval naskah serta menyertakan sumber.
- **Pemilihan style setelah naskah disetujui** — `select_style` hanya boleh dipanggil di `naskah_approved`, sehingga preset dipilih berdasarkan ritme dan struktur beat yang sudah final.
- **Artifact dokumen dari worker** — target `brief`, `research`, `naskah`, dan `style_decision` menerbitkan JSON reviewable lewat `ump_render` tanpa menjalankan HyperFrames.
- **Approval bertahap** — `UMP APPROVE <job_id> <rev> <stage> <artifact_id>` kini menerima stage `brief`, `research`, `naskah`, `style`, `script_style`, dan `preview`.
- **Migrasi database job** — database job lama ditambahi kolom `stage` dan `origin` secara idempoten; job lama tetap terbaca.

- **Acceptance gate dan cleanup audit** — `minimal-explainer`, `scripts/verify_ump.py`, `tests/test_acceptance.py`, `scripts/cleanup_research.py`, `tests/test_cleanup.py`, serta bukti render dan manifest riset di `docs/verification/`.

- **Style `kinetic-bold` naik ke `ready`** — komposisi `styles/kinetic-bold/index.html` (blok warna, kinetic typography, cut tajam), preview di `styles/kinetic-bold/preview/`, sudah lolos `hyperframes check` 0 error dan dirender 1080×1920 30 fps.
- **`render.py` memilih komposisi dari `style_id`** — tanpa `--project`, komposisi diambil dari `styles/registry.json`. Style berstatus `spec` ditolak dengan pesan jelas.

- **Desain v1** — spesifikasi explainer vertikal 9:16 berbahasa Indonesia lewat Hermes dan HyperFrames, status disetujui operator.
- **README** — status proyek, prasyarat, alur satu episode lima langkah, cara mengembangkan workflow.
- **ROADMAP** — pemisahan tegas antara v1.0 yang ditetapkan, milestone M1–M6, empat arah yang menunggu keputusan (C1–C4), keputusan teknis terbuka (D), dan referensi eksternal yang belum di-vendor (E).
- **CHANGELOG** — dokumen ini.
- **LICENSE** — MIT, © 2026 kannnnna9.
- **THIRD_PARTY_NOTICES.md** — enam sumber rujukan dengan commit, lisensi, file yang disalin dan yang sengaja dilewati. MG Styles 15 dan Anidoodle dicatat tanpa lisensi di upstream.
- **Vendor Bang Motion** — `vendor/bang-motion/`: `SKILL.md`, `references/{explainer,anti-ppt,techniques,architecture}.md`, dan `LICENSE` dari commit `c1aa65e` (v1.19.0). Tanpa `icon.png`, aset, script, atau `.git/`.
- **`.env.example`** — variabel HyperFrames `0.9.72`, VO, caption, output.
- **`.gitignore`** — OS, `node_modules`, `.env` (kecuali `.env.example`), cache, `out/`, media hasil render, dan metadata git upstream di vendor.
- **Folder placeholder** — `templates/episode/`, `scripts/`, `demo/`, `skills/` masing-masing dengan `.gitkeep`.
- **`templates/episode/`** — brand sheet, naskah, dan cue sebagai data JSON terpisah dari komposisi, dengan skema JSON, validator lintas berkas, dan episode contoh `_contoh` yang lulus validasi.
- **`scripts/`** — `setup`, `check`, `caption` (VO + caption per kata), `subs` (ASS), dan `render` (HyperFrames → caption terbakar + audio → verifikasi ffprobe). HyperFrames dipatok `0.9.72`.
- **Demo explainer 9:16** — `demo/explain-9x16/` (data episode) dan `demo/hyperframes/` (komposisi). Tiga beat, VO Edge TTS, caption sinkron, kartu sumber. MP4 1080×1920 30 fps H.264/AAC terukur lewat `ffprobe`.
+- **Portability verifier** — `scripts/verify_portability.py` menguji archive `HEAD` sebagai clean clone; `--render` menjalankan render master dari clone tersebut.
- **Style registry** — `styles/registry.json` mencatat preset `dark-editorial`, `kinetic-bold`, dan `minimal-explainer` (ready), serta `botanical-paper` (spec). Detail aturan tiap preset ada di `styles/<id>/STYLE.md`; brand sheet memilihnya lewat `style_id`.
- **`skills/motion-pipeline/SKILL.md`** — alur Hermes dari pilih style → brand sheet → naskah → VO/caption → cue → preview → render → review.

### Changed

- **`THIRD_PARTY_NOTICES.md` §4** — MG Styles 15 sekarang mencatat tiga style yang diadopsi, file upstream mana yang dibaca, dan file upstream mana yang tidak diambil.
- **`ROADMAP.md`** — status `main` jadi `c323ddd`; T9 ditambahkan dengan daftar 12 style MG Styles 15 yang **belum** diadopsi.

- **`skills/motion-pipeline/SKILL.md`** — alur diubah dari "pilih style dulu" menjadi topik → brief → riset → naskah → style → style frame → preview → final, dengan tabel stage dan penegasan bahwa langkah tidak boleh dilewati.
- **`ump_render`** — target diperluas ke dokumen review (`brief`, `research`, `naskah`, `style_decision`) selain `style_frame`, `preview`, dan `final`.
- **Revisi menurunkan stage** — revisi naskah setelah approval membatalkan approval hilir; style frame tidak bisa di-claim ulang tanpa approval baru.

- **ROADMAP** — v1.x, v3, dan v4+ tidak lagi ditampilkan sebagai fase terjadwal. Ketiganya dipindahkan ke bagian "Menunggu keputusan operator" karena desain hanya menyebutnya bersyarat atau eksploratif, bukan komit.
- **README** — status implementasi: sebelumnya "implementasi belum dimulai" dengan daftar artefak hilang, sekarang "M1 selesai; M2–M6 belum" dengan daftar artefak yang sudah ada. Bagian referensi menautkan `THIRD_PARTY_NOTICES.md` dan lokasi vendor.

### Notes

Rilis pertama yang dinyatakan stabil. `1.0.0` dipakai karena permukaan publiknya sudah beku: kontrak plugin (`ump_styles`, `ump_job`, `ump_render`, `ump_artifact`, hook `pre_gateway_dispatch`), kontrak token komposisi (`__UMP_CARDS__`, `__UMP_EPISODE__`, `__UMP_DURATION__`), dan skema `styles/registry.json`. Selama ketiganya tidak berubah, perilaku yang sudah ada tidak boleh berubah.

Aturan penomoran setelah ini:

- **MAJOR** naik kalau kontrak di atas berubah dengan cara yang memutus pekerjaan lama — misalnya token komposisi diganti nama, aksi `ump_job` dihapus, atau field `registry.json` wajib bertambah.
- **MINOR** naik kalau ada preset style baru, target render baru, stage baru, atau aksi tool baru yang tidak memutus yang lama.
- **PATCH** naik untuk perbaikan bug, kontras, tata letak, dan perubahan dokumen yang tidak mengubah perilaku.

Penambahan preset style selalu MINOR, bukan PATCH, karena operator bisa memilih sesuatu yang sebelumnya tidak ada.
