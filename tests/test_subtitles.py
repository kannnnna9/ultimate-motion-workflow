import json
import unittest

import ump_core.subtitles as subtitle_module

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


def long_name_timeline(*, duration_ms=18000):
    words = [
        {"id": f"w{i:05d}", "text": text, "start_ms": start, "end_ms": end,
         "speaker_id": None, "timing_status": "aligned"}
        for i, (text, start, end) in enumerate((
            ("dengan", 10907, 11087),
            ("pemberhentian", 11147, 11688),
            ("di", 11748, 11808),
            ("Halte", 11868, 12108),
            ("Kejaksaan", 12128, 12608),
            ("Agung.", 12648, 14049),
        ), start=29)
    ]
    return speech_timeline(duration_ms=duration_ms, words=words)


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

    def test_long_mobile_cue_uses_smart_wrapping_with_safe_margins(self):
        words = [
            {"id": f"w{i}", "text": text, "start_ms": 10900 + i * 100,
             "end_ms": 10990 + i * 100, "speaker_id": None, "timing_status": "aligned"}
            for i, text in enumerate(("dengan", "pemberhentian", "di", "Halte", "Kejaksaan"))
        ]
        decoded = self.render(
            speech_timeline(duration_ms=18000, words=words), track_duration_ms=18000,
        ).decode("utf-8")
        self.assertIn("WrapStyle:0", decoded)
        self.assertIn(",64,64,230,1", decoded)

    def test_ass_rebalances_terminal_singleton_and_keeps_kejaksaan_agung_together(self):
        decoded = self.render(
            long_name_timeline(), track_duration_ms=18000,
        ).decode("utf-8")
        dialogue = [line for line in decoded.splitlines() if line.startswith("Dialogue:")]
        self.assertEqual(len(dialogue), 2)
        self.assertTrue(dialogue[0].endswith(",dengan pemberhentian di Halte"))
        self.assertTrue(dialogue[1].endswith(",Kejaksaan Agung."))

    def test_single_word_cue_limit_does_not_loop(self):
        style = {**MOBILE_ASS_STYLE, "max_words_per_cue": 1}
        decoded = self.render(style=style).decode("utf-8")
        dialogue = [line for line in decoded.splitlines() if line.startswith("Dialogue:")]
        self.assertEqual(len(dialogue), 3)
        self.assertTrue(all(line.rsplit(",", 1)[-1].strip() for line in dialogue))

    def test_karaoke_sidecar_is_deterministic_word_bound_and_has_mobile_default_style(self):
        timeline = long_name_timeline()
        render = subtitle_module.render_karaoke_subtitles
        output = render(timeline, audio_sha256=AUDIO_SHA256, track_duration_ms=18000)
        document = json.loads(output)
        groups = [[word["text"] for word in group["words"]] for group in document["groups"]]
        self.assertEqual(groups, [["dengan", "pemberhentian", "di", "Halte"], ["Kejaksaan", "Agung."]])
        self.assertEqual(document["audio_sha256"], AUDIO_SHA256)
        self.assertEqual(document["duration_ms"], 18000)
        self.assertEqual(document["groups"][1]["words"][0]["start_ms"], 12128)
        self.assertEqual(document["groups"][1]["words"][1]["end_ms"], 14049)
        self.assertEqual(document["style"]["font_name"], "DejaVu Sans")
        self.assertEqual(document["style"]["font_size"], 56)
        self.assertEqual(document["style"]["active_color"], "#FE7945")
        self.assertEqual(document["style"]["inactive_color"], "#FFFFFF")
        self.assertEqual(output, render(timeline, audio_sha256=AUDIO_SHA256, track_duration_ms=18000))

    def test_karaoke_sidecar_rejects_stale_hash_and_invalid_word_ranges(self):
        render = subtitle_module.render_karaoke_subtitles
        timeline = long_name_timeline()
        with self.assertRaises(ValueError):
            render(timeline, audio_sha256="b" * 64, track_duration_ms=18000)

        too_short = long_name_timeline(duration_ms=14000)
        with self.assertRaises(ValueError):
            render(too_short, audio_sha256=AUDIO_SHA256, track_duration_ms=14000)

        malformed = long_name_timeline()
        malformed["words"][-1]["start_ms"] = 14049
        malformed["words"][-1]["end_ms"] = 14049
        with self.assertRaises(ValueError):
            render(malformed, audio_sha256=AUDIO_SHA256, track_duration_ms=18000)

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
