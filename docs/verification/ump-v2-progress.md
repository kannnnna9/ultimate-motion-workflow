# UMP v2.0 Progress Log

**Date:** 2026-11-25

**Program plan:** `docs/ultra/plans/2026-11-25-ump-v2.0-program.md`

**Authoritative spec:** `docs/ultra/specs/2026-11-25-ump-v2.0-master-blueprint.md` (operator-approved copy; Markdown line-break whitespace normalized; original source SHA-256 `38bcaa1df0a22921c4e3c6d3f95f1f09298b7fabc77982d2df5de8814f068763`)

**Development path:** `~/ultimate-motion-workflow-v2`, branch `feat/ump-v2.0`

**Production:** untouched; no merge, push, release, or deployment.

## Checklist

- [x] M0 — baseline, backups, isolated branch/data, VM profile, and sandbox/render probe.
- [ ] M1 — custom composition creative core; write and commit focused M1 task plan before code.
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
- Open before M1 enables arbitrary compositions: implement and test per-job total storage policy (prefer streaming capture to avoid raw PNG accumulation), verify cgroup limit enforcement, and make the sandbox fail closed. No production composition code has been enabled or deployed.

## Next

M1 starts with the pinned external-source audit and a focused M1 execution plan. The first implementation gate is the secure, storage-bounded render path; then add the custom composition route and prove it with two distinct original short videos. Do not install WhisperX or large models until disk and compatibility measurements justify it.
