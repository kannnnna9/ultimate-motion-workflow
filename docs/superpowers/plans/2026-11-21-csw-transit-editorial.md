# CSW Transit Editorial Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a renderable `csw-transit-editorial` UMP preset and use it to produce a reference-inspired CSW style frame without changing the approved facts or script.

**Architecture:** Keep the existing UMP preset contract. Add one self-contained style package with a manifest and local HTML composition, register it in `styles/registry.json`, then update the current job revision with a new brand style and reference-informed cue parameters. The composition will build a transit hub from CSS/SVG-like geometric layers, animate four original camera beats, and keep all labels supplied by episode data.

**Tech Stack:** JSON manifests, HTML/CSS, GSAP bundled locally, UMP `build_project`, HyperFrames 0.9.72, Python validation.

**Spec:** In-chat approved reference direction: semi-realistic low-poly transit editorial; four narrative views (CSW facade, TransJakarta/Koridor 13, MRT ASEAN connection, wayfinding); blue transit signage, warm concrete, tropical green, yellow accessibility path; original framing and motion.

## Global Constraints

- Preserve the approved CSW naskah, sources, and `fact_refs`.
- Keep output portrait 1080×1920, 30 fps, H.264/AAC for video renders.
- Keep runtime dependencies local; no CDN URLs or network-loaded composition assets.
- Use only renderable registry styles; `spec` styles remain unavailable.
- Keep episode paths relative and render artifacts outside Git.
- Do not copy the supplied reference image into the repository or reproduce its exact panel geometry.
- Treat signage labels as editorial data and keep them factually consistent with the approved research.

## Review Focus

- Registry entry points to a complete local manifest and composition: test with `load_preset` and `build_project`.
- Composition consumes the selected style and episode cue data instead of hardcoded demo copy: inspect generated `staging.json` and `index.html`.
- Four visual beats remain legible in portrait output: render a style frame and review dimensions plus key-frame contact sheet.
- Local runtime stays portable: run external-URL and clean build checks.
- Job revision remains bound to the selected style and does not alter approved naskah/source data: inspect the UMP snapshot before and after revision.

---

### Task 1: Register the CSW transit style

**Files:**
- Create: `styles/csw-transit-editorial/preset.json`
- Create: `styles/csw-transit-editorial/STYLE.md`
- Create: `styles/csw-transit-editorial/ump.html`
- Modify: `styles/registry.json`
- Test: `tests/test_csw_transit_style.py`

**Interfaces:**
- Consumes: existing preset manifest contract from `ump_core.project.load_preset`.
- Produces: registry ID `csw-transit-editorial`, manifest entry `ump.html`, and a renderable style package.

- [ ] **Step 1: Write the failing registry/build test**

Add tests asserting that the registry contains exactly one `csw-transit-editorial` entry with `ready` status, `load_preset` resolves its local entry and assets, and `build_project` creates `index.html`, `meta.json`, and `staging.json` from the CSW episode fixture.

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `pytest -q tests/test_csw_transit_style.py`
Expected: FAIL because the style ID and package do not exist.

- [ ] **Step 3: Implement the manifest, registry entry, and style contract**

Use the existing manifest schema. Declare the blue signage, warm concrete, tropical green, and accessibility yellow palette; a layered transit-hub layout; four scene signatures; and local assets only. Document the reference-derived principles, original-composition rule, motion beats, safe areas, and review checklist in `STYLE.md`.

- [ ] **Step 4: Implement the composition in `ump.html`**

Create a deterministic portrait composition with separated layers for sky, office blocks, CSW massing, elevated platforms, bus, skybridge, trees, people, signage, and route path. Use four cue-driven scene states: location reveal, Koridor 13/TransJakarta, MRT ASEAN connection, and wayfinding. Animate structural reveals, parallax, signage emphasis, and route draw-on with GSAP; use episode-provided beat text and cue timestamps for timing. Keep the visual language original and avoid copying the reference panel layout.

- [ ] **Step 5: Run focused tests and verify they pass**

Run: `pytest -q tests/test_csw_transit_style.py`
Expected: PASS, including local asset and external-URL checks.

- [ ] **Step 6: Commit the preset milestone**

Commit only the new style package, registry entry, and focused test with message `feat: add CSW transit editorial style`.

### Task 2: Bind the approved CSW episode to the new style

**Files:**
- Create: `demo/csw-fakta/brand.json`
- Create: `demo/csw-fakta/naskah.json`
- Create: `demo/csw-fakta/cue.json`
- Test: `tests/test_csw_episode.py`

**Interfaces:**
- Consumes: approved UMP revision 4 naskah/cue data and `csw-transit-editorial` registry ID.
- Produces: a portable episode fixture whose factual text and sources match the approved job snapshot.

- [ ] **Step 1: Write the failing episode test**

Assert that `templates/episode/validate.py demo/csw-fakta` passes, the episode selects `csw-transit-editorial`, every cue beat exists, and every factual beat keeps the approved source IDs.

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `pytest -q tests/test_csw_episode.py`
Expected: FAIL because the episode fixture does not exist.

- [ ] **Step 3: Add the episode data**

Copy the approved revision 4 naskah and cue content exactly, use the new style in `brand.json`, and keep the 1080×1920/30 fps/H.264/AAC contract. Set cue parameters for the four visual scenes without adding unsupported factual claims.

- [ ] **Step 4: Run validation and the focused test**

Run: `python3 templates/episode/validate.py demo/csw-fakta && pytest -q tests/test_csw_episode.py`
Expected: validator exits 0 and all focused tests pass.

- [ ] **Step 5: Commit the episode milestone**

Commit the episode fixture and test with message `feat: add CSW transit editorial episode`.

### Task 3: Render and verify the UMP style frame

**Files:**
- Modify: `docs/verification/ump.md` only after render evidence exists.
- Test/evidence: generated UMP run directory under `~/.hermes/ump/runs/`.

**Interfaces:**
- Consumes: selected style package and CSW episode fixture.
- Produces: verified 1080×1920 PNG style frame, then a preview MP4 when the style frame is approved.

- [ ] **Step 1: Run repo checks**

Run: `python3 scripts/check.py` and `python3 templates/episode/validate.py demo/csw-fakta`.
Expected: prerequisites and episode validation pass.

- [ ] **Step 2: Build a clean project snapshot**

Run the project builder for `demo/csw-fakta` with `csw-transit-editorial`; inspect `staging.json` to confirm the style ID, beat IDs, and cue timing are present.

- [ ] **Step 3: Render the style frame**

Run the UMP `style_frame` target for the current job revision after the style decision revision is created. Verify the run reaches `finished`, the artifact is registered, and `file` reports PNG 1080×1920.

- [ ] **Step 4: Review visual evidence**

Inspect frame 0/first beat, transit hub structure, signage legibility, route path, caption safe area, and source-card treatment. Record any required revision as a timestamped UMP annotation before preview rendering.

- [ ] **Step 5: Record evidence**

Append the actual run ID, artifact ID, dimensions, and unresolved visual notes to `docs/verification/ump.md`. Do not claim preview/final completion until the style frame is explicitly approved.

## Self-review

- Spec coverage: all reference requirements map to Task 1 composition and `STYLE.md`; portability and factual preservation map to Tasks 1–3; render evidence maps to Task 3.
- Step scan: every task has a failing check, implementation action, passing check, and milestone boundary.
- Type consistency: the package uses the existing `load_preset`/`build_project` contract and the episode uses existing brand/naskah/cue schemas.
- Review focus coverage: registry completeness, data binding, visual legibility, portability, and job snapshot integrity each have an explicit check.
- Proportion: the plan adds one preset and one episode fixture only; no renderer refactor or unrelated style changes are included.
