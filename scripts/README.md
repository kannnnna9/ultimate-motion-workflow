# scripts/

Alat baris perintah untuk satu episode. Semua path dihitung dari lokasi script, jadi repo boleh dipindah.

| Script | Guna | Butuh jaringan |
|---|---|---|
| `setup.py` | Buat `.venv`, pasang `requirements.txt`, cek prasyarat, panaskan cache HyperFrames | ya (unduh paket) |
| `check.py` | Verifikasi prasyarat: node, npm, ffmpeg, ffprobe, Chrome, python3, jsonschema, edge-tts, HyperFrames `0.9.72` | ya (npx) |
| `caption.py` | Naskah → VO `mp3` + caption per kata + batas beat | ya untuk `edge` |
| `subs.py` | `captions.json` → subtitle ASS memakai font dan kotak aman dari `brand.json` | tidak |
| `render.py` | Validasi → HyperFrames check/render → bakar caption + audio → verifikasi ffprobe | ya (npx) |
| `verify_portability.py` | Ekspor HEAD ke clone sementara, cek aset/path lokal, validasi dan syntax; `--render` menguji render ulang | ya dengan `--render` |

## Urutan satu episode

```bash
python3 scripts/setup.py                                    # sekali per clone
python3 templates/episode/validate.py demo/explain-9x16     # data episode
python3 scripts/caption.py demo/explain-9x16                # VO + caption
python3 scripts/render.py demo/explain-9x16 --project demo/hyperframes
```

Hasil: `out/<episode>/<episode>.mp4` (di-ignore Git).

## Opsi penting

- `caption.py --force` menimpa VO/caption yang sudah ada. Tanpa flag ini, keluaran lama dipertahankan.
- `render.py --preview` membuat MP4 540×960 CRF 23. Validasi resolusi dilewati karena bukan master.
- `render.py --snapshot 1,4,8` hanya mengambil PNG di detik tersebut — cara termurah memeriksa frame kunci.
- `render.py --keep-frames` menyimpan `frames.mp4` (hasil HyperFrames sebelum caption dan audio).
- `render.py --skip-check` melewati `hyperframes check` bila sudah dijalankan sendiri.

## Hal yang tidak boleh diubah tanpa alasan

- **HyperFrames dipatok `0.9.72`** di `render.py`, `setup.py`, dan `package.json` demo. Naikkan versi di ketiga tempat sekaligus lalu render ulang demo.
- **`HYPERFRAMES_CACHE_DIR`** diarahkan ke `~/.hermes/hyperframes-cache` (mode 700). HyperFrames menolak menjalankan Chrome dari `~/.cache` yang group-writable; `render.py` sudah mengurus ini sendiri.
- **`apad` pada jalur audio.** VO lebih pendek dari komposisi; tanpa `apad` lalu `-shortest`, kartu sumber ikut terpotong.
- **`data-duration` di root komposisi.** Tanpa atribut itu HyperFrames memotong video di cue terakhir, bukan di akhir timeline.

## Kode keluar

`0` sukses, `1` ada masalah (validasi, prasyarat, atau verifikasi ffprobe), `2` pemakaian salah. `render.py` berhenti pada kegagalan pertama dan mencetak perintah yang gagal.
