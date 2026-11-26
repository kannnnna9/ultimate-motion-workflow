import unittest

from ump_core.composition import validate_composition
import ump_core.composition as composition_module
from ump_core.jobs import DomainError


VALID = {
    "html": "<!doctype html><html><body>scene</body></html>",
    "width": 1080,
    "height": 1920,
    "fps": 30,
    "duration_frames": 30,
    "audio_enabled": False,
    "subtitles_enabled": False,
}


def manifest_asset(asset_id="leaf", size_bytes=1):
    return {
        "id": asset_id, "version": 1, "sha256": "a" * 64, "type": "vector",
        "format": "svg", "media_type": "image/svg+xml", "size_bytes": size_bytes,
        "width": 8, "height": 6, "source": "procedural", "provenance": "test shape",
        "license": "MIT", "seed": 1, "parameters": {"color": "blue"}, "locked": True,
        "path": f"assets/{asset_id}-v1.svg",
    }


class CompositionValidationTests(unittest.TestCase):
    def test_accepts_the_pinned_silent_vertical_composition(self):
        self.assertEqual(validate_composition(VALID), VALID)

    def test_accepts_unique_versioned_asset_references_without_changing_m1_defaults(self):
        self.assertEqual(validate_composition(VALID), VALID)
        payload = {**VALID, "assets": [{"id": "leaf", "version": 2}]}
        self.assertEqual(validate_composition(payload), payload)

    def test_rejects_asset_paths_duplicates_and_invalid_versions(self):
        cases = (
            [{"id": "leaf", "version": 1, "path": "../../etc/passwd"}],
            [{"id": "leaf", "version": 1}, {"id": "leaf", "version": 2}],
            [{"id": "leaf", "version": True}],
        )
        for assets in cases:
            with self.subTest(assets=assets):
                with self.assertRaises(DomainError) as raised:
                    validate_composition({**VALID, "assets": assets})
                self.assertEqual(raised.exception.code, "INVALID_COMPOSITION")

    def test_asset_manifest_requires_canonical_relative_paths_and_bundle_bound(self):
        good = manifest_asset()
        self.assertEqual(composition_module.validate_asset_manifest([good]), [good])
        for field, value in (("path", "../../etc/passwd"), ("sha256", "bad")):
            malformed = {**good, field: value}
            with self.subTest(field=field), self.assertRaises(DomainError) as raised:
                composition_module.validate_asset_manifest([malformed])
            self.assertEqual(raised.exception.code, "INVALID_ASSET_MANIFEST")

        oversized = [manifest_asset(f"leaf-{index}", 10 * 1024 * 1024) for index in range(5)]
        with self.assertRaises(DomainError) as raised:
            composition_module.validate_asset_manifest(oversized)
        self.assertEqual(raised.exception.code, "ASSET_BUNDLE_TOO_LARGE")

    def test_rejects_canvas_or_frame_rate_mismatch(self):
        for field, value in (("width", 720), ("height", 1280), ("fps", 24)):
            with self.subTest(field=field):
                payload = {**VALID, field: value}
                with self.assertRaises(DomainError) as raised:
                    validate_composition(payload)
                self.assertEqual(raised.exception.code, "INVALID_COMPOSITION")

    def test_rejects_zero_or_non_integer_frame_count(self):
        for frames in (0, -1, 30.0, True):
            with self.subTest(frames=frames):
                payload = {**VALID, "duration_frames": frames}
                with self.assertRaises(DomainError) as raised:
                    validate_composition(payload)
                self.assertEqual(raised.exception.code, "INVALID_DURATION")

    def test_rejects_duration_over_sixty_seconds(self):
        payload = {**VALID, "duration_frames": 1801}
        with self.assertRaises(DomainError) as raised:
            validate_composition(payload)
        self.assertEqual(raised.exception.code, "DURATION_LIMIT_EXCEEDED")

    def test_rejects_oversized_html(self):
        payload = {**VALID, "html": "x" * (1_048_576 + 1)}
        with self.assertRaises(DomainError) as raised:
            validate_composition(payload)
        self.assertEqual(raised.exception.code, "COMPOSITION_TOO_LARGE")

    def test_rejects_html_that_cannot_be_encoded_as_utf8(self):
        payload = {**VALID, "html": "<html>\ud800</html>"}
        with self.assertRaises(DomainError) as raised:
            validate_composition(payload)
        self.assertEqual(raised.exception.code, "INVALID_HTML_ENCODING")

    def test_rejects_paths_and_unknown_fields(self):
        payload = {**VALID, "path": "/etc/passwd"}
        with self.assertRaises(DomainError) as raised:
            validate_composition(payload)
        self.assertEqual(raised.exception.code, "INVALID_COMPOSITION")

    def test_rejects_audio_or_subtitles_in_the_m1_silent_route(self):
        for field in ("audio_enabled", "subtitles_enabled"):
            with self.subTest(field=field):
                payload = {**VALID, field: True}
                with self.assertRaises(DomainError) as raised:
                    validate_composition(payload)
                self.assertEqual(raised.exception.code, "INVALID_COMPOSITION")


if __name__ == "__main__":
    unittest.main()
