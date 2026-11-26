# UMP v2.0 Progress Log

**Date:** 2026-11-25

**Program plan:** `docs/ultra/plans/2026-11-25-ump-v2.0-program.md`

**Authoritative spec:** `docs/ultra/specs/2026-11-25-ump-v2.0-master-blueprint.md` (operator-approved copy; Markdown line-break whitespace normalized; original source SHA-256 `38bcaa1df0a22921c4e3c6d3f95f1f09298b7fabc77982d2df5de8814f068763`)

**Development path:** `~/ultimate-motion-workflow-v2`, branch `feat/ump-v2.0`

**Production:** untouched; no merge, push, release, or deployment.

## Checklist

- [x] M0 — baseline, backups, isolated branch/data, VM profile, and sandbox/render probe.
- [x] M0 follow-up — DATA_ROOT aggregate budget, manual preflight, retention, and cleanup gates defined; automatic aggregate enforcement remains explicitly deferred before M4/M5 batches.
- [x] M1 — custom-composition creative core; T1–T5 verified, including two original outputs, adaptive-skill review, and full browser/sandbox regression gate.
- [ ] M2 — minimal reusable assets and meaningful articulated motion.
- [ ] M3 — optional audio, WhisperX timing, and independent subtitles.
- [ ] M4 — evidence-based creative QA, repair, revision locks, and resource validation.
- [ ] M5 — four benchmarks, generalization exercise, release-candidate gate review.

## M0 — Foundation & safety

### Repository and isolation

- Remote `main` / installed UMP plugin: v1.6.2 at `c98abae30ef729ddc77c00911878d108986d9b0f`.
- The pre-existing local checkout remains on `main` at v1.5.0 commit `e94670f`, 23 commits behind remote; it was not reset or modified.
- Separate development worktree was created from remote v1.6.2 at `~/ultimate-motion-workflow-v2`, branch `feat/ump-v2.0`.
- Development DATA_ROOT is `~/.hermes/ump-v2`, separate from production. It contains 17 fixture files (232,570 bytes).
- Installed-plugin local edits were not overwritten: `styles/flat-vector/STYLE.md` and `styles/flat-vector/ump.html`. Both the complete installed-plugin archive and a binary patch are preserved for later comparison.

### Backup evidence

Private backup directory: `~/.hermes/backups/ump-v2-m0-20261125` (directory mode 700).

- `repo-before-v2.bundle` — all refs; SHA-256 `a097b99e02f3128686a281a4f716685b0334f79d30d2a19b7fe6b1d5a3472055`.
- `ump-data-before-v2.tar.zst` — 2,159 archive members; SHA-256 `67bcb92498184934ce0bc74c3c9f0d131e9ab008392e963d29278af46459ec91`.
- `installed-plugin-before-v2.tar.zst` — 719 archive members; SHA-256 `edacdbc08e971c9af18b63569988e0a10348f17197fcd270a2f968e31dd7b159`.
- `installed-plugin-local-changes.patch` — SHA-256 `ca4db0522632be2ba51e99e746fb574c2aae21939c60240acdf38ec188997444`.
- `config.yaml` — mode 600; SHA-256 matches the current config copy (`c7d087362ae4a7061531dbcf334c844d09c421c065d09ba93c6633377e71346e`). Credentials were not printed or added to the repository.
- Bundle verification and archive member listing succeeded.

### Baseline tests

Command:

```text
PYTHONPATH=. UMP_DATA_ROOT=~/.hermes/ump-v2 python3 -m unittest discover -s tests -p 'test_*.py'
```

Result: exit 0; 171 tests in 5.058s; `OK (skipped=1)`. The only skip is the browser pixel-motion lane because it requires both `UMP_BROWSER=1` and NumPy; NumPy is absent in the active environment. `pytest` is not installed, so the repository's unittest runner is the baseline.

### Re-audit findings

- `ump_core/project.py` builds the legacy card/timeline HTML; `ump_core/worker.py` routes production jobs through that path. No integrated job-owned custom composition path was found.
- Cue schema constrains actions to a fixed enum. It remains a legacy validation surface, not a v2 animation ceiling.
- `styles/dark-editorial/ump.html` and `styles/minimal-explainer/ump.html` have the same SHA-256 (`3095745b02bba7eea1fcdad429cf70aa6a666134f8554f102ed1e7c11064fc76`). This confirms duplication, not that every preset shares the same limitation.
- Registry has 20 styles: 19 ready, 1 spec. README still identifies v1.4.0 and reports 18 styles, 17 ready; reconcile it in a later documentation milestone.
- `docs/verification/research-manifest.json` has empty `sha` values for Huashu and Reanimate. Verify upstream provenance before filling them.
- `scripts/record_frames.mjs` imports Playwright Core from the environment-specific sibling path `/home/agentuser/ump-webgl-spike/node_modules/playwright-core`; the package-lock pins Playwright Core 1.65.1. Make the v2 path portable and pinned.
- Existing v1 recorder/WebGL recovery and test coverage remain compatibility requirements.
- Earlier 2026-11-21/22 staged-pipeline and pipeline-repair plans predate this blueprint and contain conflicting fixed-stage choices. They are retained as history, not v2 authority.

### VM and runtime profile

- 2 vCPUs, affinity 2.
- RAM: 7,806,876 kB total; 4,924,300 kB available. Swap: 10,424,312 kB total.
- Root disk: 50 GB; 46 GB used; 1.4 GB free (98%). Avoid large installs and concurrent long renders until a measured storage budget is in place.
- Python 3.11.16; Node 22.22.2; Chrome 156.0.8078.47; FFmpeg 6.1.1; HyperFrames 0.9.72 cached; Edge TTS 7.2.7 installed.
- WebGL renderer is software SwiftShader (`ANGLE Vulkan 1.3 SwiftShader`); no dedicated GPU was detected by the probe.
- NumPy, faster-whisper, and WhisperX are not installed. Pillow 12.3.0 and jsonschema 4.26.0 are installed.
- Host cgroup files are not exposed at `/sys/fs/cgroup`; user systemd accepted `CPUQuota`, `MemoryMax`, and runtime timeout properties during the render probe, but hard-limit stress behavior remains unverified.

### Sandbox and rendered evidence

- Landlock ABI 4 is available. A path-beneath allowlist denied reading `~/.hermes/config.yaml` and denied writes outside the scratch render workspace; workspace reads/writes succeeded. Parent directories are search-only.
- A transient user systemd service ran the recorder with `IPAddressDeny=any`, `RestrictAddressFamilies=AF_UNIX`, `NoNewPrivileges`, `ProtectSystem`, `MemoryMax=2G`, `CPUQuota=200%`, and `RuntimeMaxSec=120s`. AF_INET socket creation was denied in a separate isolation probe. `ProtectHome` by itself did not hide home in this user service; rely on Landlock for the user-file boundary.
- **Ruling:** use Landlock ABI 4 plus a transient user systemd service on this VM. Bubblewrap fails to establish its UID map/network namespace here; the future launcher must fail closed when required controls are unavailable.
- Actual command path: existing `scripts/record_frames.mjs` captured 30/30 1080×1920 frames at 30 FPS, then FFmpeg encoded H.264 MP4. `ffprobe`: 1080×1920, 30/1, H.264, 1.000 seconds. Decode check found 30 unique frames. Total recorder+encode time: 6.346 seconds; output 117,424 bytes.
- Contact sheet inspected with vision: all three sampled frames contain intact, visibly changing artwork. This is only a technical abstract shader profile, not a creative-quality benchmark.
- Evidence lives under `~/.hermes/cache/scratch/ump-v2-isolation-probe/`; not production data.
- M0/M1 storage update (2026-11-26): live root is 50 GiB total, 36 GiB used, 12 GiB free (76%); UMP DATA_ROOT is 23 MiB; `/dev/shm` is 3.8 GiB with 24 KiB used; RAM available 4.8 GiB. Aggregate DATA_ROOT policy is documented in the program plan: 2 GiB soft / 3 GiB hard manual stop thresholds, 5 GiB root reserve, immutable-input/approved-output retention, exact-run quarantine, and no age-only or wildcard deletes. There is no automated aggregate guard yet; add/test one before M4 concurrency or M5 benchmark batches. M1 already enforces a 512 MiB MP4 ceiling and streams capture through bounded private tmpfs without persistent raw PNGs.

## M1 — Planning and sandbox preflight

- Read the operator-supplied public ChatGPT share. Its visible copy contains the operator’s prompts and processing-time placeholders, but not the assistant replies. It corroborates web-rendered, mobile-first vertical work, open-ended creative genres, the no-Blender constraint, flexible safe-zone/subtitle placement, and deferral of series continuity; the approved blueprint remains the complete design authority.
- Re-read the pinned reference reports and verified local Git objects for Huashu (`a97c45018d46e950d56d61981138e08f4e092909`, ref `refs/research/a97c450`), Reanimate (`3f7a55a2986a84ca2173e86ee9127eefe31d046d`, ref `refs/research/pinned`), Anidoodle (`4ae27d2c281fcfd01bce87ab13dc5ad4df81a795`), LottieFiles (`d77a9fc8cb371cab1ba5420df5bfd232cd14c9bb`), and MG Styles 15 (`8fab7eb49cc60590c518d1a932dfff753697fce7`). Read selected pinned source blobs, not only the existing audit summaries. Huashu/Reanimate license is MIT; Anidoodle software is Apache-2.0 with separate asset licenses; LottieFiles/MG Styles 15/Bang Motion are MIT, with third-party assets still needing per-file rights checks. No upstream demo media or code was added by this audit. The Bang Motion scratch checkout path is absent; its reference files remain vendored in the repository.
- Live resource check: 2 CPUs; about 5.1 GiB available memory; root has 1.4 GiB free (98% used); `/dev/shm` is 3.8 GiB. Keep raw frames off disk and avoid large installs.
- Resource-control probes: `CPUQuota=50%` yielded 1.0 CPU-second over a 2-second run. `MemoryMax` without `MemoryAccounting=yes` did not constrain a 256 MiB allocation; with explicit accounting, a 64 MiB unit was OOM-killed. A tmpfs-only write under `MemoryMax=64M` was OOM-killed after about 59 MiB written, then its file was removed. `TemporaryFileSystem=/tmp:rw,size=16M` failed with systemd status `226/NAMESPACE` (`Operation not supported`), so M1 will use a private `/dev/shm` run directory plus Landlock and the memory cgroup cap. Probe failure flags created here were reset; the user manager remains `degraded` due to existing XDG portal/notification units, but transient units ran successfully.
- M1 task plan: `docs/ultra/plans/2026-11-25-ump-v2.0-m1-creative-core.md`. T1–T5 are implemented and verified. Two 8-second custom compositions render through the isolated browser-to-FFmpeg route; full regression includes browser and sandbox lanes.

## M1 Task 1 — References and adoption

- Reconfirmed all five available Git source pins and their exact local refs: Anidoodle `4ae27d2c281fcfd01bce87ab13dc5ad4df81a795` → `refs/heads/main`; Huashu `a97c45018d46e950d56d61981138e08f4e092909` → `refs/research/a97c450`; LottieFiles `d77a9fc8cb371cab1ba5420df5bfd232cd14c9bb` → `refs/heads/main`; MG Styles 15 `8fab7eb49cc60590c518d1a932dfff753697fce7` → `refs/heads/main`; Reanimate `3f7a55a2986a84ca2173e86ee9127eefe31d046d` → `refs/research/pinned`. Every `cat-file` and `rev-parse` assertion passed.
- Verified license headers from the pinned `LICENSE` blobs: Anidoodle Apache-2.0; Huashu, LottieFiles, MG Styles 15, and Reanimate MIT; Bang Motion’s vendored license MIT. Halftone Dossier’s `STYLE.md` explicitly records CC-BY-NC-4.0; it is excluded from the M1 artwork.
- Corrected the empty Huashu/Reanimate SHA and ref fields. The Bang Motion cache path is absent, but six selected reference/license files remain in `vendor/bang-motion/`; manifest now records `checkout_status=absent` and `vendored_material=vendor/bang-motion` while preserving historical pin/path.
- `python3 -c ...` manifest assertions passed: `manifest pins and materialization verified`. `python3 scripts/cleanup_research.py --manifest docs/verification/research-manifest.json` was dry-run only: `eligible=[]`, Bang rejected as `not_git_root`, and `DRY RUN: no checkout changed`. All 11 relative links in `v2-adoption.md` resolve locally; `git diff --check` passes.
- Created [`v2-adoption.md`](../research/ump/v2-adoption.md) with source file, technique, license boundary, planned UMP consumer, test, and evidence status. No upstream scene/demo media or new code/dependency was copied or executed.
- Plan commits: `ede373d` (M1 plan), `0faf09f` (clarified absent Bang checkout and vendor material).

## M1 Task 2 — Fail-closed sandbox

- Added `ump_core/sandbox.py`: private per-run `/dev/shm/ump-v2/<uid>/<run_id>` workspaces, validated cleanup, one nonblocking global `flock`, systemd-run resource/network restrictions, a clean child environment, and Landlock ABI-4 allowlisting. The workspace is the only writable ordinary path; only `/dev/null` is additionally writable. `/proc` is not granted because the live same-UID probe showed `ProtectProc=ptraceable` alone does not hide same-UID command-line metadata. `/dev` and `/dev/shm` parent paths are execute-only; sibling tmpfs content cannot be read or listed.
- Verified the transient-unit controls in `systemctl show`: `MemoryAccounting=yes`, `MemoryMax=2G`, `MemorySwapMax=0`, `CPUQuota=200%`, `TasksMax=64`, `IPAddressDeny=any`, `RestrictAddressFamilies=AF_UNIX`, `ProtectProc=ptraceable`, and `NoNewPrivileges=yes`.
- Integration command: `UMP_SANDBOX_TESTS=1 PYTHONPATH=. python3 -W error::ResourceWarning -m unittest discover -s tests -p 'test_sandbox.py'`.
- Result: exit 0; 14 tests in 3.473s; `OK`. Coverage includes allowed workspace writes and clean env, denied Hermes config and same-UID `/proc` reads, denied sibling tmpfs read/list/write and AF_INET sockets, binary stdout preservation, busy lock, symlink-safe cleanup, timeout stop with no child left and status 124 (including a mocked nonzero runner exit), and a 64 MiB tmpfs negative control whose systemd result is `oom-kill` after 32–64 MiB.
- Baseline regression: `PYTHONPATH=. UMP_DATA_ROOT=~/.hermes/ump-v2 python3 -W error::ResourceWarning -m unittest discover -s tests -p 'test_*.py'` → exit 0; 185 tests; `OK (skipped=15)`.
- Post-test cleanup verification: no `ump-v2-*` transient user units remain; `/dev/shm/ump-v2/1002` contains only the global `.render.lock`, with no per-run workspace or OOM test file left behind.
- The user-manager feature probe accepted all configured properties. `start_isolated` does not execute the requested child unless its wrapper successfully applies Landlock ABI 4; unavailable controls fail closed. No browser capture is claimed here; Chromium integration remains Task 4 evidence.

## M1 Task 3 — Mode-aware custom composition

- Added `ump_core/composition.py`: exact-shape input, UTF-8 HTML bounded to 1 MiB, pinned 1080×1920/30 FPS metadata, positive duration up to 1,800 frames, and silent/no-subtitle flags. Filesystem paths and unknown value fields are rejected by runtime validation and the tool schema.
- Jobs persist `mode` and JSON `requirements` in the revision payload (64 KiB bound); modes are `autonomous`, `collaborative`, and `directed`. Custom compositions are revision-owned under `revisions/<rev>/composition/index.html` plus `project.json`. Metadata-only revisions copy those files and return to `composition_draft`; generic patches cannot replace composition paths.
- Custom jobs reach preview directly. Collaborative and directed-default jobs require a real preview approval; directed `checkpoints: []` and autonomous jobs proceed to final after successful preview without synthetic approvals. Editing composition creates a new revision, making previous preview/final artifacts stale. Legacy jobs keep their existing routes and operator-message approval evidence.
- Directed custom compositions currently support only the `preview` checkpoint. Other checkpoint names fail with `UNSUPPORTED_CHECKPOINT` rather than being silently ignored. The preview HTML is not executed until Task 4 supplies the isolated recorder.
- `PYTHONPATH=. python3 -m unittest discover -s tests -p 'test_composition*.py'` → exit 0; 12 tests in 0.128s; `OK`.
- `PYTHONPATH=. python3 -m unittest discover -s tests -p 'test_jobs.py'` → exit 0; 26 tests in 1.457s; `OK`.
- Legacy approval verification: `test_approval_sentence.py` → 23 tests; `test_stage_gate.py` → 16 tests; both exit 0. No approval text or evidence was fabricated by the custom route.

## M1 Task 4 — Streamed custom renderer and artifacts

- Added a pinned Playwright Core 1.65.1 recorder and custom worker route. Browser PNG frames stream through a pipe directly to FFmpeg; raw frames are not written to disk. The custom route bypasses legacy style/card staging, ASR/caption preparation, and mux. H.264 output is bounded to 512 MiB and checked for dimensions, frame rate, exact decoded frame count, duration, and absence of an audio stream.
- The Chromium and FFmpeg children run under transient system-manager units that create private PID/mount namespaces, drop to UID 1002 before the Landlock wrapper, deny network, and clean up within a bounded lifecycle. The recorder fails closed without passwordless namespace-launch capability. A dedicated regression test confirms host PID metadata is hidden. `TasksMax=256` and `TimeoutStopSec=3s` are limited to these private-PID units; v1 user-manager limits remain unchanged.
- Security review found that the recorder continued every `file:` URL while Landlock intentionally allowed read-only `/etc` and other runtime roots. The regression was reproduced before the fix: an untrusted full-screen `iframe src="file:///etc/hostname"` completed as `finished`. The recorder now routes at browser-context scope, resolves every file target through `realpath`, allows only files rooted under the staged composition directory, and fails closed on outside/invalid paths. The matching integration test now returns `failed` with no registered artifacts; a companion test proves an explicitly staged SVG asset remains usable. This bounds the browser file-read surface without changing the OS allowlist Chrome needs.
- Review test gaps were closed: the binary pipe test now transfers 4 MiB through a delayed reader and verifies SHA-256; timeout coverage checks the transient unit is no longer active and a short-deadline worker render reaps both units/cleans its workspace; failure-path tests assert tmpfs cleanup and no artifact publication after FFmpeg exits on partial input.
- Targeted regression commands passed with `-W error::ResourceWarning`: `test_custom_renderer.py` — 12 tests in 29.399s, including file-URL denial/asset allowance, slow-consumer pipe backpressure, timeout-unit/workspace cleanup, and FFmpeg early-exit cleanup; `test_sandbox.py` — 14; `test_worker.py` — 10; `test_jobs.py` — 26; `test_composition_tools.py` — 6. The 1-second browser integration case asserts a 30-frame H.264 video, deterministic rewind pixels, seekable player, manifest/source equality, and no legacy render/ASR/caption/mux call. Failed renders publish no artifacts or tmpfs workspaces.
- Two original 8-second custom jobs ran through the actual worker and are registered under `~/.hermes/ump-v2`:

| Work | Job / revision / run | MP4 | Render time | MP4 SHA-256 | Source ZIP SHA-256 | Composition SHA-256 |
|---|---|---|---:|---|---|---|
| Lantern rising through rain into dawn | `rubRByRPiVxgi0ip` / 4 / `jLRUjIFPJlL2dnr0` | `runs/jLRUjIFPJlL2dnr0/preview.mp4`, 5,347,898 bytes | 190.441 s | `c7e779fe5450685ab3f6c541cf26a604fc9ad7c28b514b0fb81502f4094b69b7` | `9a8c396330cf0e915f105f70ef924d68e3152eaa7f48038d3edd8c5a3a541be5` | `3008f1714c93808322b821ee0e41a0da694ddcd8d7539c5a9c8dd65f420b6386` |
| Ink drop unfolding into an orbit | `KoI3RC4ElS0f1pUQ` / 3 / `mpfvgG4KeqFuhcAx` | `runs/mpfvgG4KeqFuhcAx/preview.mp4`, 3,213,570 bytes | 141.539 s | `09c60272c40ddab06e2fe14abd31073da11f2acc0dc64de609e7c3ebdd1cae92` | `e5019a0d678998fb327e0507e88429278dd0d5b207e67f3201cd8fac6db77c5e` | `223f05baf5c899849bf987108a785d399e6d20aabc1a1f15fabea5fe3c93f069` |

- Each run registers exactly three revision/run-bound outputs: `preview.mp4`, `source.zip` (`index.html`, `project.json`, `manifest.json`), and `web-preview.html`. `m1-render-evidence.json` records artifact IDs, sizes, and hashes. A fresh verifier re-read the database entries, opened each registered artifact, recomputed all file and source-manifest SHA-256 values, re-ran FFprobe and full decode, and passed for both runs.
- Both files are H.264 1080×1920, 30/1 FPS, exactly 240 frames / 8.000 seconds, with no audio stream; FFmpeg decode exit 0. Chrome opened each actual web player at a 540×960 phone viewport, played it to the end, and reported duration/currentTime 8 seconds, `ended=true`, 1080×1920 video dimensions, and `error=null`. No transient render units or per-run tmpfs workspaces remain; `/dev/shm` is back to 24 KiB used. Root disk remains at 98% used with 1.3 GiB free, so no additional package/install work is needed for M1.
- Contact sheets at 0, 1, 2, 4, 6, and 7.967 seconds were reviewed. Full-size source frames at 4 and 7.967 seconds were also extracted for both pieces and inspected at a 540×960 phone-scale view:
  - **Lantern:** the bright lantern remains the unmistakable focal point; it climbs from the rainy skyline into the upper half while the blue-purple night shifts to a warmer dawn. Building silhouettes and lights remain readable without text. Motion/pacing is one continuous, measured ascent, and the final frame retains the lantern and warm horizon rather than ending on a blank card. No visible clipping, broken layers, or render defects.
  - **Ink orbit:** the dark organic form holds the center; teal, ochre, and red orbital traces plus nodes/dots provide secondary motion against ample cream negative space. The form and orbit arrangement evolve gradually without a cut, and the visual hierarchy survives the phone-scale view. The exact 4.000 s and 7.967 s comparison of revision 2 (`yhhFPPsabcnZpmGB`) against revision 3 (`mpfvgG4KeqFuhcAx`) shows the earlier sharp radial teeth replaced by a rounded, seeded contour while preserving orbit layout. Native-resolution crops retain one broad drip-like lobe consistent with the ink-drop motif; edges are clean and the remaining lobe is not a capture artifact. No additional render was needed.
- Ruling for Task 4: keep the renderer serialized and retain the 900-second per-run ceiling. The software-browser capture took 141.539–190.441 seconds for these two 8-second, 1080×1920 pieces, so the 45-second benchmark target is not an appropriate timeout for full-resolution reference renders. Cost if wrong: high-resolution renders have longer wait time; lower resolution or frame rate must be a separately approved creative change, not a silent optimization.

## M1 Task 5 — Adaptive creative skill and evidence review

- Rewrote `skills/motion-pipeline/SKILL.md` around adaptive creative roles and prompt routing. It names the nine roles and three job modes; makes custom HTML the default; keeps research, script, VO, subtitles, storyboard, audio, and presets optional; binds approvals to the current revision/stage/artifact; documents source provenance, licensing, sandboxing, no-Blender/ADR-010 boundaries, exact render contract, and the legacy route as compatibility only. Added a separate `Kapan dipakai`, `Pitfalls`, and `Verification` section with fresh artifact/source/codec/playback checks.
- `MotionPipelineSkillContractTests` were first observed failing on missing verification coverage and then pass 3/3. They assert roles/modes/defaults, optional-task bypass, approval/provenance/sandbox rules, selective references, and the evidence gate.
- `docs/research/ump/v2-adoption.md` now distinguishes proven M1 consumers from deferred work: deterministic local seeded geometry, visual-first/no-VO route, timestamp review, original works, and the M2 asset-generator/M3 audio-alignment tasks. No upstream scenes, code, or demo assets were copied.
- `docs/ultra/plans/2026-11-25-ump-v2.0-program.md` records M1 acceptance as complete on this development branch only; M2–M5 remain pending and no v2.0 release/deployment is claimed. `CHANGELOG.md` records the work under `[Unreleased]`, separating the added path and file/network restrictions from a release entry.
- Creative QA: lantern contact sheet shows the lantern rising from the rainy lower skyline through the mid-scene to the upper half as the sky warms; the single measured ascent remains the focal motion and the last frame is a stable visual hold. The 0–1 s ink opening is an intentional quiet setup; the drop appears near 1 s, the bloom/orbit develops by about 2 s, then the constellation continues moving through the final hold. Both maintain a clear focal hierarchy at phone scale and have no visible aliasing, clipping, or compositing defects.
- Targeted contour repair evidence: revision 2 of ink-orbit had prominent sharp radial teeth. The same 4.000 s and 7.967 s frames from revision 3 show those teeth replaced by a smooth irregular outline, preserving orbit layout and negative space. Full-size crops show a broad rounded drip lobe that fits the ink-drop motif; this is intentional art direction, not a remaining render defect. No further repair cycle was needed.
- Full regression command: `PYTHONPATH=. UMP_DATA_ROOT=~/.hermes/ump-v2 UMP_BROWSER=1 UMP_SANDBOX_TESTS=1 python3 -W error::ResourceWarning -m unittest discover -s tests -p 'test_*.py'` → exit 0; 227 tests in 38.582s; `OK (skipped=1)`. The sole skip is the pre-existing `test_halftone_motion_pixels` lane because NumPy is unavailable; browser and sandbox integrations were enabled and passed.
- `python3 scripts/check.py` → exit 0, `HASIL: SIAP`; Node/npm, FFmpeg/FFprobe, Chrome, Python/jsonschema, Edge TTS, HyperFrames 0.9.72, and required repository files all passed. `git diff --check` and staged diff check passed.
- Ruling for Task 5: retain the revision-3 lower ink lobe as a deliberate organic silhouette after exact-time comparison showed the prior radial spikes were removed; further rounding would erase part of the ink-drop character. Cost if wrong: the silhouette may read more droplet-like than expected, but that is consistent with the original brief.

## Next

M1 Tasks 1–5 are complete on `feat/ump-v2.0`: renderer/security implementation `42564ed`; adaptive skill, adoption map, and evidence commit `d2a6325`. The 227-test suite and environment gate passed; the single pre-existing NumPy-dependent pixel test remains skipped. No merge, push, release, or production deployment was performed. The two verified MP4s and their source ZIPs are ready for delivery.
