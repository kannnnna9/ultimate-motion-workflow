# Ledger — M5 benchmark & generalization

**Date:** 2026-11-28
**Branch:** `feat/ump-v2.0`
**Commits:** `97cedd3` (M5 evidence + motion audit), `71c8c9a` (round-2 fixes from operator review), plus this closure commit.
**Scope:** benchmarks only — no merge, push, release, plugin install, or deployment.

## What was built

Four original compositions, each a normal `directed` job rendered by the existing worker under the existing render lease. No benchmark-specific code path exists in the repo; the compositions are job data.

| Benchmark | Job | Rev | Run | Frames | Duration | Bytes |
|---|---|---|---|---|---|---|
| Opening anime | `j3LHrBggrd9R9pFa` | 4 | `SUM6rvYcsJhppTTD` | 600 | 20.000 s | 8,821,171 |
| Explainer ID + VO | `vyZCxi1ewM4Rsp9_` | 4 | `DFrn1HV0KqemFrmM` | 900 | 30.000 s | 3,891,714 |
| Cerita | `oCpeFrl69T0k0NAn` | 3 | `k3qDtSOIDlRs-iKp` | 1350 | 45.000 s | 8,615,739 |
| Iklan produk | `tHNRA3Pu0IEIAU-q` | 4 | `d484qV8QZDF5O03C` | 450 | 15.000 s | 2,238,079 |

## Decisions

- **Serial rendering.** One global render lease; four simultaneous submissions were rejected `RENDER_BUSY`. The queue is serial by design, not a workaround. Cost if wrong: total wall time equals the sum of all renders (~31 minutes for these four).
- **Motion audit over still inspection.** Freezes are invisible in a still, so every artifact is decoded to 160×284 grayscale and measured frame-to-frame. This is what caught two real holds (explainer 3.2 s, ad 1.5 s) that both looked fine as images.
- **Fix root causes, not symptoms.** The missing crane was a leaked canvas transform, not a crane-drawing bug; the jumping handle was a binary side test, not a bad curve; the missing arm was a fixed rotation offset. Each fix targets the mechanism and was re-verified by measurement.
- **Reject the first asymmetry attempt.** The story's second arm was first drawn raised symmetrically and read as a duplicated gesture. Recorded as a failed iteration and replaced with a bent, resting limb.
- **Mux explainer audio at file level, and say so.** `import_audio` demands an inbound Telegram attachment for provenance; a locally generated VO cannot satisfy it. The alternative — silently leaving the explainer voiceless or pretending the track is registered — would be worse. The video stream is proven unchanged (payload MD5 identical).

## Evidence

- `docs/verification/ump-v2-m5-benchmarks.md` — per-benchmark measurements, the eight fixes with reasons, the honesty notes.
- `docs/verification/ump-v2-progress.md` §M5 closure — gate status at closure.
- Duplicate-job cleanup backup: `~/.hermes/ump/evidence/m5/duplicate-jobs-20261128T063821.json`.

## Open

- **G10 generalization exercise not run.** Blueprint requires one new subject/artwork, one directed lock, one autonomous brief. All four benchmarks were `directed`.
- **Explainer audio not in JobStore.** Needs a real attachment import or a documented local-provider provenance path before production use.
- **Synthetic VO.** Piper `id_ID-news_tts-medium`; acceptable, not talent.
