import copy
import unittest

from ump_core.audio import (
    native_timeline_from_boundaries,
    normalize_provider_timeline,
    resolve_speech_anchors,
    validate_speech_timeline,
)
from scripts.asr.align import (
    flatten_transcript,
    install_whisperx_local_sentence_splitter,
    resolve_workspace_file,
    verify_model_snapshot,
)


AUDIO_SHA256 = "a" * 64


def valid_timeline():
    return {
        "schema_version": 1,
        "language": "id",
        "audio_sha256": AUDIO_SHA256,
        "provider": "whisperx",
        "model_id": "cahya/wav2vec2-large-xlsr-indonesian",
        "model_revision": "ce6da00000a403c7cf3857197f4132439667e8f0",
        "status": "aligned",
        "duration_ms": 5000,
        "words": [
            {
                "id": "w0001",
                "text": "Cakra",
                "start_ms": 120,
                "end_ms": 360,
                "speaker_id": "speaker-1",
                "timing_status": "aligned",
                "score": 0.97,
            },
            {
                "id": "w0002",
                "text": "Selaras",
                "start_ms": 410,
                "end_ms": 760,
                "speaker_id": "speaker-1",
                "timing_status": "aligned",
            },
        ],
    }


class SpeechTimelineTests(unittest.TestCase):
    def test_valid_timeline_is_bound_to_exact_audio_hash_and_preserves_text(self):
        doc = valid_timeline()
        original = copy.deepcopy(doc)
        result = validate_speech_timeline(doc, audio_sha256=AUDIO_SHA256, duration_ms=5000)
        self.assertEqual(result, original)
        self.assertEqual(result["words"][0]["text"], "Cakra")
        self.assertEqual(result["audio_sha256"], AUDIO_SHA256)

    def test_stale_audio_hash_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_speech_timeline(valid_timeline(), audio_sha256="b" * 64, duration_ms=5000)

    def test_noncanonical_audio_hash_is_rejected(self):
        doc = valid_timeline()
        doc["audio_sha256"] = "A" * 64
        with self.assertRaises(ValueError):
            validate_speech_timeline(doc, audio_sha256=AUDIO_SHA256, duration_ms=5000)

    def test_timeline_duration_must_match_decoded_audio(self):
        with self.assertRaises(ValueError):
            validate_speech_timeline(valid_timeline(), audio_sha256=AUDIO_SHA256, duration_ms=4999)

    def test_start_must_precede_end(self):
        doc = valid_timeline()
        doc["words"][0]["end_ms"] = doc["words"][0]["start_ms"]
        with self.assertRaises(ValueError):
            validate_speech_timeline(doc, audio_sha256=AUDIO_SHA256, duration_ms=5000)

    def test_negative_and_out_of_duration_times_are_rejected(self):
        for field, value in (("start_ms", -1), ("end_ms", 5001)):
            with self.subTest(field=field):
                doc = valid_timeline()
                doc["words"][0][field] = value
                with self.assertRaises(ValueError):
                    validate_speech_timeline(doc, audio_sha256=AUDIO_SHA256, duration_ms=5000)

    def test_empty_or_whitespace_word_text_is_rejected(self):
        for text in ("", "  \t"):
            with self.subTest(text=repr(text)):
                doc = valid_timeline()
                doc["words"][0]["text"] = text
                with self.assertRaises(ValueError):
                    validate_speech_timeline(doc, audio_sha256=AUDIO_SHA256, duration_ms=5000)

    def test_milliseconds_must_be_integers_not_float_or_bool(self):
        for value in (120.0, True):
            with self.subTest(value=value):
                doc = valid_timeline()
                doc["words"][0]["start_ms"] = value
                with self.assertRaises(ValueError):
                    validate_speech_timeline(doc, audio_sha256=AUDIO_SHA256, duration_ms=5000)

    def test_word_ids_must_be_unique(self):
        doc = valid_timeline()
        doc["words"][1]["id"] = doc["words"][0]["id"]
        with self.assertRaises(ValueError):
            validate_speech_timeline(doc, audio_sha256=AUDIO_SHA256, duration_ms=5000)

    def test_word_order_is_monotonic_per_speaker_track(self):
        doc = valid_timeline()
        doc["words"][1]["start_ms"] = 100
        doc["words"][1]["end_ms"] = 200
        with self.assertRaises(ValueError):
            validate_speech_timeline(doc, audio_sha256=AUDIO_SHA256, duration_ms=5000)

    def test_overlapping_different_speaker_tracks_are_valid(self):
        doc = valid_timeline()
        doc["words"][1].update(start_ms=150, end_ms=300, speaker_id="speaker-2")
        self.assertEqual(
            validate_speech_timeline(doc, audio_sha256=AUDIO_SHA256, duration_ms=5000),
            doc,
        )

    def test_only_supported_timing_statuses_are_accepted(self):
        for status in ("native", "aligned", "estimated", "unresolved"):
            with self.subTest(status=status):
                doc = valid_timeline()
                doc["status"] = status
                doc["words"][0]["timing_status"] = status
                if status == "unresolved":
                    doc["reason"] = "provider_failed"
                self.assertEqual(
                    validate_speech_timeline(doc, audio_sha256=AUDIO_SHA256, duration_ms=5000)["status"],
                    status,
                )
        doc = valid_timeline()
        doc["words"][0]["timing_status"] = "guessed"
        with self.assertRaises(ValueError):
            validate_speech_timeline(doc, audio_sha256=AUDIO_SHA256, duration_ms=5000)

    def test_unresolved_timeline_requires_reason_and_may_have_no_words(self):
        doc = valid_timeline()
        doc.update(status="unresolved", words=[], reason="provider_failed")
        self.assertEqual(
            validate_speech_timeline(doc, audio_sha256=AUDIO_SHA256, duration_ms=5000)["words"],
            [],
        )
        del doc["reason"]
        with self.assertRaises(ValueError):
            validate_speech_timeline(doc, audio_sha256=AUDIO_SHA256, duration_ms=5000)

    def test_anchor_resolves_to_word_onset_and_applies_offset(self):
        timeline = valid_timeline()
        resolved = resolve_speech_anchors(
            timeline,
            [{"id": "name-hit", "word_id": "w0002", "action_id": "highlight", "offset_ms": 0}],
            video_duration_ms=4000,
        )
        self.assertEqual(
            resolved,
            [{"id": "name-hit", "word_id": "w0002", "action_id": "highlight", "at_ms": 410}],
        )
        shifted = resolve_speech_anchors(
            timeline,
            [{"id": "name-hit", "word_id": "w0002", "action_id": "highlight", "offset_ms": 25}],
            video_duration_ms=4000,
        )
        self.assertEqual(shifted[0]["at_ms"], 435)

    def test_missing_anchor_word_is_rejected(self):
        with self.assertRaises(ValueError):
            resolve_speech_anchors(
                valid_timeline(),
                [{"id": "missing", "word_id": "absent", "action_id": "highlight", "offset_ms": 0}],
                video_duration_ms=4000,
            )

    def test_anchor_outside_video_duration_is_rejected(self):
        timeline = valid_timeline()
        timeline["words"][1].update(start_ms=4900, end_ms=4999)
        with self.assertRaises(ValueError):
            resolve_speech_anchors(
                timeline,
                [{"id": "late", "word_id": "w0002", "action_id": "highlight", "offset_ms": 101}],
                video_duration_ms=5000,
            )

    def test_provider_timing_keeps_approved_word_surface_text(self):
        result = normalize_provider_timeline(
            "Cakra Selaras Wahana,",
            [
                {"text": "cakra", "start_ms": 100, "end_ms": 320, "score": 0.9},
                {"text": "selaras", "start_ms": 340, "end_ms": 600, "score": 0.9},
                {"text": "wahana", "start_ms": 620, "end_ms": 900, "score": 0.8},
            ],
            audio_sha256=AUDIO_SHA256,
            duration_ms=5000,
            provider="whisperx",
            model_id="cahya/wav2vec2-large-xlsr-indonesian",
            model_revision="ce6da00000000000000000000000000000000000",
            timing_status="aligned",
        )
        self.assertEqual(result["status"], "aligned")
        self.assertEqual(result["approved_transcript"], "Cakra Selaras Wahana,")
        self.assertEqual([word["text"] for word in result["words"]], ["Cakra", "Selaras", "Wahana,"])

    def test_provider_token_mismatch_is_unresolved_without_replacing_script(self):
        result = normalize_provider_timeline(
            "Cakra Selaras Wahana",
            [
                {"text": "Cakra", "start_ms": 100, "end_ms": 320},
                {"text": "Selaras", "start_ms": 340, "end_ms": 600},
                {"text": "Wahanna", "start_ms": 620, "end_ms": 900},
            ],
            audio_sha256=AUDIO_SHA256,
            duration_ms=5000,
            provider="faster-whisper",
            model_id="Systran/faster-whisper-small",
            model_revision="536b0662742c02347bc0e980a01041f333bce120",
            timing_status="estimated",
        )
        self.assertEqual(result["status"], "unresolved")
        self.assertEqual(result["reason"], "transcript_mismatch")
        self.assertEqual(result["approved_transcript"], "Cakra Selaras Wahana")
        self.assertEqual(result["recognized_text"], "Cakra Selaras Wahanna")
        self.assertEqual(result["words"], [])
        self.assertEqual([word["text"] for word in result["recognized_words"]], ["Cakra", "Selaras", "Wahanna"])

    def test_edge_native_boundaries_keep_exact_text_and_label_status(self):
        result = native_timeline_from_boundaries(
            [
                {"text": "Cakra,", "start_ms": 100, "end_ms": 320},
                {"text": "Selaras", "start_ms": 350, "end_ms": 700},
            ],
            audio_sha256=AUDIO_SHA256,
            duration_ms=5000,
            voice="id-ID-ArdiNeural",
            model_revision="edge-tts-7.3.1",
        )
        self.assertEqual(result["provider"], "edge-tts")
        self.assertEqual(result["status"], "native")
        self.assertEqual([word["text"] for word in result["words"]], ["Cakra,", "Selaras"])
        self.assertEqual([word["timing_status"] for word in result["words"]], ["native", "native"])
        self.assertEqual(result["words"][0]["start_ms"], 100)

    def test_anchor_spec_requires_integer_offset_and_unique_id(self):
        for specs in (
            [{"id": "x", "word_id": "w0001", "action_id": "a", "offset_ms": True}],
            [
                {"id": "x", "word_id": "w0001", "action_id": "a", "offset_ms": 0},
                {"id": "x", "word_id": "w0002", "action_id": "b", "offset_ms": 0},
            ],
        ):
            with self.subTest(specs=specs), self.assertRaises(ValueError):
                resolve_speech_anchors(valid_timeline(), specs, video_duration_ms=4000)
class AlignRunnerHelperTests(unittest.TestCase):
    def test_overlapping_provider_spans_are_clamped_in_place_before_matching(self):
        result = normalize_provider_timeline(
            "Dari Blok M Cakra Selaras",
            [
                {"text": "Dari", "start_ms": 2000, "end_ms": 2400},
                {"text": "Blok", "start_ms": 2400, "end_ms": 2600},
                {"text": "M", "start_ms": 2600, "end_ms": 3000},
                {"text": "Cakra", "start_ms": 2900, "end_ms": 3300},
                {"text": "Selaras", "start_ms": 3300, "end_ms": 3700},
            ],
            audio_sha256=AUDIO_SHA256,
            duration_ms=5000,
            provider="faster-whisper",
            model_id="Systran/faster-whisper-small",
            model_revision="536b0662742c02347bc0e980a01041f333bce120",
            timing_status="estimated",
        )
        self.assertEqual(result["status"], "estimated")
        self.assertEqual([word["text"] for word in result["words"]], ["Dari", "Blok", "M", "Cakra", "Selaras"])
        spans = [(word["start_ms"], word["end_ms"]) for word in result["words"]]
        self.assertEqual(spans[0], (2000, 2400))
        self.assertEqual(spans[3], (3000, 3300))
        for (start, end), (next_start, _) in zip(spans, spans[1:]):
            self.assertLess(start, end)
            self.assertLessEqual(end, next_start)

    def test_out_of_order_words_are_never_resequenced_into_a_match(self):
        result = normalize_provider_timeline(
            "Dari Blok M",
            [
                {"text": "Blok", "start_ms": 2600, "end_ms": 2800},
                {"text": "Dari", "start_ms": 2000, "end_ms": 2400},
                {"text": "M", "start_ms": 2820, "end_ms": 3000},
            ],
            audio_sha256=AUDIO_SHA256,
            duration_ms=5000,
            provider="faster-whisper",
            model_id="Systran/faster-whisper-small",
            model_revision="536b0662742c02347bc0e980a01041f333bce120",
            timing_status="estimated",
        )
        self.assertEqual(result["status"], "unresolved")
        self.assertEqual(result["reason"], "transcript_mismatch")
        self.assertEqual(result["words"], [])
        self.assertEqual(
            [word["text"] for word in result["recognized_words"]],
            ["Blok", "Dari", "M"],
            "clamping may move spans but must never reorder recognized words",
        )

    def test_flatten_transcript_preserves_approved_word_surfaces(self):
        transcript = flatten_transcript(
            {
                "beats": [
                    {"id": "b1", "narration": "Cakra Selaras Wahana,"},
                    {"id": "b2", "narration": "pada dua ribu dua puluh dua."},
                ]
            }
        )
        self.assertEqual(transcript, "Cakra Selaras Wahana, pada dua ribu dua puluh dua.")

    def test_transcript_requires_nonempty_narration(self):
        with self.assertRaises(ValueError):
            flatten_transcript({"beats": [{"id": "b1", "narration": "  "}]})

    def test_whisperx_local_sentence_splitter_bypasses_nltk_loader(self):
        class FakeAlignment:
            def nltk_load(self, *_args, **_kwargs):
                raise AssertionError("NLTK corpus loader must not be called")

        alignment = FakeAlignment()
        install_whisperx_local_sentence_splitter(alignment)
        transcript = "Cakra Selaras Wahana. Transjakarta melayani Ragunan."
        splitter = alignment.nltk_load("tokenizers/punkt_tab/english.pickle")
        self.assertEqual(list(splitter.span_tokenize(transcript)), [(0, len(transcript))])

    def test_runner_accepts_only_regular_files_inside_workspace(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            inside = root / "audio.mp3"
            inside.write_bytes(b"audio")
            outside = Path(directory) / "outside.json"
            outside.write_text("{}", encoding="utf-8")
            self.assertEqual(resolve_workspace_file(str(inside), root), inside)
            with self.assertRaises(ValueError):
                resolve_workspace_file(str(outside), root)
            link = root / "linked.json"
            link.symlink_to(outside)
            with self.assertRaises(ValueError):
                resolve_workspace_file(str(link), root)

    def test_model_snapshot_accepts_only_verified_in_root_cache_symlink(self):
        import hashlib
        import tempfile
        from pathlib import Path

        payload = b"pinned weights"
        relative = "models--example/snapshots/rev1"
        spec = {
            "cache_relative_path": relative,
            "files": [
                {"filename": "model.bin", "size_bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "models"
            blob = root / "models--example" / "blobs" / "model-hash"
            blob.parent.mkdir(parents=True)
            blob.write_bytes(payload)
            snapshot = root / relative
            snapshot.mkdir(parents=True)
            (snapshot / "model.bin").symlink_to("../../blobs/model-hash")
            self.assertEqual(verify_model_snapshot(spec, root), snapshot)

    def test_model_snapshot_rejects_symlink_outside_model_root(self):
        import hashlib
        import tempfile
        from pathlib import Path

        payload = b"pinned weights"
        relative = "models--example/snapshots/rev1"
        spec = {
            "cache_relative_path": relative,
            "files": [
                {"filename": "model.bin", "size_bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "models"
            snapshot = root / relative
            snapshot.mkdir(parents=True)
            outside = base / "outside.bin"
            outside.write_bytes(payload)
            (snapshot / "model.bin").symlink_to(outside)
            with self.assertRaises(ValueError):
                verify_model_snapshot(spec, root)

    def test_model_snapshot_hash_verification_detects_tampering(self):
        import hashlib
        import tempfile
        from pathlib import Path

        payload = b"pinned weights"
        relative = "models--example/snapshots/rev1"
        spec = {
            "cache_relative_path": relative,
            "files": [
                {"filename": "model.bin", "size_bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "models"
            snapshot = root / relative
            snapshot.mkdir(parents=True)
            weight = snapshot / "model.bin"
            weight.write_bytes(payload)
            self.assertEqual(verify_model_snapshot(spec, root), snapshot)
            weight.write_bytes(b"tampered")
            with self.assertRaises(ValueError):
                verify_model_snapshot(spec, root)
@unittest.skipUnless(__import__("os").environ.get("UMP_ASR_INTEGRATION") == "1", "set UMP_ASR_INTEGRATION=1 for isolated real-audio provider integration")
class RealSpeechProviderIntegrationTests(unittest.TestCase):
    def test_pinned_providers_run_offline_on_committed_indonesian_audio(self):
        import hashlib
        import json
        import os
        import re
        import shutil
        import subprocess
        import sys
        import uuid
        from difflib import SequenceMatcher
        from pathlib import Path

        from ump_core import sandbox
        from ump_core.audio import validate_speech_timeline

        repo = Path(__file__).resolve().parents[1]
        audio_source = repo / "demo" / "ump-second" / "assets" / "audio" / "vo.mp3"
        script_source = repo / "demo" / "ump-second" / "naskah.json"
        fixture_source = repo / "demo" / "ump-second" / "fixtures" / "vo-boundaries.json"
        audio_sha256 = hashlib.sha256(audio_source.read_bytes()).hexdigest()
        self.assertEqual(audio_sha256, "35de0ac92a01d9b886e76ad0aa584514a5a9eab850225aacde125b6ccf5016db")
        fixture = json.loads(fixture_source.read_text(encoding="utf-8"))
        fixture_words = fixture["words"]
        sample_naskah = json.loads(script_source.read_text(encoding="utf-8"))
        approved = " ".join(beat["narration"].strip() for beat in sample_naskah["beats"])
        runtime_root = Path.home() / ".local" / "share" / "ump-v2" / "asr-runtime"
        model_root = Path.home() / ".cache" / "ump-v2" / "whisperx"
        runner = repo / "scripts" / "asr" / "align.py"
        python = runtime_root / "bin" / "python"
        normalized = lambda text: re.sub(r"[^a-z0-9]", "", text.casefold())

        def phrase_span(words, phrase):
            expected = [normalized(token) for token in phrase]
            actual = [normalized(word["text"]) for word in words]
            for index in range(len(actual) - len(expected) + 1):
                if actual[index:index + len(expected)] == expected:
                    return {"start_ms": words[index]["start_ms"], "end_ms": words[index + len(expected) - 1]["end_ms"]}
            return None

        def unresolved_token_groups(approved_text, recognized_words, *, status):
            if status != "unresolved":
                return []
            expected = approved_text.split()
            actual = [word["text"] for word in recognized_words]
            matcher = SequenceMatcher(
                None,
                [normalized(token) for token in expected],
                [normalized(token) for token in actual],
                autojunk=False,
            )
            return [
                {"operation": operation, "approved": expected[i1:i2], "recognized": actual[j1:j2]}
                for operation, i1, i2, j1, j2 in matcher.get_opcodes()
                if operation != "equal"
            ]

        results = {}
        for provider in ("whisperx", "faster-whisper"):
            workspace = sandbox.create_workspace(f"m3-asr-{provider}-" + uuid.uuid4().hex)
            try:
                staged_audio = workspace / "vo.mp3"
                staged_script = workspace / "naskah.json"
                shutil.copyfile(audio_source, staged_audio)
                shutil.copyfile(script_source, staged_script)
                os.chmod(staged_audio, 0o600)
                os.chmod(staged_script, 0o600)
                process, unit = sandbox.start_asr_isolated(
                    [
                        str(python), str(runner),
                        "--audio", str(staged_audio),
                        "--transcript-json", str(staged_script),
                        "--language", "id",
                        "--provider", provider,
                    ],
                    workspace=workspace,
                    runtime_root=runtime_root,
                    model_root=model_root,
                    timeout_s=300,
                )
                status = sandbox.wait_isolated(process, unit, timeout_s=300)
                stdout, stderr = process.communicate()
                self.assertEqual(status, 0, stderr.decode(errors="replace")[-4000:])
                self.assertLessEqual(len(stdout), 4 * 1024 * 1024)
                lines = stdout.decode("utf-8").splitlines()
                self.assertEqual(len(lines), 1, "runner must emit exactly one JSON line")
                timeline = json.loads(lines[0])
                self.assertEqual(timeline["provider"], provider)
                self.assertEqual(timeline["audio_sha256"], audio_sha256)
                self.assertEqual(timeline["approved_transcript"], approved)
                self.assertEqual(timeline["duration_ms"], 17952)
                self.assertEqual(timeline["model_revision"], "ce6da00000a403c7cf3857197f4132439667e8f0" if provider == "whisperx" else "536b0662742c02347bc0e980a01041f333bce120")
                validated = validate_speech_timeline(timeline, audio_sha256=audio_sha256, duration_ms=17952)
                self.assertTrue(validated["metrics"]["wall_seconds"] > 0)
                self.assertTrue(validated["metrics"]["max_rss_kib"] > 0)
                if provider == "whisperx":
                    self.assertEqual(validated["status"], "aligned", validated.get("reason"))
                    observed_words = validated["words"]
                    self.assertEqual([word["text"] for word in observed_words], approved.split())
                else:
                    self.assertIn(validated["status"], {"estimated", "unresolved"})
                    if validated["status"] == "unresolved":
                        self.assertTrue(validated.get("reason"))
                        observed_words = validated.get("recognized_words", [])
                    else:
                        self.assertEqual([word["text"] for word in validated["words"]], approved.split())
                        observed_words = validated["words"]

                unresolved_tokens = unresolved_token_groups(
                    approved,
                    validated.get("recognized_words", []),
                    status=validated["status"],
                )
                if validated["status"] == "aligned":
                    self.assertEqual(unresolved_tokens, [])
                elif validated["status"] == "unresolved":
                    self.assertTrue(unresolved_tokens)

                phrases = {
                    "Cakra Selaras Wahana": ["Cakra", "Selaras", "Wahana"],
                    "Transjakarta": ["Transjakarta"],
                    "Ragunan": ["Ragunan"],
                    "Kejaksaan Agung": ["Kejaksaan", "Agung"],
                    "year_2022": ["dua", "ribu", "dua", "puluh", "dua"],
                }
                phrase_results = {label: phrase_span(observed_words, tokens) for label, tokens in phrases.items()}
                gaps = {}
                gap_cases = (("We_to_Pada", 12, 13), ("Agung_to_Di", 33, 34), ("sama_to_ada", 37, 38))
                for label, left, right in gap_cases:
                    expected_gap = fixture_words[right]["start_ms"] - fixture_words[left]["end_ms"]
                    actual_gap = None
                    if len(observed_words) > right:
                        actual_gap = observed_words[right]["start_ms"] - observed_words[left]["end_ms"]
                    gaps[label] = {"fixture_ms": expected_gap, "observed_ms": actual_gap}
                results[provider] = {
                    "status": validated["status"],
                    "model_id": validated["model_id"],
                    "model_revision": validated["model_revision"],
                    "duration_ms": validated["duration_ms"],
                    "word_count": len(validated["words"]),
                    "recognized_word_count": len(validated.get("recognized_words", [])),
                    "unresolved_reason": validated.get("reason"),
                    "recognized_text": validated.get("recognized_text"),
                    "recognized_words": validated.get("recognized_words", []),
                    "unresolved_tokens": unresolved_tokens,
                    "metrics": validated["metrics"],
                    "phrases": phrase_results,
                    "pause_gaps": gaps,
                }
            finally:
                if workspace.exists():
                    sandbox.cleanup_workspace(workspace)

        evidence_root = Path(os.environ["UMP_DATA_ROOT"]).expanduser().resolve(strict=True) / "evidence" / "m3"
        evidence_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        evidence_name = os.environ.get("UMP_ASR_EVIDENCE_NAME", "provider-integration.json")
        self.assertEqual(Path(evidence_name).name, evidence_name)
        self.assertTrue(evidence_name.endswith(".json"))
        evidence_path = evidence_root / evidence_name
        self.assertFalse(evidence_path.exists(), f"refusing to overwrite prior evidence: {evidence_path}")
        evidence = {
            "audio_sha256": audio_sha256,
            "audio_duration_ms": 17952,
            "approved_transcript": approved,
            "fixture_timestamps_are_comparison_only": True,
            "providers": results,
        }
        with evidence_path.open("x", encoding="utf-8") as output:
            json.dump(evidence, output, ensure_ascii=False, indent=2)
            output.write("\n")
        os.chmod(evidence_path, 0o600)
        self.assertEqual(set(results), {"whisperx", "faster-whisper"})


if __name__ == "__main__":
    unittest.main()
