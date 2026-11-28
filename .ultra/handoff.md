# Handoff — 2026-11-28

Goal: UMP v2.0 on isolated local `feat/ump-v2.0`. **M0–M5 complete.** M5 closed as a **technical release candidate, not a v2.0 release**: G10 is only partially satisfied. No merge/push/release/production plugin install/deployment.

## M5 closure evidence
- Four benchmarks produced on the ordinary job path (`ump_job create` → `write_composition` → `ump_render target=preview`), serialised because the render lease rejects concurrency (`RENDER_BUSY`). Final artifacts: opening `j3LHrBggrd9R9pFa` rev4/run `SUM6rvYcsJhppTTD` (600 f, 20.000 s, SHA `a979ed84358b2189…`); explainer `vyZCxi1ewM4Rsp9_` rev4/run `DFrn1HV0KqemFrmM` (900 f, 30.000 s, SHA `e53dcf3489dbc3e7…`, VO muxed); story `oCpeFrl69T0k0NAn` rev3/run `k3qDtSOIDlRs-iKp` (1350 f, 45.000 s, SHA `05da2b109db89399…`); ad `tHNRA3Pu0IEIAU-q` rev4/run `d484qV8QZDF5O03C` (450 f, 15.000 s, SHA `9f1227d552152004…`).
- Operator reviewed the first delivery at 1× and named four defects: sun wedged between hill peaks, story character one-armed, mug handle shifting sides, explainer missing VO. All four fixed and re-rendered on the same path; operator answered **“Ini semua lebih baik”**. Commits `97cedd3` (evidence + motion audit) and `71c8c9a` (round-2 fixes).
- Root causes: sun drawn *after* the hill layers so it surfaced through a valley notch; inner arm drawn with a fixed offset that never cleared the torso; handle side chosen by a binary `cos(rot) < 0` test (measured 621 px teleport at 90°/270°, now 0 jumps across 300 samples since attachment follows `cos(rot)` and depth follows `sin(rot)`).
- VO produced locally with Piper `id_ID-news_tts-medium` after the repo's default `edge-tts` returned HTTP 403. Audio muxed at file level; video payload MD5 identical to the silent preview (`fed130cfa189caa54131ecf161a3624d`).
- Post-fix regression: opt-in suite **478 tests OK, 2 skips** in 100.438 s; `scripts/check.py` READY; worktree clean.

## Next action
**G10 generalization exercise is the remaining benchmark gate.** Blueprint requires one new subject/artwork, one `directed` lock, and one `autonomous` brief; every M5 benchmark was `directed`. Second: register the explainer VO in the JobStore (needs a real attachment import for `import_audio`, or a documented local-provider provenance path) — currently the track exists only in the delivered file. Then, and only with separate operator authorization: merge, push, release, plugin install, deployment.

## Do not repeat
- Two renders at once — the lease will reject the second.
- Judging motion from a still — two multi-second freezes passed visual inspection and only showed up in frame-difference measurement.
- Adding limbs by rotating a fixed offset — verify against the silhouette; the first story arm fix read as a duplicated gesture.
- Drawing anything that belongs behind scenery before that scenery is painted.
- Claiming M5 is a release. It is a candidate with one open gate.

Read first: `docs/verification/ump-v2-m5-benchmarks.md`, `docs/verification/ump-v2-progress.md` (M5 closure), `.ultra/ledger-2026-11-28-ump-v2.0-m5-benchmarks.md`, `docs/ultra/plans/2026-11-25-ump-v2.0-program.md`.
