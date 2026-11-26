# M4 — QA video, revisi terarah, dan pemulihan render

**Status:** usulan desain untuk review operator; belum diimplementasikan. Baseline: `feat/ump-v2.0` @ `c548353`. Rujukan: master blueprint §6 (render dan resource), §10–11 (revisi dan QA), §M4, G4/G8/G9. M5, rilis, push, dan deployment di luar cakupan.

## Hasil yang ingin dilihat operator

Dua MP4 pendek dari **dua revisi berurutan dalam satu job uji baru**: pada klip pertama sayap ngengat diam padahal seharusnya mengepak; pada klip kedua gerak itu kembali. Job M2 asli tidak diubah. Laporan menunjuk rentang waktu yang sama pada keduanya, menjelaskan apa yang diubah dan apa yang tetap identik. Sukses bukan sekadar render selesai, JSON menyebut `motion`, atau skor selisih piksel naik.

## Bangun di atas yang sudah ada

- `ump_core/jobs.py`: `JobStore` sudah menyimpan revisi, artefak, stage, approval, dan menolak dua run aktif untuk job yang sama. Perluas mekanisme ini, jangan buat version control atau antrean baru yang paralel.
- `ump_core/reference.py`: `make_contact_sheet` memilih frame dari presentation timestamp relatif ke frame pertama. Gunakan jam yang sama untuk bukti sebelum/sesudah; sediakan crop ukuran asli untuk objek kecil.
- `ump_core/worker.py`: `_render_custom` dan `_validate_custom_video` menangani jalur komposisi custom. `_record_frames` pada jalur lama menilai PNG lengkap dengan `captured >= expected`; `_frames_intact` mengecek ciri struktur MP4. Kedua cek itu bukan bukti bahwa isi berasal dari revisi yang diminta. Cakup jalur lama dan custom dalam regresi tanpa menganggap mekanisme capturenya sama.
- Pilihan desain: perluas worker + snapshot yang ada. Checker terpisah setelah ekspor lebih ringan, tetapi tidak bisa mencegah approval basi; engine QA/render kedua akan menggandakan state. Tidak ada dependency/model baru yang dibutuhkan untuk memulai.

## 1. Bukti kualitas dan uji sayap diam

Gunakan salinan sumber adegan ngengat M2 (job asal `A7w6XhFV1uvUFeVW`, rev 2), bukan mengubah job yang diterima. Tentukan lebih dulu interval aksi, lokasi sayap relatif ke tubuh, dan waktu sampel dari sumber/brief. Buat revisi negatif yang mengunci sudut sayap; gerak tubuh/latar tetap berjalan. Sediakan juga kontrol berupa hold yang memang diminta dan tidak boleh dilaporkan sebagai sayap gagal. Jangan memilih ambang setelah melihat hasil perbaikan.

- **Teknis:** decode keluaran aktual; cek dimensi, frame count, FPS, durasi/stream, aset, browser error, dan hasil mux sesuai brief.
- **Gerak:** bandingkan crop sayap relatif ke tubuh sebelum/saat/sesudah aksi dengan jam PTS yang sama. Pakai pembanding tubuh/latar supaya translasi seluruh ngengat atau grain tidak dihitung mengepak. Kalibrasikan sinyal pada kontrol baik dan hold terpisah, lalu bekukan aturan sebelum menjalankan pasangan negatif/perbaikan. Jika posisi sayap tidak bisa diisolasi dengan andal, hasil `pending_review`, bukan lulus. Ekspor crop, frame/time range dan klip; angka saja tak cukup.
- **Visual/editorial:** cek keterbacaan, framing dan apakah gerak memenuhi brief. Frame statis valid bila diminta. Laporan membedakan `frame_inspection` dari `playback_1x`, dan menyebut siapa yang melakukan review. Bila hanya gambar tersedia, jangan klaim sudah menonton klip. Bila playback wajib belum dinilai, keputusan kreatif tetap `pending_review`.
- Setiap masalah memuat severity, shot/rentang waktu, intent, gejala, dugaan sebab, bukti, artefak terdampak, tindakan, dan hasil review. Bukti terikat pada job, revisi, run, dan hash artefak. Pisahkan kesalahan wajib dari saran artistik; saran tidak otomatis memblokir.
- Untuk demo M4, hasil yang diharapkan ditetapkan sebelum run: kepakan yang diwajibkan tetapi diam → `fail`; pose diam yang diminta → bukan kegagalan gerak; bukti tidak cukup → `pending_review`. `pending_review` tidak menghabiskan siklus perbaikan hanya untuk memperoleh approval dan tidak bisa menutup penilaian kreatif. Jika tidak ada peninjau dengan kemampuan menilai video pada 1×, serahkan kedua MP4 dan interval aksi ke operator. Status teknis dilaporkan terpisah.

## 2. Perbaikan dan persetujuan

Satu siklus = revisi sumber terarah → render baru → pengukuran dan review ulang pada interval yang sama. Simpan preview awal di luar hitungan; maksimal tiga siklus otomatis per rangkaian, boleh berhenti lebih awal karena berhasil, konflik lock, atau budget habis. Percobaan gagal tetap menghabiskan jatah setelah revisi dimulai; restart tidak meresetnya. Simpan versi terbaik beserta alasan dan issue terbuka jika belum lulus. Perbaikan contoh harus mengubah gerak sayap yang dirender, bukan melonggarkan tes atau menghapus intent.

Hitung dampak sebelum mutasi. Setiap lock mengikat scope dan nilai/hash yang dilindungi; konflik dilaporkan sebelum render. Pembukaan lock membutuhkan keputusan operator yang menunjuk scope tersebut. Bedakan reuse data dari reuse approval: aset yang tidak berubah boleh dipakai lagi dengan hash identik; approval lama tidak boleh menyetujui preview baru. Perubahan font ASS tidak mengganti VO atau alignment; perubahan VO menginvalidasi timing, caption, anchor, mux, dan review terkait. Rollback membuat revisi baru dengan isi terverifikasi dari snapshot terpilih, tanpa menimpa histori atau memberi approval baru secara implisit. Penerimaan M3 berlaku untuk subtitle normal; jangan catat karaoke sebagai disetujui.

## 3. Cache, interupsi, dan batas VM

Key per tahap mencakup semua input yang memengaruhi keluaran tahap itu: source, aset, timing, opsi audio/subtitle bila dikonsumsi, backend/versi, resolusi, FPS, pengaturan dan rentang frame. Cache hit mensyaratkan provenance, checksum, dan kelengkapan urutan yang tepat. Jumlah frame sama tapi source beda, frame hilang/duplikat/korup, atau keluaran setengah ditulis = miss. Pakai publikasi atomik; capture parsial tak tampil sebagai preview siap atau accepted.

Default pemulihan: gunakan kembali unit lengkap yang lolos validasi; bila tidak ada unit aman, render ulang dari awal dengan alasan tercatat. Untuk satu adegan deterministik yang bisa di-seek, bandingkan hasil pemulihan/chunk dengan render penuh pada seluruh rentang dan batas sambung. Uji kesetaraan urutan/PTS dan hasil visual terdekode (serta audio jika ada); bila tak terbukti, nonaktifkan reuse parsial pada jalur itu. Tidak menjanjikan chunk untuk global state atau transisi lintas-scene.

Satu pekerjaan berat sekaligus per VM sebagai default; fase ASR dan render berat tidak bersaing lintas job. Uji dua job bersamaan dan pelepasan slot setelah gagal/interupsi. Probe CPU/RAM efektif (termasuk cgroup), disk bebas, backend browser/encoder, lalu tetapkan batas waktu/disk eksplisit sebelum run. Laporan mencatat wall time, peak memory beserta cakupan pengukurannya, disk sebelum/sesudah, cache hit/miss, alasan retry, dan cleanup. Bila budget kurang, berhenti dengan alasan; jangan turunkan kualitas diam-diam atau hapus artefak yang disetujui.

## Bukti wajib sebelum M4 ditutup

1. MP4 gagal dan MP4 hasil perbaikan, report bertimestamp, crop setara, dan hasil review yang sesuai kemampuan aktual. Kontrol hold tidak ditolak. Tes berhenti pada batas siklus/budget meski masalah masih ada.
2. Manifest per revisi: sumber gerak berubah; aset lain tetap ber-hash identik. Uji terpisah untuk edit font vs VO, konflik lock, approval basi, rollback, dan histori yang tak berubah.
3. Percobaan cache salah dengan jumlah frame tepat ditolak. Interupsi di tengah capture dan sebelum publikasi encode tidak meninggalkan artefak siap palsu. Pemulihan menghasilkan keluaran tervalidasi; buktikan ekuivalensi sebelum mengaktifkan reuse parsial.
4. Hasil regresi jalur custom dan legacy, laporan resource VM aktual dan uji dua job. Gate yang tidak bisa dijalankan dicatat dengan sebab dan bukti; tidak dicentang lulus.

**Urutan setelah desain diterima:** plan dengan acceptance test tiap bagian → self-review plan → implementasi dan pembuktian. Dokumen ini tidak menyatakan demo, tes, atau review M4 sudah dijalankan.
