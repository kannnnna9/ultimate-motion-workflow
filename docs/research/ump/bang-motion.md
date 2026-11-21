# Bang Motion audit for UMP

## Provenance and scope

- **Source:** [bangtutorial/bang-motion](https://github.com/bangtutorial/bang-motion)
- **Pinned revision:** `c1aa65e921c970d858705fcb4a53a8294d1a4131` (tag `v1.19.0`, `shot sizes — camera moves to the element so videos don't feel static`)
- **Audited checkout:** `/home/agentuser/.hermes/cache/scratch/bang-motion`
- **License:** MIT, copyright 2026 Bang Tutorial. The license permits use, modification, distribution, sublicensing, and sale provided the notice and license are retained; it carries no warranty. Evidence: [`LICENSE`](https://github.com/bangtutorial/bang-motion/blob/c1aa65e921c970d858705fcb4a53a8294d1a4131/LICENSE#L1-L21).
- **Audit boundary:** read-only inspection of the pinned tree and reference text. Upstream code was not executed, copied, committed, or deleted.

The source is a browser-first motion-design skill. Its README states the intended output is one `index.html` that plays like video, with a continuous world, camera movement, persistent subject, and text living in the scene ([README](https://github.com/bangtutorial/bang-motion/blob/c1aa65e921c970d858705fcb4a53a8294d1a4131/README.md#L23-L41)). The UMP target remains a data-driven, vertical HyperFrames pipeline, so the useful contribution is the motion contract and review method, not the upstream HTML as a runtime dependency.

## Starter and exact flow

### Explainer starter: two world modes and a shared camera rig

`assets/starter-explainer.html` defines a fixed 1920×1080 stage and three views: a continuous side-flow world, a collage world, and a map ([source](https://github.com/bangtutorial/bang-motion/blob/c1aa65e921c970d858705fcb4a53a8294d1a4131/assets/starter-explainer.html#L7-L23), [lines 46-69](https://github.com/bangtutorial/bang-motion/blob/c1aa65e921c970d858705fcb4a53a8294d1a4131/assets/starter-explainer.html#L46-L69)).

The exact runtime path is:

1. `fit()` scales the fixed stage to the viewport; it does not redesign layout (`L81-L82`).
2. The side world calculates a speed profile `VK`, integrates it into a lookup table `DA`, and exposes distance `D(t)` (`L84-L88`). Strips and world objects are positioned from `D(t)` and a parallax factor, so position is a function of timeline time (`L90-L98`, `L157-L165`).
3. The persistent hero is rendered from `t`; its vertical motion and wheel rotation use sine functions, not per-frame accumulation (`L100-L105`, `L162-L164`).
4. The map view draws a route and marker from `t`, then moves its camera from a close route point to the wider route (`L110-L120`).
5. The collage view keeps the world larger than the frame and applies camera state `C={x,y,z}` through `applyCam()` (`L122-L128`). `into()` pushes before a cut, `settle()` lands after it, `look()` travels to the next asset, and `home()` returns to the wide framing. Assets enter with directional blur and continue a small deterministic sway (`L129-L133`).
6. The sample timeline whips side→map at 8.6s, reveals the number after the map camera move at 12.6s, whips map→collage at 15s, then runs close-up asset A→asset B→home before the annotation at 19.5s (`L139-L155`). This is a useful camera choreography example; the sample content and its 22-second rundown are not UMP content.
7. `render()` is registered on the GSAP ticker and reads `tl.time()` for all world state (`L157-L166`). `window.OPENER.seek(t)` pauses at an exact time, applies the camera, and renders immediately (`L169-L173`).
8. Playback loops through `onComplete`; `?debug=1` exposes the scrub panel; `?clean=1` suppresses autoplay for capture (`L168-L173`, `L206-L213`). Optional footage is tied to the same timeline through `clipTime`, `syncClips`, and `seekClips`; it does not use an independent autoplay/loop clock (`L174-L204`).

**Adopt:** the stage-to-viewport separation, a camera rig that transforms the whole world, world state derived from one timeline clock, persistent subject/world continuity, explicit `seek(t)`, and footage synchronization as a timeline layer.

**Skip:** the sample landscape, hero, map, labels, colors, fonts, CDN choices, 22-second timing, and placeholder assets. They are starter content and would violate UMP style/preset ownership if treated as a default look.

### Opener starter: architecture and review hooks

`assets/starter-opener.html` explicitly says it is architecture and tools, not a style or scene sequence; the scene structure must come from a product-specific concept and style brief ([source](https://github.com/bangtutorial/bang-motion/blob/c1aa65e921c970d858705fcb4a53a8294d1a4131/assets/starter-opener.html#L7-L36)). Its camera/world structure is `#stage → #world → #gl/.bgfx/#dom`, with the light leak outside the world so it behaves like light in front of the lens ([L60-L69](https://github.com/bangtutorial/bang-motion/blob/c1aa65e921c970d858705fcb4a53a8294d1a4131/assets/starter-opener.html#L60-L69)).

The useful flow is:

- Build stateful Three.js scene state (`S`) and render it from the timeline time (`L205-L219`, `L277-L295`).
- Use CSS initial hidden states and directional blur helpers for text entrances/exits (`L172-L203`, `L330-L357`).
- Keep camera transforms, transitions, and text choreography in the master GSAP timeline (`L297-L328`, `L359-L385`).
- Expose `window.OPENER={tl,DURATION,seek,ready}` and let `snap.mjs` drive exact review frames (`L393-L417`).

**Review finding:** the starter’s dust setup calls `Math.random()` for positions and seeds (`L221-L224`). The architecture rule permits setup-time randomness only when a seed is created once and then retained, while forbidding random values in the render path ([architecture](https://github.com/bangtutorial/bang-motion/blob/c1aa65e921c970d858705fcb4a53a8294d1a4131/references/architecture.md#L107-L115)). This code does retain generated arrays and does not call random during `render()`, so it is not per-frame nondeterminism; however, independent page loads can produce different dust layouts. UMP should replace it with a fixed seed or deterministic hash before claiming repeatable frame output.

## Anti-slideshow rules to carry into review

The source’s anti-PPT document supplies the most useful review contract:

- One continuous world; changing a section or fading full-screen sections is not enough ([anti-ppt](https://github.com/bangtutorial/bang-motion/blob/c1aa65e921c970d858705fcb4a53a8294d1a4131/references/anti-ppt.md#L46-L60)).
- Camera movement must carry the whole world; text-only movement reads as a slide. Shot size should move to the element that is speaking, with wide/medium/close framing motivated by the story ([L34-L43](https://github.com/bangtutorial/bang-motion/blob/c1aa65e921c970d858705fcb4a53a8294d1a4131/references/anti-ppt.md#L34-L43)).
- Avoid one transition preset at every cut. The source recommends overlapping choreography and motivated object wipes/push-throughs, with abrupt zoom/rotation/blur on every cut treated as a warning ([L40-L44](https://github.com/bangtutorial/bang-motion/blob/c1aa65e921c970d858705fcb4a53a8294d1a4131/references/anti-ppt.md#L40-L44), [L79-L87](https://github.com/bangtutorial/bang-motion/blob/c1aa65e921c970d858705fcb4a53a8294d1a4131/references/anti-ppt.md#L79-L87)).
- The fast screenshot heuristics are: could this frame be a corporate slide; is the focal point clear within 0.2s; is the background alive but quiet; and does the camera move the world at transitions ([L100-L110](https://github.com/bangtutorial/bang-motion/blob/c1aa65e921c970d858705fcb4a53a8294d1a4131/references/anti-ppt.md#L100-L110)).
- For explainers, the source requires a persistent subject where appropriate, a small number of text levels, and several evidence-bearing elements accumulating per beat rather than one photo plus one caption ([L48-L60](https://github.com/bangtutorial/bang-motion/blob/c1aa65e921c970d858705fcb4a53a8294d1a4131/references/anti-ppt.md#L48-L60), [explainer](https://github.com/bangtutorial/bang-motion/blob/c1aa65e921c970d858705fcb4a53a8294d1a4131/references/explainer.md#L142-L175)).

These are review criteria, not a mandate to copy the source’s visual skin. UMP should apply them to its own 1080×1920 presets and caption-safe layout.

## Timeline and determinism contract

The source architecture makes GSAP’s master timeline the only clock: `render(t)` receives `t = tl.time()`, and flow is stored as tweened distance rather than `pos += v*dt` ([architecture](https://github.com/bangtutorial/bang-motion/blob/c1aa65e921c970d858705fcb4a53a8294d1a4131/references/architecture.md#L56-L77)). It recommends absolute timeline positions, CSS initial states plus `immediateRender:false`, no `Date.now()`/`performance.now()`/render-loop `Math.random()`, and deterministic formulas for particles and motion ([L79-L115](https://github.com/bangtutorial/bang-motion/blob/c1aa65e921c970d858705fcb4a53a8294d1a4131/references/architecture.md#L79-L115)).

The source also distinguishes review from export: capture 6–20 key seconds through `OPENER.seek(t)`; render every frame only for an explicitly requested MP4 ([L125-L145](https://github.com/bangtutorial/bang-motion/blob/c1aa65e921c970d858705fcb4a53a8294d1a4131/references/architecture.md#L125-L145)). `scripts/snap.mjs` opens `?clean=1`, waits for readiness, calls `seek()`/`seekFrame()`, waits for two animation frames, and saves only the requested screenshots ([source](https://github.com/bangtutorial/bang-motion/blob/c1aa65e921c970d858705fcb4a53a8294d1a4131/scripts/snap.mjs#L1-L52)).

**Adopt in UMP:** one canonical cue clock in integer milliseconds; derive visual frame using the UMP 30fps conversion; let the same cue drive action and SFX; expose a deterministic frame seek for browser snapshots; keep review snapshots separate from full export. UMP’s existing contract is `frame_at(t_ms) = (t_ms * 30 + 500) // 1000`, with audio remaining at cue milliseconds; Bang Motion supplies the seek/review discipline, not the UMP frame-rate policy.

**Do not adopt unchanged:** upstream’s 16:9 default, its GSAP-only export assumptions, or its CDN/module runtime. The UMP deliverable is vertical 1080×1920 with local GSAP and HyperFrames integration.

## Rights and provenance decision

The source code and text are MIT-licensed. Retain the copyright/license notice for any substantial copied portion. The UMP repository already records the source and license in [`THIRD_PARTY_NOTICES.md`](../../THIRD_PARTY_NOTICES.md#L16-L33). This audit recommends **adaptation of behavior and review rules with a local implementation**, rather than copying the starter HTML into the UMP runtime. If any upstream code is later copied, add the exact source file, pinned SHA, and retained notice to the notices table and preserve the MIT license.

The source itself warns that brand look must come from the current product and that the starter is only architecture ([starter-opener](https://github.com/bangtutorial/bang-motion/blob/c1aa65e921c970d858705fcb4a53a8294d1a4131/assets/starter-opener.html#L12-L36)). Therefore UMP should not inherit its fonts, placeholder palette, demo scenes, generated gallery images, or CDN dependencies. No upstream asset rights are needed for this behavioral adaptation because no upstream asset is being copied.

## UMP adoption path

1. **T3 (`ump_core/project.py`, `styles/*`, `styles/runtime/*`):** implement a local shared timeline contract. Stage input remains episode JSON and cue JSON; inject literal text safely; map each beat/cue to primary, secondary, and ambient actions. Add a preset-level camera block supporting `world` transforms, motivated punch-in/pull-back, and at least one shot that follows the speaking subject. Use a fixed seed/hash for any generated ambient geometry. Keep `frame_at(t_ms)` and SFX on the existing single cue clock.
2. **T3 validation:** reject a preset with missing assets, duplicate/mismatched manifest ID, symlink/path escape, or external runtime dependency; keep `sources` and asset provenance in the manifest. Treat upstream’s starter HTML as design evidence only.
3. **T5/T6 review tooling:** use the existing local frame/contact-sheet path to capture hook, first beat, each major shot transition, caption-safe regions, and end card. Add an anti-slideshow review record with: persistent world/subject, camera-caused motion, transition variety, focal clarity, and deterministic seek.
4. **T6 onboarding:** add the new preset through `styles/registry.json` and its manifest/STYLE.md, then render the same fixture through two presets plus a second episode. The new preset should prove that source data changes the composition while the style’s camera/layout behavior remains intentional.
5. **Rights:** keep Bang Motion listed as audited MIT reference. Do not add upstream files to runtime unless the exact copied file and license notice are recorded.

## Concrete acceptance for T3 and T6

### T3 acceptance

- `test_episode_drives_two_styles` passes: the same fixture produces two staging projects with identical cue/timing input but different layout/motion configuration; changing a beat to `KOPI JAM LIMA <b>TEKS</b>` appears as literal text, never an HTML node.
- `test_shared_cue_clock` passes: `frame_at(8560) == 257` and `frame_at(9200) == 276`; moving one cue from 8560ms to 9200ms moves both visual action and SFX by 640ms.
- Preset validation rejects a missing asset, duplicate or mismatched ID, symlink/path escape, and dependency requiring an untracked external runtime.
- The staged browser composition exposes a deterministic seek/frame path; two captures of the same `t_ms` are byte-identical or pixel-identical within the documented renderer tolerance. No render path uses `Date.now()`, `performance.now()`, per-frame `Math.random()`, or accumulated `dt` position.
- At least one staged shot demonstrates camera movement applied to the world and speaking subject together; a frame review confirms it is not text-only motion or a full-screen section fade.

### T6 acceptance

- A valid new preset is onboarded by adding its manifest/registry entry, without editing Python dispatch code; an invalid asset path is rejected.
- The same episode renders with `dark-editorial`, `kinetic-bold`, and the new preset; a second episode renders with an existing preset. The outputs open, decode, and meet the UMP target 1080×1920, 30fps, H.264/AAC contract where the target is claimed.
- A cue move from 8560ms to 9200ms produces a visual shift of 19 frames (±1 frame) and an SFX onset shift of 640ms (±20ms); no independent SFX clock drifts from the visual cue.
- Contact-sheet review records actual hook, shot changes, caption-safe area, beat→action mapping, primary/secondary/ambient motion, and end card. The anti-PPT checks pass: the world remains coherent, camera motion is visible, transitions are motivated and varied, and no hold is a dead slide.
- The preset remains usable from a clean checkout with no access to the upstream clone. The report records actual commands and artifacts; a manifest or registry entry alone is not evidence of readiness.

## Decision

**Adopt:** deterministic single-clock timeline, full-world camera rig, persistent subject/world continuity, motivated shot-size changes, timeline-bound footage, hidden debug scrub, exact seek, key-frame/contact-sheet review, and anti-slideshow heuristics.

**Skip:** upstream demo scenes, palette/fonts/assets, 16:9 assumptions, CDN runtime, sample durations, and unseeded ambient randomness. Build the UMP version locally around its existing vertical HyperFrames contract and keep this report as the provenance and evidence record.
