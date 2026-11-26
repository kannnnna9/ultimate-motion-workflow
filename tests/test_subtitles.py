import unittest

from ump_core.subtitles import MOBILE_ASS_STYLE, render_ass, render_subtitles


AUDIO_SHA256 = "a" * 64


def speech_timeline(*, duration_ms=5000, words=None):
    return {
        "schema_version": 1,
        "language": "id",
        "audio_sha256": AUDIO_SHA256,
        "provider": "whisperx",
        "model_id": "cahya/wav2vec2-large-xlsr-indonesian",
        "model_revision": "ce6da00000a403c7cf3857197f4132439667e8f0",
        "status": "aligned",
        "duration_ms": duration_ms,
        "words": words if words is not None else [
            {"id": "w1", "text": "Cakra", "start_ms": 1200, "end_ms": 1450,
             "speaker_id": None, "timing_status": "aligned"},
            {"id": "w2", "text": "Selaras", "start_ms": 1450, "end_ms": 1800,
             "speaker_id": None, "timing_status": "aligned"},
            {"id": "w3", "text": "Wahana.", "start_ms": 1800, "end_ms": 2400,
             "speaker_id": None, "timing_status": "aligned"},
        ],
    }


class SubtitleRendererTests(unittest.TestCase):
    def render(self, timeline=None, *, audio_sha256=AUDIO_SHA256, track_duration_ms=5000, style=None):
        return render_ass(
            timeline or speech_timeline(),
            style or MOBILE_ASS_STYLE,
            audio_sha256=audio_sha256,
            track_duration_ms=track_duration_ms,
        )

    def test_ass_is_deterministic_and_uses_word_clock_timestamps(self):
        output = self.render()
        decoded = output.decode("utf-8")
        self.assertEqual(output, self.render())
        self.assertIn("PlayResX:1080", decoded)
        self.assertIn("PlayResY:1920", decoded)
        self.assertIn(
            "Dialogue: 0,0:00:01.20,0:00:02.40,Default,,0,0,0,,Cakra Selaras Wahana.", decoded,
        )
        self.assertTrue(decoded.endswith("\n"))

    def test_none_mode_returns_no_subtitle_artifact(self):
        self.assertIsNone(render_subtitles("none", None, None))

    def test_text_escaping_preserves_braces_backslashes_punctuation_and_utf8(self):
        words = [{
            "id": "w1", "text": "né {baru} \\N, ya?", "start_ms": 1200, "end_ms": 2400,
            "speaker_id": None, "timing_status": "aligned",
        }]
        decoded = self.render(speech_timeline(words=words)).decode("utf-8")
        self.assertIn("né", decoded)
        self.assertIn(r"\{{}", decoded)
        self.assertIn("}", decoded)
        self.assertIn("\\\u2060N, ya?", decoded)

    def test_stale_audio_hash_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.render(audio_sha256="b" * 64)

    def test_word_end_after_track_duration_is_rejected(self):
        words = [{
            "id": "w1", "text": "telat", "start_ms": 4900, "end_ms": 5001,
            "speaker_id": None, "timing_status": "aligned",
        }]
        with self.assertRaisesRegex(ValueError, "exceeds audio duration"):
            self.render(speech_timeline(duration_ms=5000, words=words))

    def test_unresolved_timeline_cannot_create_ass(self):
        timeline = speech_timeline()
        timeline["status"] = "unresolved"
        timeline["reason"] = "transcript_mismatch"
        timeline["words"] = []
        with self.assertRaisesRegex(ValueError, "unresolved"):
            self.render(timeline)

    def test_ass_mode_returns_bytes_and_rejects_style_injection(self):
        output = render_subtitles(
            "ass", speech_timeline(), MOBILE_ASS_STYLE,
            audio_sha256=AUDIO_SHA256, track_duration_ms=5000,
        )
        self.assertIsInstance(output, bytes)
        bad_style = {**MOBILE_ASS_STYLE, "font_name": "Arial,\nDialogue: injected"}
        with self.assertRaisesRegex(ValueError, "font_name"):
            self.render(style=bad_style)
        huge_style = {**MOBILE_ASS_STYLE, "outline": 10**1000}
        with self.assertRaisesRegex(ValueError, "finite number"):
            self.render(style=huge_style)


if __name__ == "__main__":
    unittest.main()
