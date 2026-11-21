# Audit referensi UMP

Audit ini memeriksa commit yang dipatok dan menelusuri file sumber. Status di bawah adalah keputusan adopsi; bukti implementasi dan render masuk `docs/verification/ump.md` saat T6.

| Sumber | Yang masuk ke UMP | Bukti penerimaan |
|---|---|---|
| [Bang Motion](bang-motion.md) | Blocking kamera mengikuti subjek; review anti-slideshow dan timeline deterministik | T3: timeline per shot; T6: frame kunci memperlihatkan reveal/gerak subjek, bukan sekadar pergantian kartu |
| [Reanimate](reanimate.md) | Contact sheet lokal dan perbandingan dua revisi pada timestamp identik | T5: fixture tiga warna dan batas durasi; T6: bandingkan revisi demo |
| [LottieFiles](lottiefiles.md) | Koreografi utama, pendukung, ambient dengan easing dan stagger | T3: lane gerak; T6: review frame kunci |
| [MG Styles 15](mg-styles-15.md) | Kartu gaya orisinal dan satu cue untuk aksi dan suara | T3: staging cue bersama; T6: ukur perpindahan frame dan onset SFX |
| [Huashu Art Motion](huashu.md) | Gate style frame dan pemetaan beat VO ke aksi | T2: approval basi ditolak; T6: jalur persetujuan dan revisi nyata |
| [Anidoodle](anidoodle.md) | Ilustrasi SVG orisinal dari seed dan parameter | T5: byte identik lintas proses dan aset terpakai di episode CSW |

Tidak ada engine upstream yang ditambahkan ke runtime. Lisensi root tidak otomatis mencakup aset pihak ketiga. Bukti baca kode bukan bukti bahwa aplikasi upstream sudah dijalankan.
