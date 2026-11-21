# UMP — desain plugin Hermes

Status: draf untuk persetujuan operator. Baseline: `main` pada `5eb5aad`, dengan pipeline HyperFrames/FFmpeg, schema episode, skill panduan, dan dua demo gaya. Dokumen v1 lama tetap menjadi rekam jejak baseline.

## Tujuan dan bentuk produk

Ultimate Motion Workflow (UMP) adalah plugin Hermes untuk operator yang bekerja dari chat: brief → pilih atau bandingkan gaya → setujui naskah dan style frame → preview bersuara → revisi berdasarkan timestamp → MP4. Profil Hermes baru harus bisa memuat plugin dari repo UMP. Plugin mengakses job dan artefak melalui tool terstruktur; skill memandu keputusan kreatif dan review. Pipeline tetap bisa dijalankan sendiri untuk render dan pengujian. UMP tidak menerbitkan video ke platform sosial.

## Pilihan arsitektur

- **Direkomendasikan:** plugin native yang membungkus pipeline yang ada. Hermes menangani interaksi dan persetujuan; HyperFrames merender animasi; FFmpeg mencampur audio, membakar caption, dan memvalidasi output. Episode, preset gaya, dan status persetujuan disimpan terpisah. Ini mempertahankan investasi v1 dan mengisolasi API Hermes dari renderer.
- Skill panduan saja mudah dipasang, tetapi tidak memenuhi kebutuhan plugin yang benar-benar ditemukan dan dapat dipanggil Hermes. Penyatuan semua engine upstream akan menambah dependency, masalah lisensi, dan alur render yang sulit diverifikasi. Pertahankan satu renderer sampai ada kebutuhan dan bukti yang mengubah keputusan.

## Kontrak produksi dan gaya

- Job membawa brief, ID gaya, naskah per beat, cue, sumber aset, dan revisi. Dari chat, operator dapat melihat katalog gaya, memulai job dari brief, membuka style frame/preview, mengirim revisi bertimestamp, dan meminta hasil final. Operator menyetujui naskah dan style frame sebelum preview, lalu menyetujui preview tertentu sebelum render final. Persetujuan terikat ke revisi; perubahan yang memengaruhi render (isi, gaya, aset, audio, atau timing) membuat revisi baru dan perlu persetujuan ulang sesuai tahapnya. Edit label dan catatan internal tidak membatalkan persetujuan. Simpan artefak dalam workspace job, dengan path relatif dan tanpa kredensial di Git.
- Dukungan audio: VO yang diberikan operator atau dihasilkan; caption diturunkan dari timestamp audio. Cue yang sama menggerakkan aksi dan SFX; musik/SFX opsional sesuai brief, dan demo audio membuktikan SFX serta VO. Demo tanpa API key memakai VO dan data timing yang sudah tersedia. Waktu kanonik adalah milidetik bulat dari frame nol preview. Catatan revisi mencantumkan ID revisi preview dan ID cue bila relevan. Blueprint menetapkan aturan pembulatan ke frame dan tes pergeseran visual + SFX.
- Gaya yang siap harus bisa menerima data episode, bukan mengulang teks, warna, atau timing demo yang ditulis langsung di HTML. Bentuk gaya mencakup tipografi, palet, layout, perilaku gerak, aturan caption/safe area, renderer entry dan sumber. Registry membedakan `spec` dan `ready`; status `ready` lama adalah bukti render demo, belum bukti kontrak plugin baru.
- Prosedur tambah gaya: tulis brief dan tanda tangan gerak → buat manifest dan komposisi di folder preset → jalankan validator (ID unik, dependensi, sumber/lisensi) → preview dengan episode uji → tinjau hook, transisi, caption, dan akhir → render bersuara serta simpan bukti → daftarkan `ready`. Penambahan satu preset uji harus berhasil tanpa mengubah inti plugin. Render dua gaya dengan episode sama, lalu episode kedua dengan isi berbeda, untuk membuktikan pemilihan gaya dan penggunaan data.
- Baseline target Linux, MP4 vertikal 1080×1920 30 fps H.264/AAC; format baru di luar acceptance awal. Verifikasi pemuatan plugin dan panggilan tool dari profil uji, bukan sekadar adanya file manifest. Telegram DM menjadi jalur chat yang diuji. Saat render dimulai, operator menerima ID job yang tersimpan; status (termasuk alasan gagal) dan artefak bisa diakses kembali dengan ID itu setelah sesi chat berganti. Acceptance: preview dan final MP4 bisa dibuka dari Telegram setelah sesi baru. Detail API ditetapkan terhadap versi Hermes yang benar-benar terpasang dan dokumentasi resmi saat membuat plan.

## Riset dan adopsi enam sumber

Target di bawah adalah hipotesis adopsi dari catatan proyek, bukan hasil audit mendalam. Untuk tiap repo, simpan URL, commit yang diperiksa, lisensi per materi yang diambil, entry point atau file sumber, jejak input → output, teknik yang dipilih/ditolak, perubahan UMP, dan uji penerimaan. Audit contoh dan implementasi yang relevan, tidak berhenti di README. Jika suatu fitur sumber tidak bisa diamati, catat buktinya dan jangan klaim sudah dipelajari atau diadopsi. Clone sementara boleh; pilih sparse/filtered checkout bila aset besar tidak diperlukan. Jangan jalankan skrip upstream sebelum ditinjau. Tidak ada salin kode/aset tanpa hak yang jelas.

- **Bang Motion** (`c1aa65e`): pelajari starter, rig kamera, timeline deterministik, dan proses review. Target: teknik adegan/kamera dan pemeriksaan anti-slideshow yang dipakai pada demo.
- **Reanimate** (`3f7a55a`): telusuri cara memilih dan membandingkan frame. Target: alat lokal yang menghasilkan contact sheet dan catatan timing dari referensi yang berizin atau membandingkan dua revisi UMP.
- **LottieFiles motion-design-skill** (`d77a9fc`): telusuri aturan koreografi dan contoh. Target: template shot dan review gerak utama, pendukung, ambient serta easing/stagger yang dipakai pada demo.
- **MG Styles 15** (`8fab7eb`): telusuri struktur kartu gaya dan pola cue. Target: kartu preset orisinal dan tes bahwa satu edit cue menggeser visual serta SFX. Jangan salin materi yang lisensinya belum jelas.
- **Huashu Art Motion** (`a97c450`): telusuri alur style frame, VO dan shot list. Target: persetujuan style frame dan pemetaan beat VO ke aksi yang dapat diperiksa pada job demo.
- **Anidoodle** (`4ae27d2`): telusuri bagaimana parameter dan seed menghasilkan ilustrasi. Target: satu generator aset orisinal dengan tes input sama menghasilkan aset sama, tanpa membawa engine atau materi yang lisensinya belum jelas.

Sumber berikutnya mengikuti alur yang sama: audit → keputusan adopsi → implementasi orisinal atau salinan berlisensi → bukti → catatan sumber. Setiap sumber awal harus berkontribusi pada perilaku, alat, atau prosedur yang dipakai dan diuji; atribusi saja tidak memenuhi syarat. Jika target awal tidak bisa diverifikasi atau hak penggunaan tidak jelas, pilih kontribusi lain dari sumber yang sama. Jika tetap tidak ada kontribusi yang layak diuji, tandai acceptance terblokir sampai operator menyetujui pengecualian atau sumber pengganti. Jangan bersihkan checkout selagi blokir ini terbuka.

## Portabilitas dan cleanup

Dari checkout UMP baru di path lain dan profil Hermes uji, pasang plugin, pilih gaya, hasilkan preview, revisi berdasarkan timestamp, dan render final dengan suara. Periksa frame, durasi, stream dan decode hasil. Ulangi smoke test tanpa akses ke checkout upstream agar lolos sebelum menghapus clone. Inventaris path dan status Git masing-masing checkout; jangan hapus checkout yang berisi perubahan unik yang belum diamankan. Setelah integrasi dan verifikasi lulus, bersihkan checkout riset lokal, termasuk clone penuh Bang Motion. Skill Bang Motion di profil default adalah instalasi terpisah; scope default cleanup adalah checkout riset, sedangkan salinan vendor dan atribusi di repo UMP dipertahankan. Tidak ada penghapusan pada fase desain.

## Tahapan ULTRA

1. Setujui desain ini.
2. `ultra:plan`: buat blueprint dengan task, owner, dependensi, input/output, perintah verifikasi dan acceptance. Urutan target: audit sumber → kontrak plugin/job → adaptasi sumber dan preset → demo serta uji portabilitas → cleanup. Audit dapat berjalan paralel; kontrak integrasi mengacu pada temuan audit, bukan asumsi dokumen lama. Review blueprint sebelum build.
3. `ultra:build`: kerjakan per tahap di branch dengan ledger dan RED→GREEN untuk perilaku. Catat keputusan adopsi dan pengecualian yang ditemukan.
4. `ultra:verify` dan `ultra:review`: lakukan uji akhir, self-review dan uji profil Hermes yang terisolasi. Bersihkan checkout riset hanya setelah lolos; push/merge mengikuti persetujuan terpisah.
