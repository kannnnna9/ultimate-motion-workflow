# UMP Hermes Plugin Implementation Plan

**Goal:** Dari Telegram DM, buat, tinjau, revisi, dan ambil MP4 motion bersuara; buktikan gaya bisa ditambah dan keenam sumber berkontribusi.
**Architecture:** Plugin native `ump` menjadi adapter tipis atas penyimpanan job dan worker Python. Worker memakai kontrak episode dan preset, merakit project HyperFrames yang mandiri, lalu memakai FFmpeg untuk audio/caption/master. Jalur CLI render tetap didukung.
**Tech stack:** Hermes Agent 0.21.5 (baseline teramati), Python 3.11, stdlib SQLite dan unittest, HyperFrames 0.9.72, GSAP lokal berlisensi, Node 22, Chrome, FFmpeg 6.1.1, jsonschema 4.26.0, Edge TTS 7.3.1.
**Spec:** [`2026-11-21-ump-hermes-plugin-design.md`](../specs/2026-11-21-ump-hermes-plugin-design.md), desain disetujui, commit `8b49aa0`.

## Global Constraints

- “Baseline target Linux, MP4 vertikal 1080×1920 30 fps H.264/AAC; format baru di luar acceptance awal.”
- “Telegram DM menjadi jalur chat yang diuji.”
- “UMP tidak menerbitkan video ke platform sosial.”
- “Waktu kanonik adalah milidetik bulat dari frame nol preview.”
- “Tidak ada salin kode/aset tanpa hak yang jelas.”
- “Jangan jalankan skrip upstream sebelum ditinjau.”
- Versi baseline bukan klaim kompatibilitas lintas versi. Hak/kredit per file dipertahankan. Tidak ada clone sumber dalam dependency runtime. Tidak install ke profil default, push, merge, atau kirim ke chat lain pada fase build tanpa otorisasi terkait.

## Review Focus

1. Persetujuan usang atau final diminta dari preview lain → T2/T6.
2. Worker gagal, keluar mendadak atau dua kali submit → T4/T6.
3. Isi baru tidak masuk ke komposisi, HTML menginterpretasi input sebagai markup, atau SFX bergeser sendiri → T3/T5/T6.
4. Asset/artefak keluar dari workspace, lookup bocor antarprofil, atau bukti Telegram hanya berasal dari simulasi → T2/T4/T6.
5. Sumber tidak bisa diaudit, hak tidak jelas, atau clone punya pekerjaan unik → T1/T7.

## Dependency graph and lanes

`T1 → T2 → T3 → T4 → T6 → T7`; `T3 → T5 → T6`; T4 dan T5 boleh paralel setelah T3 (file disjoint). Enam audit T1 berjalan paralel, masing-masing hanya menulis satu source report; koordinator menggabungkan dan mengoreksi attribution. Owner koordinasi dan integrasi: agent utama; owner audit: satu reviewer per sumber; owner acceptance di HP: operator Telegram. Tidak ada tanggal rilis yang dijanjikan: promosi berdasarkan exit gate.

Tulis ledger build di `.ultra/ledger-ump-plugin.md`: commit, owner, command, exit status, artefak, keputusan. Bukti final yang dapat dibaca tanpa ledger lokal masuk `docs/verification/ump.md`. Jika temuan membatalkan desain teknis sebuah task, edit task dan graph dulu, tulis `Plan change: <task> — <why>` di akhir plan, lalu lanjut. Jika kontribusi wajib hilang, mekanisme pengecualian di spec berlaku.

## File map

- T1: `docs/research/ump/{bang-motion,reanimate,lottiefiles,mg-styles-15,huashu,anidoodle}.md` (audit terpisah), `docs/research/ump/README.md` (manifest dan matriks keputusan), `THIRD_PARTY_NOTICES.md` (status hak dan provenance).
- T2: `ump_core/__init__.py`, `ump_core/jobs.py` (workspace, revisi, transisi), `tests/test_jobs.py`, `.gitignore` (artefak lokal saja).
- T3: `ump_core/{presets,project}.py` (manifest, staging/timeline), `styles/registry.json`, `styles/{dark-editorial,kinetic-bold}/{preset.json,STYLE.md,index.html,hyperframes.json}`, `styles/runtime/{gsap.min.js,README.md}`, `scripts/render.py`, `templates/episode/validate.py`, `tests/test_project.py`. Komposisi demo lama tetap ada untuk regresi.
- T4: `plugin.yaml`, `__init__.py` (register), `ump_core/{tools,worker}.py` (adapter dan worker), `skills/motion-pipeline/SKILL.md`, `scripts/probe_hermes_plugin.py`, `tests/test_worker.py`.
- T5: `ump_core/{reference,illustration}.py`, `scripts/reference_frames.py`, `demo/ump-second/{brand,naskah,cue}.json`, `demo/ump-second/assets/audio/vo.mp3`, `demo/ump-second/fixtures/vo-boundaries.json`, `demo/ump-second/fixtures/LICENSE.md`, `demo/ump-second/assets/{illustration.svg,audio/cue-accent.wav}`, `tests/test_adoptions.py`.
- T6: `styles/minimal-explainer/{preset.json,STYLE.md,index.html,hyperframes.json}`, `styles/registry.json`, `styles/README.md`, `scripts/verify_portability.py`, `scripts/verify_ump.py`, `tests/test_acceptance.py`, `docs/verification/ump.md`, `README.md`, `ROADMAP.md`, `CHANGELOG.md`. T6 baru mengubah registry setelah T3 selesai, dan setelah render preset baru ditinjau.
- T7: `scripts/cleanup_research.py`, `tests/test_cleanup.py`, `docs/verification/ump.md`; manifest path aktual dan quarantine tetap di scratch, tidak ikut commit.

Tes Python berjalan via `.venv/bin/python -m unittest discover -s tests -p 'test_<task>.py' -v`; buat venv lewat `python3 scripts/setup.py` bila belum tersedia. Tiap task simpan RED (assertion perilaku gagal, bukan sekadar import gagal; jika perlu, pasang signature minimal) dan GREEN. Commit lokal hanya file task, pesan tercantum di akhir task; jangan stage direktori yang juga disentuh lane lain.

### Task 1: Audit sumber dan putuskan bentuk adopsi

**Brief:** Baca notices dan bagian riset spec. Inspeksi commit yang disebut, source tree, lisensi per komponen, entry point, serta input/output contoh. Jangan copy file atau menjalankan kode upstream secara otomatis.
**Files:** T1 pada file map. **Produces:** enam report, tiap report memuat URL, full SHA, lokasi bukti upstream, hak, skenario yang ditelusuri, pilihan kontribusi, path implementasi UMP, dan ID tes penerimaan.

- [ ] Inventaris scratch dan ruang dengan `df -h "$TMPDIR"`; check Bang Motion `git status --porcelain=v1 --untracked-files=all --ignored` dan verifikasi SHA sebelum pakai. Untuk lima lainnya, utamakan Git partial fetch pada commit pinned, tanpa checkout penuh, dan baca blob yang dibutuhkan. Simpan manifest riset di direktori scratch khusus yang bertahan antar-sesi build: source ID, root kanonik, remote, HEAD, semua refs dan status (termasuk ignored). Catat path manifest di ledger T1 untuk T7; tidak mengganti revision diam-diam bila inaccessible.
- [ ] Telusuri Bang Motion `c1aa65e` (kamera, starter, timeline, review); Reanimate `3f7a55a` (seleksi/perbandingan frame); LottieFiles `d77a9fc` (koreografi utama/pendukung/ambient); MG Styles 15 `8fab7eb` (kartu gaya dan cue); Huashu `a97c450` (style frame dan beat VO); Anidoodle `4ae27d2` (input/seed ke ilustrasi). Pakai string commit dan URL dalam notices; tautkan bukti ke full SHA di report. Catat tepat bahan yang tidak bisa diamati.
- [ ] Hubungkan enam temuan ke T2–T6. Buktikan satu perilaku masing-masing, bukan sebatas menambahkan nama ke README. Jika target awal gagal, ganti dengan kontribusi teruji dari sumber yang sama dan patch blueprint sebelum mulai consumer. Jika tidak ada, tandai blokir dan minta pengecualian; jangan lanjut ke cleanup.
- [ ] Jalankan check matriks: `python3 -c 'from pathlib import Path; p=Path("docs/research/ump"); assert {x.stem for x in p.glob("*.md")} == {"README","bang-motion","reanimate","lottiefiles","mg-styles-15","huashu","anidoodle"}'`. Koordinator buka dan cocokkan enam bukti file/full SHA serta enam link uji; catat PASS hanya bila semuanya valid.
- [ ] Commit `docs/research/ump` dan `THIRD_PARTY_NOTICES.md`: `docs(ump): audit six motion references`.

### Task 2: Job persisten dan persetujuan revisi

**Brief:** Baca spec bagian job dan temuan Huashu. T2 tidak memanggil Hermes atau render. **Files:** T2 pada file map.
**Produces:** `JobStore(root: Path)`, `create(brief: str) -> dict`, `revise(job_id: str, rev: int, patch: dict) -> dict`, `annotate(job_id: str, rev: int, t_ms: int, cue_id: str | None, text: str) -> dict`, `approve(job_id: str, rev: int, stage: str, artifact_id: str) -> dict` (actor approval gate in T4), `get(job_id: str) -> dict`, `claim(job_id: str, rev: int, target: str) -> dict`, `get_run(run_id: str) -> dict`, `finish(run_id: str, artifact: str | None, error: str | None) -> dict`. `get_run` returns `run_id, job_id, rev, target, status, error` plus the validated snapshot path relative to the store root; worker reconstructs its workspace from that record. Artifact ID = opaque ID dari record job, bukan path input. `get` mengembalikan `job_id, rev, status, error, artifacts`; artefak berisi `id, rev, kind, path` dengan path relatif. Domain error memakai `.code` stabil.

- [ ] RED `test_revision_requires_new_approval`: buat rev 1, `claim(...,1,"preview")` → `APPROVAL_REQUIRED`; simpan hasil style frame, setujui `script_style` terhadap ID itu, simpan hasil preview, setujui preview, lalu ubah `t_ms` lewat `revise` → rev 2; `claim(...,2,"final")` tetap `APPROVAL_REQUIRED`. `test_job_reopen`: root sama + instance baru membaca job lama; root kedua mendapat `NOT_FOUND`. `test_note_timestamp`: `8560` + `c007` tersimpan pada rev preview; waktu negatif, cue tak dikenal, path escape ditolak; catatan tidak membatalkan approval.
- [ ] Implementasikan penyimpanan SQLite, ID acak, revisi immutable bernomor per job, input episode/aset snapshot dalam direktori revisi, persetujuan terikat artifact ID/rev. Style frame boleh setelah episode valid, preview setelah `script_style`, final setelah `preview`. `claim` atomik dan satu run aktif per job. `finish` hanya menerima path file relatif dari workspace run; kegagalan tidak menyajikan file parsial. Revisi baru tidak mengubah artefak lama. Validasi payload sebelum dipersist dan tolak perubahan yang menarget revisi basi.
- [ ] GREEN `test_jobs.py` exit 0; uji race dua claim dan pastikan salah satunya `ALREADY_RUNNING`.
- [ ] Commit file T2: `feat(ump): persist jobs and revision approvals`.

### Task 3: Episode dinamis, katalog preset, dan satu clock

**Brief:** Baca registry, demo dan validator yang dilacak, serta temuan Bang Motion/LottieFiles/MG Styles 15. Buat dua gaya reuse dengan gerak dan layout berbeda. **Files:** T3 pada file map.
**Produces:** `load_preset(repo_root: Path, style_id: str) -> dict`, `build_project(repo_root: Path, episode_dir: Path, style_id: str, dst: Path) -> Path`, `frame_at(t_ms: int) -> int`. Preset JSON v1: `id`, `entry`, `assets`, `palette`, `typography`, `layout`, `motion`, `caption_safe_area`, `sources`; entry dan aset relatif di bawah paket preset atau runtime bersama. Registry memetakan ID → manifest, membedakan `spec`/`ready`, dan tidak mempromosikan preset sampai bukti T6.

- [ ] RED `test_episode_drives_two_styles`: render staging dari fixture yang sama lewat kedua preset; timeline input sama, layout/konfigurasi gerak berbeda. Ganti satu beat dengan `KOPI JAM LIMA <b>TEKS</b>`; data staging berubah dan browser menampilkan literal, bukan node `<b>`. `test_shared_cue_clock`: `frame_at(8560) == 257`, `frame_at(9200) == 276`; cue pindah `8560 → 9200`, `cue_ms` visual dan SFX keduanya naik `640`. `test_invalid_preset`: manifest mismatch ID, duplikat, file hilang, dependency eksternal dan symlink keluar ditolak.
- [ ] Implementasikan `frame_at(t_ms) = (t_ms * 30 + 500) // 1000` untuk input nonnegatif. Visual memakai grid frame tersebut, audio tetap pada milidetik cue; selisih maksimal setengah frame. Staging menyuntikkan JSON aman dan teks literal, memetakan beat→aksi, menggunakan lane primary/secondary/ambient dengan easing/stagger; desain kamera/punch-in harus mengikuti subjek minimal pada satu shot. Simpan VO, cutoff end card, safe area dan lookup lokal; musik/SFX opsional, cue tetap satu sumber.
- [ ] GREEN `test_project.py` exit 0. Jalankan `python3 scripts/verify_portability.py` untuk regresi struktur; bila verifier awal hanya membaca HEAD, jalankan lagi sesudah commit. Buktikan HyperFrames check pada staged project, bukan hanya HTML demo lama.
- [ ] Commit file T3: `feat(ump): build data-driven motion presets`.

### Task 4: Loader Hermes, worker nonblokir, dan jalur audio

**Brief:** Baca `jobs` dan `project`; cocokkan [API Hermes](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins) dengan runtime lokal. Tidak ubah Hermes core, tidak `override` tool. **Files:** T4 pada file map.
**Produces:** `register(ctx)` dengan `ctx.register_tool(name, "ump", schema, handler)` dan `ctx.register_skill("motion-pipeline", path)` → `ump:motion-pipeline`; `submit(store: JobStore, job_id: str, rev: int, target: str) -> dict` → `job_id, run_id, status`; `python -m ump_core.worker --root <abs> --run <id>`. Tool schemas: `ump_styles()`, `ump_job(action, payload)` untuk `create/get/revise/annotate/approve`, `ump_render(job_id, rev, target)` untuk `style_frame/preview/final`, `ump_artifact(job_id, artifact_id)` membaca artefak tervalidasi. Semua handler JSON envelope `{ok,data}` atau `{ok:false,error:{code,message}}`. Preview/final MP4 dan style frame PNG lewat pengiriman media asli gateway: skill menyajikan path absolut yang dikembalikan resolver melalui `MEDIA:` pada jawaban; tanpa bot send kedua. Body tool tidak menerima root/path output arbitrary. Skill wajib meminta dan mengenali persetujuan eksplisit untuk job/rev/artifact yang ditampilkan; tool/store memverifikasi binding dan state. Untuk gate chat, uji pesan netral atau permintaan revisi tidak ditafsirkan sebagai persetujuan.

- [ ] RED `test_worker_failure`: worker dengan fixture renderer exit 7 → status `failed`, error terbaca setelah reopen dan tak ada MP4. `test_submit_returns_id`: child terblokir oleh pipe fixture, submit sudah mengembalikan ID, submit ulang ditolak. `test_abandoned_worker`: proses test dihentikan tanpa finish; status rekonsiliasi ke `failed` dan retry bisa berjalan. `test_import_audio`: audio input tanpa timing valid memakai jalur transkrip eksternal yang sudah ada atau gagal dengan alasan jelas; tidak mengarang timing.
- [ ] Implementasikan render lewat child process argv tetap, timeout, logs ringkas dan output atomik, lock OS per run + start deadline untuk membedakan startup dengan worker mati. Status tidak bergantung pada in-memory gateway atau chat session. Pilih root `get_hermes_home()/ump` pada saat panggilan, bukan saat import; input file dari staging yang terverifikasi dan dimiliki workspace, tidak mengakses path user arbitrer. Untuk VO dari Telegram, impor dari cache attachment tepercaya Hermes yang berasal dari chat operator, ke snapshot revisi; gunakan path yang ditetapkan runtime dan validasi containment, tanpa URL atau path absolut dari payload. Probe T4 harus membuktikan kontrak attachment terhadap runtime terpasang; jika attachment gagal, gate VO impor belum lulus. Worker menyiapkan VO + timestamps melalui `caption.py` (Edge atau VO operator dengan timing/transkrip), `subs.py`, serta master `render.py`; gunakan frame style representatif, preview bersuara dan final full target. Batasi `apad` ke durasi video.
- [ ] GREEN `test_worker.py`; di temp Hermes home jalankan `hermes plugins doctor --ci <repo>` dan `python3 scripts/probe_hermes_plugin.py --repo <repo>` via interpreter Hermes. Probe memakai loader asli di A→B→A, install/copy root hanya ke test home, panggil katalog melalui registry dan `create/get` via dispatcher, resolve `ump:motion-pipeline`, buktikan B tidak bisa membaca job A. Plugin manifest/skill terdaftar saja belum PASS.
- [ ] Commit file T4: `feat(ump): register Hermes tools and run async renders`.

### Task 5: Adopsi alat referensi dan aset prosedural

**Brief:** Baca audit Reanimate dan Anidoodle; implementasi lokal sendiri, aset input harus path berizin. Gunakan episode kedua sebagai consumer supaya fitur tidak sebatas utilitas mati. **Files:** T5 pada file map.
**Produces:** `make_contact_sheet(video: Path, times_ms: list[int], out: Path) -> dict` dengan frame aktual dan indeks waktu; `draw_svg(seed: int, palette: tuple[str, ...], subject: str) -> str`. `scripts/reference_frames.py VIDEO --at-ms 0,1000,2000 --out DIR` menulis `sheet.png`, `frames.json` dan menolak waktu di luar video.

- [ ] RED `test_reference_sheet`: video synthetic tiga warna pada detik 0/1/2, output punya tiga tile berurutan, warna tengahnya berbeda, dan `at_ms` tercatat; timestamp out-of-range ditolak. `test_illustration_seed`: `draw_svg(23,("#002233","#FFAA00"),"orbit")` identik pada dua proses; seed 24 berbeda; palette invalid ditolak.
- [ ] Implementasikan seleksi frame FFmpeg dan contact sheet untuk membandingkan revisi; buat generator SVG orisinal berbasis seed dan palet. Episode `ump-second` harus membawa isi/timing berbeda, VO Indonesia dengan word boundaries lokal yang sah, lisensi provenance, dan aset dari generator yang dipakai saat staging melalui field pada episode/cue yang telah diizinkan schema. Bila schema belum mengizinkan, gunakan aset SVG yang dihasilkan dan dilacak di `demo/ump-second/assets/`, commit file itu bersama episode; jangan ubah schema dari lane ini. Sertakan SFX asli atau berlisensi melalui satu cue dengan provenance.
- [ ] GREEN `test_adoptions.py`; validasi `demo/ump-second` dengan `templates/episode/validate.py`; buktikan SVG hasil generator cocok dengan aset yang dipakai episode dan contact sheet membuka tiga frame.
- [ ] Commit file T5: `feat(ump): add frame comparison and seeded artwork`.

### Task 6: Onboarding preset, demo Telegram, dan portabilitas

**Brief:** Integrasikan hasil T1–T5. Pakai preset baru `minimal-explainer` sebagai pelaksanaan prosedur, bukan edit engine. **Files:** T6 pada file map. **Produces:** `scripts/verify_ump.py --checkout PATH --report PATH` (tes noninteraktif, status terpisah dari receipt Telegram); `docs/verification/ump.md` dengan bukti audit→adopsi, render tiga gaya, episode berbeda, dan receipt Telegram.

- [ ] RED `test_acceptance.py` dengan onboarding: salin preset valid pada ID `fixture-paper`, tambah satu entri registry sementara tanpa edit Python, `build_project` harus memilih entry baru; ubah asset ke path yang hilang → ditolak. Uji snapshot preview final terhadap perubahan `c007.t_ms` dari 8560 ke 9200: frame aksi bergeser 19 frame, onset SFX bergeser 640 ms (toleransi satu frame untuk visual, 20 ms untuk pengukuran onset). Uji final ditolak sebelum approval dan setelah revisi; video audio yang kosong/diam tidak lolos hanya karena memiliki stream.
- [ ] GREEN prosedur `styles/README.md`: brief dan signature gerak → manifest + composition → validator → preview fixture → review hook/transisi/caption/end card → render bersuara → rekam bukti → `ready`. Terapkan `minimal-explainer` tanpa perubahan inti setelah T4. Jika test onboarding sudah hijau sebelum T6, simpan bukti bahwa kontrak T3 sudah memenuhinya dan gunakan render preset baru sebagai gate yang sebelumnya belum ada.
- [ ] Render episode baseline dengan `dark-editorial` dan `kinetic-bold`, episode kedua dengan preset yang sama seperti salah satu baseline, ditambah render untuk preset baru. Tes juga VO dari fixture tanpa kredensial dan jalur generasi VO sekali secara live; bila provider gagal, laporkan terpisah, jangan substitusi fixture sebagai bukti online. Review hook, pergantian shot, area subtitle, end card, beat→aksi dan review primary/secondary/ambient. Gunakan contact sheet dari T5 untuk revisi. `verify_ump.py` cek ffprobe dimensi/fps/codecs, durasi wajar terhadap VO/end card, decode FFmpeg dan onset SFX. Catat output aktual dan artefak.
- [ ] Commit kandidat pada branch kerja setelah unit dan visual awal hijau: `feat(ump): prove style onboarding and local rendering`. Dari checkout commit kandidat di path lain, tanpa akses clone upstream, jalankan setup, `hermes plugins doctor --ci <checkout>`, probe Hermes, `python3 scripts/verify_portability.py --render`, dan `.venv/bin/python scripts/verify_ump.py --checkout <checkout> --report <scratch>/ump-report.json`. Gunakan sandbox yang hanya memasang checkout UMP, toolchain, dan direktori output/cache; periksa dari dalam bahwa semua path clone di manifest T1 tidak terlihat. Bila sandbox tidak tersedia, quarantine sementara dengan pemindahan reversible setelah cek inventaris T1, lalu restore begitu smoke test selesai; langkah ini tidak bergantung pada implementasi T7 dan tidak menghapus apa pun. Bukti koneksi toolchain awal boleh butuh instalasi; demo tidak meminta API key atau sumber upstream.
- [ ] Uji Telegram DM dari profil uji yang memang diotorisasi dengan sesi baru: buat job dari brief, pilih gaya, lihat style frame, setujui naskah/frame, minta preview dan catat ID. Buka sesi baru, minta preview berdasarkan ID, buka dan dengarkan, beri revisi bertimestamp; pastikan satu cue bergeser visual+SFX; buat style frame revisi baru, setujui kembali naskah/frame, minta preview baru, lalu setujui preview revisi yang tepat dan minta final. Buka sesi baru lagi dan putar final. Catat versi, commit kandidat, ID job/rev, ukuran/codec, dan konfirmasi operator bahwa dua video bisa dibuka. Jalur ini membutuhkan profil uji/bot yang sah; jangan ganti bot default. Tanpa akses atau konfirmasi playback, gate Telegram tetap `BLOCKED` walaupun probe sukses.
- [ ] Update README, roadmap, changelog, dan bukti. Jalankan `git diff --check` dan review ULTRA. Commit `docs(ump): record verified rollout evidence` hanya setelah status tiap gate dinyatakan apa adanya.

### Task 7: Cleanup checkout riset dengan manifest

**Brief:** Baca manifest T1, bukti T6, dan approval cleanup. Bedakan clone Bang Motion dari skill dan vendor. **Files:** T7 pada file map.
**Produces:** `scripts/cleanup_research.py --manifest PATH` default dry run; `--quarantine DIR` memindah hanya root yang lolos; penghapusan permanen hanya daftar quarantine yang sudah divalidasi operator. Manifest aktual berisi source ID, path kanonik, git remote, full SHA dan ref inventory baseline; tidak ada wildcard.

- [ ] RED `test_cleanup.py`: root bersih dengan commit yang sesuai diterima; `dirty`, `untracked`, `ignored`, symlink, remote/SHA berbeda, branch/ref unik, path bukan root checkout, path skill/vendor, dan path di luar allowlist ditolak. Dry run tidak mengubah fixture. Simulasikan T6 belum hijau: tidak ada penghapusan.
- [ ] Implementasikan pemeriksaan path dan Git; jangan perlakukan checkout yang branch/ref-nya berubah sejak inventaris sebagai salinan disposable. Quarantine lewat rename pada filesystem yang sama, bisa dikembalikan untuk diagnosis. Gunakan manifest minimum daftar checkout aktual; untuk Bang Motion hanya clone upstream `~/.hermes/cache/scratch/bang-motion`.
- [ ] GREEN `test_cleanup.py`; lakukan dry run sebelum uji portabilitas jika perlu isolasi. Sesudah semua gate T6 lulus, verifikasi ulang daftar aktual dan kondisi Git, minta konfirmasi penghapusan permanen atas daftar final, kemudian hapus checkout riset yang diizinkan dan cek path hilang dengan vendor/skill tetap ada. Catat jika ada checkout dipertahankan karena perubahan unik.
- [ ] Commit `scripts/cleanup_research.py tests/test_cleanup.py docs/verification/ump.md`: `chore(ump): verify and record research cleanup`. Push/merge adalah keputusan terpisah.

## Self-review

- Coverage: spec §Tujuan → T2/T4/T6; kontrak revisi/audio/gaya → T2–T6; §Riset → T1 dan enam tes/penggunaan di T2/T3/T5/T6; §Portabilitas → T6, §Cleanup → T7. Implementasi/render/Telegram belum dilakukan pada tahap plan.
- Step scan: setiap task punya input dan exit gate; pemilihan teknik upstream masih hipotesis sampai T1, dengan prosedur eksplisit untuk perubahan plan dan blokir.
- Interfaces: T4 memakai `JobStore` dan `build_project`; T5 memakai schema existing dan staging aset, bukan perubahan paralel; T6 memakai tool dan artefak T4 serta contact sheet T5; T7 membaca manifest T1 dan bukti T6. Adapter tidak menjadi dependency CLI.
- Review Focus: reuse, approval stale, kegagalan worker, isolasi, media Telegram, dan cleanup punya tes/periksa di task yang disebut. Uji Telegram manual dicatat terpisah dari test otomatis.
- Proportion: test cases menetapkan nilai/asersi dan signatures, body produksi tetap di build. Registry perubahan T6 serial sesudah T3; lane paralel tidak share jalur tulis.
