import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ump_core.project import DomainError, build_project, load_preset  # noqa: E402

NEW_STYLES = (
    "line-art",
    "bauhaus",
    "cyber-hud",
    "flat-vector",
    "collage",
    "morph",
    "paperclip",
    "pixel",
    "cel-boil",
    "liquid",
    "aurora-glass",
    "synthwave",
    "isometric",
)


def registry():
    return json.loads((ROOT / "styles" / "registry.json").read_text(encoding="utf-8"))


def entry(style_id):
    matches = [s for s in registry()["styles"] if s["id"] == style_id]
    return matches[0] if matches else None


class NewStyleContractTests(unittest.TestCase):
    def test_entry_exports_the_timeline_hyperframes_seeks(self):
        """HyperFrames drives frames by seeking window.__timelines.root.

        A composition that only defines its own playback hook renders a frozen
        first frame: every snapshot after 0 s looks identical to 0 s.
        """
        for style_id in NEW_STYLES:
            with self.subTest(style=style_id):
                manifest = load_preset(ROOT, style_id)
                body = Path(manifest["_entry_path"]).read_text(encoding="utf-8")
                self.assertIn("window.__timelines={root:tl}", body)

    def test_styles_without_the_shared_timeline_do_not_emit_addcue(self):
        """`__UMP_TIMELINE__` expands to addCue(...) calls.

        A style that keeps the token but defines no addCue throws
        "addCue is not defined" at load and renders an empty frame.
        """
        for style_id in NEW_STYLES:
            with self.subTest(style=style_id):
                manifest = load_preset(ROOT, style_id)
                body = Path(manifest["_entry_path"]).read_text(encoding="utf-8")
                self.assertNotIn("__UMP_TIMELINE__", body)
                self.assertNotIn("addCue(", body)

    def test_each_new_style_is_registered_ready(self):
        for style_id in NEW_STYLES:
            with self.subTest(style=style_id):
                item = entry(style_id)
                self.assertIsNotNone(item, f"{style_id} missing from registry")
                self.assertEqual(item["status"], "ready")
                self.assertTrue((ROOT / item["reference"]).is_file())

    def test_each_new_style_loads_through_the_preset_loader(self):
        for style_id in NEW_STYLES:
            with self.subTest(style=style_id):
                manifest = load_preset(ROOT, style_id)
                self.assertEqual(manifest["id"], style_id)
                self.assertIn("mg-styles-15", json.dumps(manifest["sources"]))

    def test_entry_html_uses_required_substitution_tokens(self):
        """The three build-supplied tokens are mandatory; __UMP_TIMELINE__ is opt-in.

        __UMP_TIMELINE__ expands to `addCue(...)` lines, which only composes with a
        timeline that defines addCue. Styles with their own choreography do not use it,
        and must instead drive cards from the episode cue list themselves.
        """
        for style_id in NEW_STYLES:
            with self.subTest(style=style_id):
                manifest = load_preset(ROOT, style_id)
                body = Path(manifest["_entry_path"]).read_text(encoding="utf-8")
                for token in ("__UMP_CARDS__", "__UMP_EPISODE__", "__UMP_DURATION__"):
                    self.assertIn(token, body, f"{style_id} missing {token}")
                if "__UMP_TIMELINE__" not in body:
                    self.assertRegex(
                        body,
                        r'#cards\s*\.beat|ep\.cues\b|\bCARDS\b',
                        f"{style_id} neither uses the shared timeline nor reads episode cues",
                    )

    def test_style_contributions_are_not_placeholders(self):
        """A vendored copy with a new palette is the failure this test guards against."""
        for style_id in NEW_STYLES:
            with self.subTest(style=style_id):
                manifest = load_preset(ROOT, style_id)
                body = Path(manifest["_entry_path"]).read_text(encoding="utf-8")
                self.assertNotIn("__STYLE__", body)
                self.assertGreater(len(body), 1200, f"{style_id} entry looks empty")
                self.assertIn("gsap", body)

    def test_declared_signatures_exist_in_the_composition(self):
        """Each style must declare its signature features and actually implement them."""
        for style_id in NEW_STYLES:
            with self.subTest(style=style_id):
                manifest = load_preset(ROOT, style_id)
                body = Path(manifest["_entry_path"]).read_text(encoding="utf-8")
                signatures = manifest["signatures"]
                self.assertIsInstance(signatures, list)
                self.assertGreaterEqual(len(signatures), 3)
                for token in signatures:
                    self.assertIn(token, body, f"{style_id} declares {token} but never uses it")

    def test_new_styles_produce_distinct_palettes_and_motion(self):
        seen = {}
        for style_id in NEW_STYLES:
            manifest = load_preset(ROOT, style_id)
            key = (manifest["palette"]["ground"], manifest["motion"]["camera"])
            self.assertNotIn(key, seen, f"{style_id} duplicates {seen.get(key)}")
            seen[key] = style_id
        for existing in ("dark-editorial", "kinetic-bold", "minimal-explainer"):
            manifest = load_preset(ROOT, existing)
            self.assertNotIn((manifest["palette"]["ground"], manifest["motion"]["camera"]), seen)


class MediumStyleContractTests(unittest.TestCase):
    """The 'medium' styles must stay inside one DOM renderer.

    Each one has a technique the prompt file names as its signature. These
    assertions check the technique is present rather than approximated away.
    """

    def _body(self, style_id):
        manifest = load_preset(ROOT, style_id)
        return Path(manifest["_entry_path"]).read_text(encoding="utf-8")

    def test_step_timed_styles_quantise_time(self):
        """Collage runs on a 12 fps grid, cel-boil on twos, pixel never eases.

        A stepped style that tweens smoothly stops looking hand-made, so each
        must snap the clock to a frame quantum before animating.
        """
        for style_id in ("collage", "cel-boil", "pixel"):
            with self.subTest(style=style_id):
                body = self._body(style_id)
                self.assertTrue(
                    "Math.round(" in body or "Math.floor(" in body,
                    f"{style_id} declares stepped timing but never quantises time",
                )

    def test_pixel_art_uses_nearest_neighbour_and_low_res_canvas(self):
        body = self._body("pixel")
        self.assertIn("pixelated", body)
        self.assertIn("320", body)

    def test_liquid_uses_blur_threshold_metaball_not_webgl(self):
        """Prompt suggests WebGL; a blurred-blob + threshold fake renders identically."""
        body = self._body("liquid")
        self.assertIn("<filter", body)
        self.assertIn("feGaussianBlur", body)
        self.assertNotIn("WebGLRenderer", body)
        self.assertNotIn("getContext('webgl", body)

    def test_aurora_uses_backdrop_blur_cards(self):
        body = self._body("aurora-glass")
        self.assertIn("backdrop-filter", body)
        self.assertIn("blur(", body)

    def test_morph_bridges_through_an_intermediate_shape(self):
        """A→B directly tangles; the prompt requires A→circle→B."""
        body = self._body("morph")
        self.assertIn("bridge", body)

    def test_no_medium_style_needs_a_second_renderer(self):
        """Keeps the whole catalog inside HyperFrames' single-page contract."""
        for style_id in NEW_STYLES:
            with self.subTest(style=style_id):
                manifest = load_preset(ROOT, style_id)
                self.assertNotIn(
                    "renderer",
                    json.dumps(manifest.get("requires", [])).lower(),
                    f"{style_id} declares an external renderer requirement",
                )


class SynthwaveContractTests(unittest.TestCase):
    """The style must be built from DOM primitives, not a second renderer.

    Upstream renders this film with WebGL. That path is unavailable to the
    seek-by-frame capture here, so each signature technique has to survive as
    a CSS/SVG equivalent. These tests fail if an approximation quietly drops
    the thing that makes the style recognisable.
    """

    def _body(self):
        manifest = load_preset(ROOT, "synthwave")
        return Path(manifest["_entry_path"]).read_text(encoding="utf-8")

    def test_grounding_grid_is_a_real_perspective_plane(self):
        """A flat grid reads as a wallpaper; the horizon needs convergence."""
        body = self._body()
        self.assertIn("perspective:", body)
        self.assertIn("rotateX(", body)

    def test_sun_is_banded_not_a_smooth_gradient(self):
        """The cut-out bands are the whole reason the sun reads as synthwave."""
        body = self._body()
        self.assertRegex(body, r"repeating-linear-gradient")
        self.assertRegex(body, r"mask-image|-webkit-mask-image")

    def test_title_uses_chrome_fill_and_double_glow(self):
        """Tight 4px highlight plus a wide ~30px bloom, upstream's recipe."""
        body = self._body()
        self.assertIn("background-clip:text", body)
        self.assertRegex(body, r"drop-shadow\(")
        self.assertIn("30px", body)

    def test_vhs_pass_is_static_so_seek_stays_deterministic(self):
        """Upstream adds wave jitter and random glitch.

        Those are generated from a real-time clock, which would make the same
        timestamp render differently on every capture. Scanlines are kept;
        frame-aligned offsets are acceptable because they are a function of t.
        """
        body = self._body()
        self.assertIn("repeating-linear-gradient", body)
        self.assertNotIn("Math.random(", body)
        self.assertNotIn("Date.now(", body)
        self.assertNotIn("performance.now(", body)

    def test_grid_parallax_is_transform_only(self):
        """Animating background-position snaps to integer pixels and stutters."""
        body = self._body()
        self.assertNotRegex(body, r"to\([^)]*\{\s*backgroundPosition")
        self.assertIn("y:GRID_TILE", body.replace(" ", ""))

    def test_tilt_and_scroll_live_on_separate_elements(self):
        """One element cannot hold both rotateX and the scroll translate.

        When a single node owns the perspective tilt and the scroll, the scroll
        transform replaces the tilt and the floor renders flat with no motion.
        """
        body = self._body()
        self.assertIn('id="tilt"', body)
        tilt_rule = re.search(r"#tilt\{([^}]*)\}", body)
        self.assertIsNotNone(tilt_rule)
        self.assertIn("rotateX(74deg)", tilt_rule.group(1))
        grid_rule = re.search(r"#grid\{([^}]*)\}", body)
        self.assertIsNotNone(grid_rule)
        self.assertNotIn("rotateX", grid_rule.group(1))
        self.assertIn("background-size:140px 140px", body)


    def test_every_css_variable_is_declared(self):
        """An undeclared --var silently nulls the whole declaration.

        The sun gradient referenced --sun_top/--sun_mid/--sun_bottom, which were
        only ever listed in preset.json. Chrome dropped the gradient, so the
        frame rendered without its most recognisable element and no check
        complained: a transparent box passes layout and contrast alike.
        """
        body = self._body()
        declared = set(re.findall(r"(--[a-z0-9_-]+)\s*:", body))
        used = set(re.findall(r"var\((--[a-z0-9_-]+)\)", body))
        self.assertEqual(used - declared, set(), "CSS variables used but never declared")

    def test_gradient_carrier_is_not_animated_directly(self):
        """GSAP rewrites `background` when it tweens an element that carries a gradient."""
        body = self._body()
        sun_rule = re.search(r"#sun\{([^}]*)\}", body)
        self.assertIsNotNone(sun_rule)
        self.assertIn("background:linear-gradient", sun_rule.group(1))
        self.assertNotRegex(body, r"fromTo\('#sun',|to\('#sun',")


class IsometricContractTests(unittest.TestCase):
    """2.5D isometric has an exact projection; approximate it and it stops reading as iso.

    Upstream names the SSR recipe (scale 86.6% -> shear +/-30 deg -> rotate -30 deg).
    The style must project with real isometric math, not a guessed rotation, and it
    must keep the three signatures that make the style recognisable: no vanishing
    point, buildings growing from the ground plane, and a parallel camera slide.
    """

    def _body(self):
        manifest = load_preset(ROOT, "isometric")
        return Path(manifest["_entry_path"]).read_text(encoding="utf-8")

    def test_projection_uses_true_isometric_ratios(self):
        """A true iso projection needs cos(30)=0.866 and a 2:1 tile slope.

        Guessed coefficients produce a dimetric look that reads as broken 3D,
        so the constants have to be present and derived, not typed as decoration.
        """
        body = self._body()
        self.assertIn("0.866", body, "missing the 86.6% vertical scale of a true iso projection")
        self.assertIn("Math.cos(30", body.replace(" ", "").replace("Math.cos(30*Math.PI/180)", "Math.cos(30"))
        self.assertIn("Math.sin(30", body.replace(" ", "").replace("Math.sin(30*Math.PI/180)", "Math.sin(30"))

    def test_there_is_no_perspective_or_vanishing_point(self):
        """Iso means parallel edges stay parallel; any perspective breaks the style."""
        body = self._body()
        self.assertNotIn("perspective:", body)
        self.assertNotIn("rotateX(74deg)", body)
        self.assertNotIn("PerspectiveCamera", body)

    def test_three_face_shading_is_distinct(self):
        """Top, left and right faces must differ in lightness or the solid reads flat."""
        body = self._body()
        for var in ("--face_top", "--face_left", "--face_right"):
            self.assertIn(var + ":", body, f"{var} never declared")
        values = re.findall(r"--face_(?:top|left|right)\s*:\s*(#[0-9A-Fa-f]{6})", body)
        self.assertEqual(len(set(values)), 3, f"the three faces are not distinguishable: {values}")

    def test_buildings_grow_from_the_ground_plane(self):
        """Upstream: base lands, walls scaleY from 0, roof caps 2-3 frames later."""
        body = self._body()
        self.assertIn("scaleY", body)
        self.assertRegex(body, r"transformOrigin|transform-origin")
        self.assertIn("GROW", body)

    def test_camera_slides_in_parallel_without_rotation(self):
        """The signal move is a whole-stage translation; rotating it breaks iso."""
        body = self._body()
        self.assertIn("PARALLAX", body)
        self.assertIn("1:0.8:0.6", body.replace(" ", ""))

    def test_depth_order_is_screen_y(self):
        """In iso, paint order is the screen y of the object; not CSS z-index alone."""
        body = self._body()
        self.assertIn("depth", body.lower())
        self.assertRegex(body, r"zIndex|z-index")

    def test_no_time_or_random_sources_break_seek_determinism(self):
        body = self._body()
        for banned in ("Math.random(", "Date.now(", "performance.now("):
            self.assertNotIn(banned, body)

    def test_timeline_duration_is_pinned_to_the_episode(self):
        """A child tween with `repeat: -1` makes tl.duration() Infinity.

        The seek driver then asks for frames far past the end and every capture
        comes back as the frozen last state. An empty `tl.set({}, {}, D)` is also
        a zero-length callback and never extends the timeline. The duration has
        to be pinned explicitly.
        """
        body = self._body()
        self.assertNotIn("repeat: -1", body)
        self.assertIn("tl.duration(DURATION)", body)

    def test_no_child_timeline_is_paused(self):
        """A paused child timeline never advances when the parent is seeked.

        The first card build nested `gsap.timeline({paused: true})` instances;
        their text sets never ran, so the composition rendered with no episode
        copy at all while every contract test still passed, because the markup
        was present in the authored HTML. Only the root timeline may be paused.
        """
        body = self._body()
        self.assertEqual(body.count("paused"), 1, "exactly one paused timeline: the root")
        root = re.search(r"const tl = gsap\.timeline\(([^)]*)\)", body)
        self.assertIsNotNone(root, "root timeline not found")
        self.assertIn("paused", root.group(1), "the root timeline must be paused")
        for args in re.findall(r"gsap\.timeline\(([^)]*)\)", body)[1:]:
            self.assertNotIn("paused", args, f"a child timeline is paused: {args}")

    def test_final_card_holds_to_the_end(self):
        """A composed end frame is part of the style contract, not a fade-out."""
        body = self._body()
        self.assertRegex(body, r"DURATION\s*-\s*1\.9")

    def test_a_card_owns_the_screen_until_the_next_beat(self):
        """Fading a card out on a fixed timer leaves dead air between beats.

        The first build faded every card 1.5 s after it appeared, so a 15 s
        film had three ~3 s stretches with no text on screen. Each card has to
        hold until the next beat replaces it.
        """
        body = self._body()
        self.assertIn("next - 0.22", body)
        self.assertNotIn("duration: 0.26, ease: 'power1.in' }, 1.5", body)


class NewStyleBuildTests(unittest.TestCase):
    def test_new_style_builds_a_stage_with_episode_content(self):
        for style_id in NEW_STYLES:
            with self.subTest(style=style_id), tempfile.TemporaryDirectory() as tmp:
                dst = build_project(ROOT, ROOT / "demo" / "explain-9x16", style_id, Path(tmp) / style_id)
                body = (dst / "index.html").read_text(encoding="utf-8")
                for token in ("__UMP_CARDS__", "__UMP_EPISODE__", "__UMP_DURATION__", "__UMP_TIMELINE__"):
                    self.assertNotIn(token, body, f"{token} left unsubstituted in stage")
                self.assertTrue((dst / "assets" / "gsap.min.js").is_file())
                self.assertTrue((dst / "meta.json").is_file())
                self.assertIn('data-composition-id="root"', body)

    def test_unregistered_style_still_rejected(self):
        with self.assertRaises(DomainError):
            build_project(ROOT, ROOT / "demo" / "explain-9x16", "not-a-style", Path(tempfile.mkdtemp()))


class SourceAttributionTests(unittest.TestCase):
    def test_notice_records_upstream_style_ids_and_commit(self):
        notice = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        for upstream in ("02-line-art", "09-bauhaus", "22-hud"):
            self.assertIn(upstream, notice, f"{upstream} not attributed")
        self.assertIn("8fab7eb", notice)


class StyleDocTests(unittest.TestCase):
    def test_each_new_style_documents_its_upstream_derivation(self):
        for style_id in NEW_STYLES:
            with self.subTest(style=style_id):
                doc = (ROOT / "styles" / style_id / "STYLE.md").read_text(encoding="utf-8")
                self.assertIn("8fab7eb", doc)
                self.assertIn("Signature", doc, "STYLE.md must list the signature features")


if __name__ == "__main__":
    unittest.main()
