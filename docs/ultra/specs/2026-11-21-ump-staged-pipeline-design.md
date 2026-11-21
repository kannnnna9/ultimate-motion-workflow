# UMP staged pipeline — design

**Status:** awaiting operator approval
**Tanggal:** 2026-11-21
**Menggantikan:** alur "style dulu, naskah belakangan" di `ump_core/jobs.py` dan `skills/motion-pipeline/SKILL.md`

## Masalah

Alur sekarang membalik urutan kerja yang wajar. Operator mengirim brief, Hermes langsung memilih style, lalu menyusun naskah, cue, dan brand sekaligus dalam satu revisi. Tidak ada tahap riset, tidak ada gate approval naskah, dan tidak ada cara mencegah langkah dilewati.

Akibatnya:

- Naskah bisa lolos tanpa sumber faktual yang tercatat.
- Style dipilih sebelum isi naskah diketahui, padahal style mengatur ritme dan struktur beat.
- Tidak ada titik di mana operator bisa menolak naskah tanpa ikut menolak style frame.
- `ump_job revise` menerima `brand`, `naskah`, `cue` dalam satu patch, jadi urutan tidak bisa dipaksakan.

## Tujuan

Satu alur linear yang tidak bisa dilompati:

```
create → brief(draft) → brief approved → research → naskah(draft) → naskah approved
       → style(approved) → style_frame → approved → preview → approved → final
```

Setiap transisi punya precondition yang diperiksa di server (`ump_core/jobs.py`), bukan hanya di skill. Skill hanya memandu; kalau model melewati langkah, tool menolak dengan kode error yang stabil.

## Non-goals

- Bukan builder naskah otomatis berbasis LLM di dalam kode. Naskah tetap disusun Hermes lalu dikirim sebagai data; yang berubah adalah gerbangnya.
- Bukan riset otomatis. Riset tetap Hermes + `web_search`/`web_extract`, tapi hasilnya wajib dicatat sebagai `sources` dan tidak bisa kosong untuk naskah non-fiksi.
- Tidak mengubah renderer, preset, atau format output.
- Tidak mengubah kontrak approval Telegram (`UMP APPROVE ...`) yang sudah terverifikasi.

## Model state

Job menyimpan `stage` sebagai kolom baru di tabel `jobs`, plus `stage_history`.

| Stage | Artinya | Transisi keluar |
|---|---|---|
| `intake` | job dibuat, brief belum ada | `set_brief` → `brief_draft` |
| `brief_draft` | brief tersusun, belum disetujui | `approve brief` → `brief_approved` |
| `brief_approved` | brief terkunci | `research` → `research_draft` |
| `research_draft` | sumber tercatat, belum disetujui | `approve research` → `research_approved` |
| `research_approved` | sumber terkunci | `naskah` → `naskah_draft` |
| `naskah_draft` | naskah + cue draf | `approve naskah` → `naskah_approved` |
| `naskah_approved` | isi terkunci | `select_style` → `style_approved` |
| `style_approved` | style dipilih | `render style_frame` → `style_frame` |
| `style_frame` | PNG ada | `approve script_style` → `preview` |
| `preview` | MP4 ada | `approve preview` → `final_ready` |
| `final_ready` | — | `render final` → `done` |

`stage` disimpan per revisi. Revisi baru mewarisi stage hanya bila patch tidak menyentuh dokumen yang sudah di-approve; kalau menyentuh, stage turun ke tahap draft yang relevan.

## Dua jalur masuk

Keduanya lewat gate yang sama, bedanya hanya siapa yang mengisi dokumen.

**Generated** — user hanya memberi topik. Hermes menyusun brief, riset, naskah, cue.

**Imported** — user sudah punya brief atau naskah sendiri.

- `import brief` → masuk di `brief_draft` dengan `origin: "imported"`, tetap harus di-approve.
- `import naskah` → masuk di `naskah_draft` dengan `origin: "imported"`. Wajib membawa `sources` (minimal satu `fact_refs` per beat) atau `fiction: true` yang eksplisit. Stage sebelumnya (`intake`..`research_approved`) ditandai `skipped: "imported"` di `stage_history`, bukan dianggap lulus.

Yang dilarang: import yang mendarat langsung di `naskah_approved`, `style_approved`, atau lebih jauh.

## Tool surface

Tool tetap empat; `ump_job` mendapat action baru dan `revise` dibatasi per stage.

`ump_job` action:

| Action | Stage yang diizinkan | Efek |
|---|---|---|
| `create` | — | job baru di `intake` |
| `set_brief` | `intake`, `brief_draft` | tulis brief, stage `brief_draft` |
| `research` | `brief_approved` | tulis `sources`, stage `research_draft` |
| `write_naskah` | `research_approved` | tulis `naskah` + `cue`, stage `naskah_draft` |
| `import_naskah` | `intake`, `brief_draft`, `brief_approved`, `research_approved` | masuk `naskah_draft`, `origin: imported`, catat skip |
| `select_style` | `naskah_approved` | set `style_id`, stage `style_approved` |
| `approve` | per stage | gate approval |
| `revise` | stage terkait | patch dokumen, turunkan stage |
| `annotate` | setelah `style_frame` | catatan bertimestamp |
| `get` | semua | baca state |

`select_style` sebelum `naskah_approved` → `STAGE_ORDER` error.

`ump_render` target tetap tiga (`style_frame`, `preview`, `final`) dengan precondition:

- `style_frame` butuh `style_approved`
- `preview` butuh `style_frame` approved
- `final` butuh `preview` approved

## Approval

`approve` stage baru: `brief`, `research`, `naskah`, `style`, `script_style`, `preview`.

Perintah Telegram yang diterima:

```
UMP APPROVE <job_id> <rev> brief <artifact_id>
UMP APPROVE <job_id> <rev> naskah <artifact_id>
UMP APPROVE <job_id> <rev> style <artifact_id>
```

`brief` dan `naskah` adalah approval dokumen, jadi butuh artifact turunan: sebuah `.json` snapshot yang ditandatangani hash-nya. `style` butuh `style_frame` PNG.

Artifact baru: `brief`, `research`, `naskah`, `style_decision`. Semuanya file JSON di dalam run workspace, ditulis oleh worker supaya melewati validasi containment yang sudah ada.

## Aturan yang diperketat

- `revise` tidak lagi menerima `brand`/`naskah`/`cue` sebelum `research_approved`. Patch itu jadi `EPISODE_REQUIRED`/`STAGE_ORDER`.
- Revisi yang menyentuh `naskah` setelah `naskah_approved` menurunkan stage ke `naskah_draft` dan membatalkan approval `style`, `script_style`, `preview` pada rev lama.
- Revisi yang menyentuh `style_id` setelah `style_approved` menurunkan ke `naskah_approved`.
- `cue` tidak boleh dikirim bersama `naskah` saat `write_naskah`? Boleh — keduanya satu paket.
- `sources` kosong dengan `fiction` bukan `true` → `RESEARCH_REQUIRED`.
- Setiap beat di `naskah` wajib punya `fact_refs` yang menunjuk ke ID `sources`.

## Perubahan file

- `ump_core/jobs.py` — kolom `stage`, `stage_history`, `origin`; `advance`, `stage_of`, validasi transisi.
- `ump_core/tools.py` — action baru, precondition, approval artifact jenis baru.
- `ump_core/worker.py` — target `brief`, `research`, `naskah`, `style_decision`.
- `skills/motion-pipeline/SKILL.md` — alur baru, larangan melompat, jalur import.
- `templates/episode/schema/naskah.schema.json` — `sources` wajib, `fact_refs` wajib per beat.
- `tests/test_jobs.py` — transisi, skip rejection, stale approval invalidation.
- `tests/test_worker.py` — artifact baru, render precondition.
- `tests/test_stages.py` (baru) — tabel kebenaran lengkap semua pasangan stage × action, satu test per pasangan terlarang.

## Migrasi

Job lama tanpa kolom `stage` dianggap `style_approved` bila punya `style_id`, selain itu `intake`. Migrasi dijalankan sekali saat `JobStore._init_db` menemukan kolom belum ada. Tidak ada job produksi; aman.

## Risiko

| Risiko | Mitigasi |
|---|---|
| Model tetap mencoba melompat | tool menolak dengan kode stabil; skill memuat tabel yang sama |
| Terlalu banyak approval, user lelah | brief + research + naskah bisa digabung dalam satu pesan balasan Hermes; tetap tiga perintah |
| Import naskah jadi celah | import hanya mendarat di `naskah_draft`, wajib approve |
| Job lama rusak | migrasi idempoten, default konservatif |

## Gate approval

Dokumen ini butuh persetujuan operator sebelum implementasi. Setelah disetujui, berikutnya `ultra:plan` lalu `ultra:build` dengan TDD.
