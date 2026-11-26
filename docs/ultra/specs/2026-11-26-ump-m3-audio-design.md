# UMP M3 — Audio, Speech Timing, and Subtitles

**Status:** Approved by operator on 2026-11-26; implementation plan is in review.
**Date:** 2026-11-26
**Repository / branch:** `~/ultimate-motion-workflow-v2`, `feat/ump-v2.0`
**Authority:** Approved UMP v2.0 master blueprint, ADR-005/006/008; M3 acceptance in the program plan.

## Intent

Add optional, reproducible audio and independently editable subtitles to the job-owned custom-composition path introduced in M1, without making narration or captions mandatory. Preserve the legacy episode path. Prove the path with real Indonesian speech, measured word timing, one speech-driven visual action, and final encoded output.

## What exists

- M1 custom compositions are deliberately silent: `ump_core/composition.py` rejects `audio_enabled=true` and `subtitles_enabled=true`; `ump_core/worker.py::_render_custom` requires a single video stream and invokes FFmpeg with `-an`.
- The legacy worker already prepares VO and ASS, then muxes VO/SFX/beds in `_render` / `_mux`. `scripts/caption.py` supports Edge TTS word boundaries and faster-whisper word timestamps; `scripts/subs.py` generates ASS. These helpers are coupled to episode JSON and do not provide the v2 normalized timing/track contract.
- A committed, real Indonesian VO sample is available at `demo/ump-second/assets/audio/vo.mp3` (17.952 s, mono MP3, 24 kHz; SHA-256 `35de0ac92a01d9b886e76ad0aa584514a5a9eab850225aacde125b6ccf5016db`). Its approved narration includes proper names (`Cakra Selaras Wahana`, `Transjakarta`, `Ragunan`, `Kemang`, `Kejaksaan Agung`), a spoken year, and natural pauses. The paired word-boundary fixture is tracked at `demo/ump-second/fixtures/vo-boundaries.json`.
- Current VM preflight (2026-11-26): 2 CPUs; 5.41 GB RAM available; 12.36 GB root free; UMP DATA_ROOT 26 MB; WhisperX, faster-whisper, PyTorch, and NumPy absent. The Hermes environment has Edge TTS 7.2.7; repo `scripts/requirements.txt` pins 7.3.1. Do not upgrade or install into the active Hermes environment.
- Current official package evidence: WhisperX 3.8.6 is the latest stable PyPI release found; 3.8.7rc1 is explicitly prerelease. The 3.8.6 release (GitHub tag commit `3ccc17b`) added the Indonesian alignment model mapping. PyPI reports BSD-2-Clause; its 16.5 MB wheel SHA-256 is `cb6d4fcd3fb6c42305cb8b222a33a0b78f6b657e9db3b714345fe43dc0a69c1f`, and no release provenance attestation is present. The Indonesian model card reports Apache-2.0; its repository lists 2.52 GB of files, including two 1.26 GB framework checkpoints, so the preflight must download only the selected checkpoint and pin an immutable model revision. Treat these as candidates, not authorization to install: verify exact package/model revisions and full resolver footprint in M3 preflight.

## Goals

1. Keep silent/no-speech jobs valid and bypass ASR and subtitle generation when neither is requested.
2. Give optional audio tracks one normalized timeline with source/provenance, offsets, trims, gain/fades, duration, content hash, and explicit provider/status metadata.
3. Normalize word timing into integer milliseconds with language, audio hash, provider/model revision, status (`native`, `aligned`, `estimated`, or `unresolved`), and optional score. Validate ranges and per-track ordering; permit legitimate multi-speaker overlap.
4. Preserve approved script text. Use WhisperX forced alignment when a verified transcript is available; use faster-whisper word timing as the fallback path where alignment is unavailable or fails. Use native Edge TTS word boundaries only when Edge TTS is explicitly selected and returns valid boundaries. Never silently replace approved words with ASR guesses.
5. Connect at least one measured word onset to an authored visual action on the same absolute master clock. Verify the audible onset against the encoded frame/event within a documented frame tolerance.
6. Produce at most one speech-subtitle owner per output. Start with independent ASS generation (retain the editable `.ass` artifact and, when enabled, burn exactly that one track into the MP4). Provide explicit `none` and `ASS` modes; do not add duplicate caption text to the composition.
7. Keep heavyweight model/package installation isolated and pinned. Benchmark CPU-only Indonesian processing and record real runtime, memory, download/storage footprint, limitations, and fallback behavior.

## Non-goals

- Diarization, automatic lip-sync, beat detection, a subtitle collision engine, platform-specific safe zones, cloud ASR, or mandatory narration.
- Replacing the legacy pipeline or requiring WhisperX for silent, no-speech, or native-boundary cases.
- Sending new script text to an external TTS service during implementation or tests. Use the already-committed audio sample; any future Edge TTS generation remains an explicit provider choice.
- Lowering resolution, frame rate, audio quality, or security isolation to make a model/render fit.
- Merging, pushing, deploying, or modifying the installed production plugin.

## Approaches considered

### A. Extend the M1 custom route with shared audio/timing/subtitle contracts — selected

Add the smallest reusable audio/timing and ASS modules, extend the custom-composition metadata and worker mux path, and leave legacy behavior intact. This puts M3 on UMP's default creative route, supports selective optional features, and allows one normalized contract to be tested without forcing the old episode schema onto authored compositions.

### B. Improve only the legacy episode scripts — rejected

This is the smallest patch, but the M1 default composition path would remain unable to produce audio or subtitles. It would not satisfy the M3 product contract or the later benchmark route.

### C. Build a general plugin/provider framework — rejected

An open-ended plugin system, diarization, and multiple subtitle renderers would add abstraction before one CPU provider and one editable subtitle output have been validated. Keep provider selection small and explicit; add another backend only for a demonstrated requirement.

## Design decisions

- Maintain separate data contracts for audio tracks, speech timing, visual anchors, and subtitle rendering. They share a master clock but remain independently optional.
- Use integer milliseconds for audio/speech; quantize a visual anchor once to the nearest 30 FPS frame. Record the expected tolerance and the measured audio/video timestamps.
- Start M3 subtitle ownership with `none` or `ASS`. Keep the `.ass` sidecar independent even when the final MP4 burns it in. If the composition draws spoken text, disable ASS for that output or reject the conflict; never stack two owners.
- Run WhisperX and model data in an isolated, versioned environment/cache outside production and outside the active Hermes venv. Pin the stable package, all resolved dependencies, and an immutable Indonesian model revision; include hashes and license notices. Disable diarization. If model or dependency storage would breach the manual DATA_ROOT threshold or the 5 GiB root reserve, stop and report the measured blocker before installing.
- Do not treat fixture timestamps as model-validation evidence. Run WhisperX and fallback against the actual committed Indonesian MP3, then manually inspect selected audible onsets (names, spoken year, and pauses) and report disagreements/unresolved words. Existing word boundaries are a reproducibility reference only.
- Make no network TTS call during acceptance. Test the Edge timing adapter with the committed boundary artifact and provider-contract tests; separately verify the pinned Edge TTS package in an isolated test environment if needed. Any test that submits text to Edge TTS requires an explicit operator opt-in.

## Acceptance criteria

- **Optional bypass:** silent/no-subtitle custom render produces no audio or subtitle stream/sidecar and invokes no ASR/TTS/subtitle process. An audio-only non-speech track can mux without ASR or captions. A no-speech path produces no fabricated words.
- **Provider:** WhisperX stable package/model revision and licenses are recorded; CPU-only alignment runs on the real sample with approved transcript retained. The test covers proper names, the spoken year, pauses, at least one unresolved/low-quality case if present, and records performance/resource results. Faster-whisper fallback is exercised on real audio or clearly recorded as blocked; fallback does not disguise WhisperX as passed.
- **Normalized timing:** every timing record includes audio hash/provider/status; timestamps are valid, within decoded duration, and monotonic within a speaker track. Invalid, stale-hash, and out-of-range timings fail before publication.
- **Visual anchor:** at least one composition action is driven by an aligned word anchor; audio onset and encoded visual event are compared at the same timestamp and meet the documented frame tolerance.
- **Subtitles:** ASS is independently editable and tied to the same audio hash/timing revision; long Indonesian phrases remain legible at phone size; muxed MP4 and source do not contain duplicate speech captions. `none` bypass is covered.
- **Integration/regression:** custom VO+alignment+ASS run emits registered MP4, source, timing/evidence, and subtitle artifacts with correct hashes and FFprobe streams; existing M1 silent renders and legacy fixtures remain unchanged. Failure is clean: no partial artifacts are registered.
- **Preflight:** record exact root/DATA_ROOT/model-cache measurements before and after installation/render, venv and model revisions, resolved package lock/hashes, CPU wall time, peak RSS, and output hashes. Enforce the existing serialized-render/resource policy; do not weaken sandbox controls.

## Likely implementation surface

`ump_core/composition.py`, `ump_core/worker.py`, focused new `ump_core/audio.py` and `ump_core/subtitles.py` only if their tested interfaces require them, `scripts/requirements.txt` or an isolated ASR lock/runner, `tests/test_composition.py`, `tests/test_worker.py`, focused audio/subtitle tests, `skills/motion-pipeline/SKILL.md`, and the M3 plan/progress/changelog. Exact file and command sequence belongs in the approved `ultra:plan` plan.

## Source references

- UMP master blueprint §8–9, §14 M3, §15 benchmark B, §16 G6–G7.
- Official WhisperX release history: https://github.com/m-bain/whisperX/releases
- PyPI WhisperX 3.8.6 metadata/checksums: https://pypi.org/project/whisperx/3.8.6/
- Indonesian alignment model card/license: https://huggingface.co/cahya/wav2vec2-large-xlsr-indonesian
