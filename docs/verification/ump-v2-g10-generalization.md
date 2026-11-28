# G10 — Generalization exercise

**Date:** 2026-11-28 · **Branch:** `feat/ump-v2.0` · **Subject:** Telaga Warna (Dieng), a new subject with a new visual register.

This closes the last open benchmark gate from M5: a *separate* exercise with a new subject, a directed lock, and an autonomous brief. It does **not** close G10 in full — see "What is still not evidenced" at the end. Nothing here was merged, pushed, released, installed, or deployed.

## Subject and source

**Telaga Warna, Dieng** — volcanic crater lake at ~2,000 m, Wonosobo, Central Java. Water shifts colour because dissolved sulphur interacts with sunlight; high hills surround it.

Source, fetched 2026-11-28 from `id.wikipedia.org` (`action=query&prop=extracts&exintro=1&titles=Telaga Warna (Dieng)`): elevation 2,000 m; sulphur-rich water; sunlight produces the colour shift; surrounded by high hills. Those four facts are the entire factual payload of both films. The Bogor article of the same name was also read, and both films say "Dieng" so the two places are never conflated.

## A — Directed, with a real lock

**Job** `8yCHDGnIKJwzwRdn` rev 2 · **Run** `luAvs_W_1RjiSJPB`

| | |
|---|---|
| Route | `create` → `write_composition` → `lock(composition)` → render |
| Frames / duration | 360 / 12.000 s |
| Codec / size | H.264, 1080×1920, 30/1 fps |
| Bytes / SHA-256 | 2,835,900 / `f84bac2fc640da7a68e8f622399c462e4cbc4d142b25e85fd8e56769fa2beb5f` |
| Motion | mean frame difference 0.125; longest sub-threshold hold **0.03 s** |

**The lock was proven by attempting to break it, twice.** After `lock(composition)` at rev 2 (`protected_sha256` `dc7016868f6e98e1…`), a `write_composition` carrying a tampered title returned:

```
LOCK_CONFLICT: composition is locked from revision 2
```

That refusal is the evidence. A lock that is only asserted is not a lock.

**First render of this film failed the motion audit** and was replaced. Version 1 was a nearly static survey sheet: mean frame difference 0.117, **352 of 359 frame pairs below 0.35**, longest hold 2.97 s. That is a slideshow, not motion. Fixed by adding a plotting head that traces the sheet across the full 12 s plus a live coordinate readout — motion that belongs to the drawing's own register rather than faking a camera move. Version 2 audits at 0.03 s. A third pass then moved the readout from y=1900 to y=1848 after frame inspection showed it clipping the bottom edge.

**Visual register:** survey-sheet cross-section — warm paper, ruled grid, rock strata, crater bowl in section, leader-line callouts (`2.000 m dpl`, `SULFUR`, `CAHAYA`, `BUKIT`), scale bar. Deliberately none of the M5 language: no radial title burst, no map beats, no turntable.

## B — Autonomous, no approval gate

**Job** `hvY9EAo2GpGxRq-H` rev 2 · **Run** `U-45448gVg1LIa02`

| | |
|---|---|
| Mode | `autonomous` |
| Stage reached | **`final_ready`**, status `finished` |
| Approvals recorded | **0** (`select count(*) from approvals where job_id=…`) |
| Artifacts | `preview`, `source`, `web_preview` |
| Frames / duration | 240 / 8.000 s, H.264 1080×1920 30/1 |
| Bytes / SHA-256 | 2,002,431 / `dc1f578d99629de6d74d84a2d7c12c5924a9b8445114a5f1a41224d488fd0a18` |
| Motion | mean 0.657; longest hold 0.2 s |

`_preview_approval_required` returns `False` for `autonomous`, and the run demonstrates the consequence end to end: a rendered, finished job at `final_ready` with zero approval rows. Different film from A — the lake surface itself, colour bands layering under a rising sun — so the two are not one film at two lengths.

## Palette distinctness

Quantised dominant colours (6-colour median cut, sampled at 1.5 s and 5.0 s):

- G10 directed: `#f0ebdf` 0.27, `#9d9ea1` 0.26, `#efeade` 0.19 — paper and graphite
- G10 autonomous: `#276265` 0.30, `#152c3d` 0.23, `#316464` 0.19 — dawn lake
- M5 opening/story: `#050c1a`/`#161b2c` family — near-black night
- M5 ad: `#0c0e12`, `#433d3d` — dark studio
- M5 explainer: `#c8a87a`/`#102f3c` — warm sand over deep teal

No G10 dominant colour appears in any M5 run. The register is new, which is the point of the exercise.

## Regression

`UMP_BROWSER=1 UMP_SANDBOX_TESTS=1 PYTHONPATH=. python3 -m unittest discover -s tests` → **Ran 478 tests, OK (skipped=2)**, 100.223 s.

## What is still not evidenced

Three things, stated plainly rather than buried:

1. **The full stage chain was not run end to end.** `research` requires `brief_approved` and `select_style` requires `research_approved`, and approval is authorised only by the operator's inbound chat message — by design, so a model cannot approve its own work. A complete brief → research → style → naskah pass therefore needs two operator approvals inside one job. Both G10 jobs used routes that need none. The driver for the full chain exists (`run_directed.py` history) but has not produced a finished job.
2. **The pipeline has no text-to-composition generator.** Composition HTML is authored and delivered through `write_composition` in both `directed` and `autonomous`. What `autonomous` proves here is the *gate* behaviour — no preview approval required, job reaches `final_ready` on render success — not that the system invented the film from a brief.
3. **G10's "one new artwork" element** was satisfied by authoring new scenes, not by exercising the asset library's image/SVG registration path.

So: G10's directed lock and autonomous-brief requirements are now evidenced with a genuinely new subject. Its full-chain requirement is not.
