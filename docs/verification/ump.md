# UMP verification record

Baseline: 2026-11-21 working tree, no commit created for this implementation pass.

## T6 acceptance

- Full Python suite: `32/32` tests passed.
- Registry onboarding: `minimal-explainer` has a manifest, local entry, local GSAP asset, and builds through `build_project`.
- Missing preset asset: rejected with `ASSET_NOT_FOUND`.
- Silent media: rejected even when an AAC audio stream exists.
- Minimal Explainer render: HyperFrames draft render completed; `1080x1920`, `30fps`, H.264, `15.000000s`. This is a video-only composition smoke render; the existing audio mux path is verified separately by the CSW preview.
- CSW preview: H.264/AAC, `540x960`, `30fps`, `19.233333s`, captions burned in and VO muxed.
- Hermes plugin probe: registry/dispatcher isolation, Telegram attachment authorization, style frame, preview, final, and approval binding all passed.

The manual Telegram playback gate is not claimed here because this pass has no new operator playback receipt. The noninteractive probe is the recorded automation evidence.

## T7 research cleanup

Manifest: [`research-manifest.json`](research-manifest.json)

Dry-run command:

```text
.venv/bin/python scripts/cleanup_research.py --manifest docs/verification/research-manifest.json
```

Result:

- `bang-motion`: eligible; clean checkout at pinned SHA/ref.
- `anidoodle`: rejected; staged deletions make the checkout dirty.
- `lottiefiles`: rejected; staged deletions make the checkout dirty.
- `mg-styles-15`: rejected; staged deletions make the checkout dirty.
- `huashu`: rejected; partial checkout has no resolvable `HEAD`.
- `reanimate`: rejected; partial checkout has no resolvable `HEAD`.

No checkout was moved or deleted. Permanent deletion requires a separate explicit operator confirmation after rechecking the final list. The UMP vendor copy and installed Bang Motion skill are outside the cleanup allowlist and remain untouched.
