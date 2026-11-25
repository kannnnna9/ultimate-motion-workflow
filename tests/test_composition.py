import unittest

from ump_core.composition import validate_composition
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


class CompositionValidationTests(unittest.TestCase):
    def test_accepts_the_pinned_silent_vertical_composition(self):
        self.assertEqual(validate_composition(VALID), VALID)

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
