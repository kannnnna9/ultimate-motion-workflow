# Perawatan UMP

Dokumen ini untuk operator yang mengelola UMP dalam jangka panjang. Tujuannya satu: setiap perubahan bisa dilacak, dan tidak ada versi yang berjalan tanpa ketahuan.

## Dua salinan, satu sumber

| Lokasi | Peran | Ditulis oleh |
|---|---|---|
| `~/ultimate-motion-workflow` | workspace | kamu dan agent |
| `~/.hermes/plugins/ump` | kode yang dijalankan Hermes | `hermes plugins install` |
| `~/.hermes/ump` | data: job, run, fixture | worker |

Ketiganya terpisah. `git status` di folder proyek **tidak** memengaruhi plugin sampai kamu install ulang.

### Aturan yang tidak boleh dilanggar

- **Jangan mengedit `~/.hermes/plugins/ump`.** Folder itu hasil clone; perubahan di sana hilang pada reinstall berikutnya.
- **`origin` di folder plugin hanya untuk fetch.** `pushurl`-nya diarahkan ke `no-push://` agar commit tidak sengaja masuk ke repo asli. Sudah diset; verifikasi dengan `git -C ~/.hermes/plugins/ump remote -v`.

## Kode dan data dipisah

`ump_core/worker.py` mendefinisikan dua root:

- `CODE_ROOT` — lokasi modul: `scripts/`, `styles/`, `templates/`. Ini kode yang benar-benar dieksekusi.
- `DATA_ROOT` — `~/.hermes/ump` (atau `$UMP_DATA_ROOT`): `fixtures/`, `jobs/`, `runs/`.

Fixture disalin ke `DATA_ROOT/fixtures/` **sekali** lewat `seed_fixtures()` saat plugin didaftarkan. Penyalinan tidak menimpa direktori yang sudah ada, jadi fixture yang kamu koreksi bertahan melewati setiap upgrade.

Kenapa begitu: dulu fixture ikut di dalam pohon kode, sehingga setiap reinstall mengembalikannya ke versi bawaan dan setiap koreksi hilang tanpa jejak.

## Prosedur rilis

Lakukan berurutan. Setiap langkah punya bukti yang harus terlihat.

1. **Test hijau.** `.venv/bin/python -m unittest discover -s tests -p 'test_*.py'`
2. **Portabilitas.** `.venv/bin/python scripts/verify_portability.py` → `PORTABILITY: PASS`
3. **Validasi plugin.** `hermes plugins validate .` → `Validation passed`
4. **Rapikan dokumen.** `CHANGELOG.md` (pindahkan `[Unreleased]` ke versi bertanggal), `plugin.yaml`, `README.md`.
5. **Commit dan merge** lewat PR. Verifikasi `git rev-parse HEAD` sama dengan `ls-remote origin refs/heads/main`.
6. **Tag.** `git tag -a vX.Y.Z` lalu push tag.
7. **Install ulang plugin.** `hermes plugins install kannnnna9/ultimate-motion-workflow --enable --force`
8. **Buktikan dari salinan terpasang**, bukan dari repo:

```bash
grep '^version' ~/.hermes/plugins/ump/plugin.yaml
git -C ~/.hermes/plugins/ump log -1 --format='%H'
```

Lalu panggil tool-nya lewat dispatcher terpasang dan pastikan hasilnya mencerminkan perubahan rilis itu. Langkah ini yang menangkap kasus "sudah di-commit tapi plugin masih versi lama".

## Aturan versi

Semantic Versioning. Satu rilis = satu perubahan yang bisa dijelaskan dalam satu kalimat.

- **MAJOR** — kontrak memutus pekerjaan lama: nama tool, bentuk payload, skema `preset.json`, atau urutan stage yang membuat job lama tidak bisa dilanjutkan.
- **MINOR** — preset style baru, stage baru, target render baru, bentuk input baru yang tidak memutus yang lama.
- **PATCH** — perbaikan perilaku tanpa permukaan baru.

Preset style baru **selalu** MINOR: operator bisa memilih sesuatu yang sebelumnya tidak ada.

## Menangkap bug yang tidak tertangkap test

Test hijau bukan bukti render benar. Tiga bug isometric dan tiga celah klasifikasi persetujuan semuanya lolos test lebih dulu.

Untuk setiap style baru atau perubahan komposisi:

1. `hyperframes check out/style-<id>` → 0 error
2. snapshot di beberapa waktu, pastikan tiap frame hash-nya berbeda
3. tinjau contact sheet secara visual
4. periksa elemen yang bisa diam-diam hilang: teks yang tidak muncul, durasi `Infinity`, frame terakhir kosong

Untuk perubahan pada approval, serang klasifikasinya dengan kalimat yang menyamar sebagai persetujuan sebelum menyatakan selesai.

## Kalau ada yang aneh

| Gejala | Periksa |
|---|---|
| Perubahan tidak berpengaruh | Versi plugin vs `main`: `git -C ~/.hermes/plugins/ump log -1` |
| Fixture kembali ke versi lama | Apakah `DATA_ROOT/fixtures/<name>` ada? Kalau tidak, `seed_fixtures()` akan menyalin ulang |
| Worker gagal mid-render | Reinstall saat job berjalan. Pastikan tidak ada run `running` sebelum install ulang |
| Job lama hilang | Store ada di `~/.hermes/ump/jobs.sqlite3`, bukan di repo. Cek path itu |
