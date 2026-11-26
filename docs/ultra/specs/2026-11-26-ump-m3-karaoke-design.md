# UMP M3 Web Karaoke Subtitle — Design Proposal

**Status:** approved by operator in chat on 2026-11-26.
**Date:** 2026-11-26
**Scope:** extend the already-approved M3 audio/subtitle path with a web-rendered karaoke mode, fix the reviewed orphaned `Agung.` cue, then complete and verify the existing M3 gates. This does not replace or relax the approved master blueprint.

## Intent

The operator reviewed the real 1× Indonesian VO and confirmed that “Kejaksaan Agung” is spoken correctly; overall video quality is about 90%. The defect is subtitle presentation: the current ASS renderer caps a cue at five words, splitting the six-word phrase `dengan pemberhentian di Halte Kejaksaan Agung.` into a cue ending in `Kejaksaan` and a second cue containing only `Agung.`. The real `speech_timeline.json` places `Agung.` at 12,648–14,049 ms and `Di` at 14,069 ms. Do not change or regenerate the VO to address this.

The desired result is word-by-word karaoke highlighting that follows the real, validated speech timeline, with `Kejaksaan Agung` kept together and no speech duplicated in the scene.

## Goals

- Add a first-class web subtitle renderer with a `karaoke` effect to the custom-composition path, driven by the existing word timeline and the same absolute master clock as frame capture.
- Keep the phrase visible as a readable group and change the active word highlight at its aligned word boundary. Default visual: current word in the existing anchor orange (`#FE7945`), remaining words white with the current outline. This is word-level highlighting, not character-level fill.
- Rebalance groups at a word-count split so a single trailing word is not stranded. For the six-word reviewed phrase, use `dengan pemberhentian di Halte` then `Kejaksaan Agung.` (4+2), subject to actual phone-scale rendering.
- Retain exactly one subtitle owner; preserve current `none` and `ass` behavior.
- Keep an independently editable, revision-bound karaoke subtitle sidecar and include it with its hash in the source bundle/manifest.
- Verify deterministic random/reverse seek, exact word transitions, readable 360×640 output, no duplicate speech, and a real 1× render.

## Non-goals

- Do not alter, synthesize, or send the VO to a provider.
- Do not claim letter-level karaoke timing from word timestamps.
- Do not add kinetic/dialogue effects, diarization, subtitle collision detection, platform-specific safe zones, or a generalized effect/plugin framework.
- Do not change the primary WhisperX alignment or silently convert fallback ASR guesses into approved transcript words.
- Do not modify production, merge, push, publish, or deploy.

## Blueprint constraints

- Master blueprint §9/ADR-006 says ASS/FFmpeg is for lightweight subtitles and a custom web composition is the route for word highlight, kinetic, dialogue, and karaoke; subtitle ownership must be exclusive. This extension follows that decision rather than encoding karaoke tags into ASS.
- Use the validated, hash-bound normalized timeline. The browser recorder already supplies that timeline before any seek; derive highlight state from the requested absolute frame time, never wall-clock playback or CSS animation time.
- Preserve 1080×1920, 30 FPS, offline/network-denied capture, pinned revision/source hashes, no-speech bypass, and the advisory safe-zone policy.
- Keep existing `none`/`ass` payloads and legacy normalization compatible.

## Approaches considered

1. **ASS `\k` karaoke tags.** Lowest code surface and continues the FFmpeg subtitle mux, but puts a complex word-highlight effect into the lightweight ASS path and conflicts with ADR-006's chosen web route. Rejected.
2. **Built-in web karaoke layer in the deterministic custom recorder.** The worker passes pinned word timing to the existing browser capture. A renderer-owned, isolated overlay is updated from each absolute `seek(t)` and is captured as part of the frame; the worker retains a deterministic editable subtitle sidecar. This is the blueprint-aligned choice and applies to the current composition without requiring its author to hand-code each word. Selected.
3. **Hand-authored karaoke inside every composition HTML.** Maximum per-scene art direction, but no reusable UMP mode; it cannot fix the current job without bespoke scene work and would leave M3's subtitle feature absent. Rejected for this deliverable; future art-directed web subtitle components may build on the timing contract.

## Proposed contract and behavior

- Represent the output owner as `subtitle_mode: "none" | "ass" | "web"`. In web mode, require `subtitle_effect: "karaoke"`; no other web effect is implemented or implied. Keep `subtitles_enabled` as a compatibility field and require it to agree with `subtitle_mode != "none"`.
- Web karaoke requires one pinned voice track, approved script, and a valid timeline bound to that voice SHA. Reject `draws_speech_text: true` to prevent a second speech owner, just as the current ASS path does.
- The recorder mounts one renderer-owned overlay in an isolated DOM/shadow root after scene readiness and timeline handoff. It lays out the current deterministic phrase group, highlights the active aligned word in orange, and captures it in the same browser frame as the scene. For a frame time inside a word interval, use that word. If a gap is shorter than one 30-FPS frame, retain the previous active word until the next word's first rendered frame; longer gaps have no active-word highlight. Do not run free-playing timers.
- Generate balanced groups using word timing, punctuation, pause boundaries, and the existing five-word readability cap. Avoid a one-word tail by rebalancing at the split; validate the example at phone scale. Long text wraps within the current tested margins.
- Keep karaoke layout/style and grouped word IDs/ranges in a deterministic editable `subtitles.json` sidecar. The worker burns the web layer into the video during browser capture, not as a second FFmpeg subtitle pass; the source bundle retains the sidecar and speech timeline. The final MP4 still receives the existing audio mux.
- Preserve `none` (no ASR/subtitle artifact) and `ass` (editable ASS sidecar, one FFmpeg burn-in) paths unchanged.

## Files expected to change

- `ump_core/composition.py`: subtitle mode/effect schema, compatibility normalization, single-owner and voice requirements.
- `ump_core/subtitles.py`: deterministic phrase grouping, karaoke subtitle document generation/validation, word-selection behavior; keep ASS renderer intact.
- `ump_core/worker.py`: web-karaoke artifact creation, source manifest/bundle inclusion, one-owner render handoff, output artifact registration, clean failure semantics.
- `scripts/record_custom.mjs`: validate the karaoke handoff, mount/update the overlay from absolute frame time, maintain deterministic seeks and offline capture.
- `tests/test_subtitles.py`, `tests/test_composition.py`, `tests/test_worker.py`, `tests/test_custom_renderer.py`: schema/bypass/ownership, group balancing, start/end/gap/reverse-seek transitions, rendered overlay and artifact/hash integration.
- `docs/ultra/plans/2026-11-26-ump-v2.0-m3-audio.md`, `docs/verification/ump-v2-progress.md`, `skills/motion-pipeline/SKILL.md`, `CHANGELOG.md`: record the new mode, proof, and remaining gate accurately.

## Acceptance

- A regression asserts that the six-word phrase does not produce a singleton `Agung.` cue and that `Kejaksaan Agung.` stays as one group.
- Unit tests assert exact active-word transitions at start/end and during short and long gaps; frame-seek order (forward, reverse, repeat) produces the same overlay for the same frame.
- Browser/sandbox integration proves offline capture, one owner, required timeline, no outside file reads, no partial artifacts on failure, and one editable/hash-verified karaoke sidecar.
- A real worker render of the committed Indonesian VO passes FFprobe and full decode; review screenshots verify before/on/after multiple word changes at 360×640; playback is checked at 1×.
- Run the configured full test suite and project checks. Update M3 evidence with the operator's stated review (VO correct, visual quality ~90%) and the remaining issue(s), then verify the rerender.
- Faster-whisper previously produced `unresolved/transcript_mismatch` on this VO. Preserve that evidence and do not claim successful fallback alignment. Before closing M3, reconcile the blueprint's fallback exit criterion with the already-approved M3 design's explicit allowance to record the real fallback as blocked; if the accepted reading requires a usable successful fallback, M3 remains open until one is demonstrated on real audio.

## Approval gate

Approved by the operator in chat on 2026-11-26. Implementation proceeds under this scope and the approved M3 design. This approval does not authorize production modification, merge, push, publish, or deployment.
