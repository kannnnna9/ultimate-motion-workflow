import hashlib
import io
import stat
import sys
import tempfile
import types
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ump_core import tools
from ump_core.jobs import DomainError, JobStore
from ump_core.tools import _parse_audio_probe, _read_audio_attachment


def probe_result(codec="mp3", format_name="mp3", duration="2.5", streams=None):
    return {
        "format": {"format_name": format_name, "duration": duration},
        "streams": streams if streams is not None else [{
            "codec_type": "audio", "codec_name": codec, "sample_rate": "44100", "channels": 2,
        }],
    }


def tiny_wav():
    out = io.BytesIO()
    with wave.open(out, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b"\0\0" * 4000)
    return out.getvalue()


class AudioProbeTests(unittest.TestCase):
    def test_normalizes_supported_mp3_metadata(self):
        result = _parse_audio_probe(probe_result(), ".mp3")
        self.assertEqual(result["codec"], "mp3")
        self.assertEqual(result["media_type"], "audio/mpeg")
        self.assertEqual(result["source_format"], "mp3")
        self.assertEqual(result["duration_ms"], 2500)
        self.assertEqual(result["sample_rate"], 44100)
        self.assertEqual(result["channels"], 2)

    def test_rejects_unsupported_codec_or_extension_mismatch(self):
        for probe, suffix in (
            (probe_result(codec="pcm_mulaw", format_name="wav"), ".wav"),
            (probe_result(codec="opus", format_name="ogg"), ".mp3"),
        ):
            with self.subTest(probe=probe, suffix=suffix), self.assertRaises(DomainError) as raised:
                _parse_audio_probe(probe, suffix)
            self.assertEqual(raised.exception.code, "UNSUPPORTED_AUDIO_FORMAT")

    def test_rejects_missing_or_multiple_audio_streams(self):
        for streams in (
            [],
            [
                {"codec_type": "audio", "codec_name": "mp3", "sample_rate": "44100", "channels": 2},
                {"codec_type": "audio", "codec_name": "mp3", "sample_rate": "44100", "channels": 2},
            ],
        ):
            with self.subTest(streams=streams), self.assertRaises(DomainError) as raised:
                _parse_audio_probe(probe_result(streams=streams), ".mp3")
            self.assertEqual(raised.exception.code, "INVALID_AUDIO_METADATA")

    def test_rejects_invalid_or_overlong_duration(self):
        for duration in ("nan", "inf", "0", "600.001"):
            with self.subTest(duration=duration), self.assertRaises(DomainError) as raised:
                _parse_audio_probe(probe_result(duration=duration), ".mp3")
            self.assertEqual(raised.exception.code, "INVALID_AUDIO_METADATA")


class AudioAttachmentTests(unittest.TestCase):
    def test_reads_only_current_regular_cache_files_and_enforces_20_mib(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "profile"
            cache = home / "cache" / "audio"
            cache.mkdir(parents=True)
            current = cache / "audio_012345abcdef.wav"
            current.write_bytes(tiny_wav())
            base = types.ModuleType("gateway.platforms.base")
            base.get_audio_cache_dir = lambda: cache
            gateway = types.ModuleType("gateway")
            gateway.__path__ = []
            platforms = types.ModuleType("gateway.platforms")
            platforms.__path__ = []
            modules = {
                "gateway": gateway,
                "gateway.platforms": platforms,
                "gateway.platforms.base": base,
            }
            with patch.dict(sys.modules, modules), patch.object(tools, "_home", return_value=home):
                data, suffix = _read_audio_attachment(current)
                self.assertEqual((data, suffix), (tiny_wav(), ".wav"))
                outside = Path(tmp) / "outside.wav"
                outside.write_bytes(tiny_wav())
                current.unlink()
                current.symlink_to(outside)
                with self.assertRaises(DomainError):
                    _read_audio_attachment(current)
                current.unlink()
                current.touch()
                with current.open("r+b") as stream:
                    stream.truncate(20 * 1024 * 1024 + 1)
                with self.assertRaises(DomainError) as raised:
                    _read_audio_attachment(current)
                self.assertEqual(raised.exception.code, "INVALID_ATTACHMENT")

    def test_import_audio_binds_exact_current_dm_caption_and_keeps_voice_shortcut(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            home = Path(tmp) / "profile"
            cache = home / "cache" / "audio"
            cache.mkdir(parents=True)
            upload = cache / "audio_012345abcdef.wav"
            data = tiny_wav()
            upload.write_bytes(data)
            store = JobStore(root)
            job = store.create("audio import", mode="autonomous")
            job_id = job["job_id"]

            event_module = types.ModuleType("gateway.platforms.event")
            event_module.MessageType = SimpleNamespace(TEXT="text")
            base = types.ModuleType("gateway.platforms.base")
            base.get_audio_cache_dir = lambda: cache
            gateway_module = types.ModuleType("gateway")
            gateway_module.__path__ = []
            platforms_module = types.ModuleType("gateway.platforms")
            platforms_module.__path__ = []
            modules = {
                "gateway": gateway_module,
                "gateway.platforms": platforms_module,
                "gateway.platforms.event": event_module,
                "gateway.platforms.base": base,
            }
            source = SimpleNamespace(
                platform=SimpleNamespace(value="telegram"), chat_type="dm",
                user_id="42", chat_id="42", is_bot=False,
            )
            gateway = SimpleNamespace(
                _is_user_authorized_for_source=lambda _source, allow_adapter_delegation=False: True,
            )
            caption = f"UMP AUDIO {job_id} 1 music1 music"
            message = SimpleNamespace(
                source=source, raw_message=SimpleNamespace(), message_id="message-audio-1",
                text=caption, message_type="voice", media_urls=[str(upload)], media_types=["audio/wav"],
            )
            event_key = (home, "42", "42", "message-audio-1")
            with tools._EVENT_LOCK:
                tools._EVENTS.clear()
            with (
                patch.dict(sys.modules, modules),
                patch.object(tools, "_home", return_value=home),
                patch.object(tools, "_event_key", return_value=event_key),
            ):
                tools.inbound(message, gateway)
                with self.assertRaises(DomainError) as wrong:
                    tools._authorization(f"UMP AUDIO {job_id} 1 other music", code="ATTACHMENT_REQUIRED")
                self.assertEqual(wrong.exception.code, "ATTACHMENT_REQUIRED")
                imported = tools._job(store, "import_audio", {
                    "job_id": job_id, "rev": 1, "track_id": "music1", "kind": "music",
                })
                self.assertEqual(imported["rev"], 2)
                self.assertNotIn(str(upload), repr(imported))
                record = imported["audio_library"][0]
                self.assertEqual(store.get(job_id).get("audio_library"), imported["audio_library"])
                self.assertEqual(record["track_id"], "music1")
                self.assertEqual(record["kind"], "music")
                self.assertEqual(record["media_type"], "audio/wav")
                self.assertEqual(record["codec"], "pcm_s16le")
                self.assertEqual(record["duration_ms"], 250)
                self.assertEqual(record["sha256"], hashlib.sha256(data).hexdigest())
                blob = root / "jobs" / job_id / "audio_blobs" / f"{record['blob_ref']}.wav"
                self.assertEqual(blob.read_bytes(), data)
                self.assertEqual(stat.S_IMODE(blob.stat().st_mode), 0o600)
                upload.write_bytes(b"replacement after import")
                self.assertEqual(blob.read_bytes(), data)
                duplicate_caption = f"UMP AUDIO {job_id} 2 music1 music"
                duplicate_message = SimpleNamespace(
                    source=source, raw_message=SimpleNamespace(), message_id="message-audio-duplicate",
                    text=duplicate_caption, message_type="voice", media_urls=[str(upload)], media_types=["audio/wav"],
                )
                duplicate_key = (home, "42", "42", "message-audio-duplicate")
                with patch.object(tools, "_event_key", return_value=duplicate_key):
                    tools.inbound(duplicate_message, gateway)
                    with self.assertRaises(DomainError) as duplicate:
                        tools._job(store, "import_audio", {
                            "job_id": job_id, "rev": 2, "track_id": "music1", "kind": "music",
                        })
                    self.assertEqual(duplicate.exception.code, "AUDIO_TRACK_EXISTS")

                voice_caption = f"UMP VO {job_id} 2"
                voice_message = SimpleNamespace(
                    source=source, raw_message=SimpleNamespace(), message_id="message-voice-1",
                    text=voice_caption, message_type="voice", media_urls=[str(upload)], media_types=["audio/wav"],
                )
                voice_key = (home, "42", "42", "message-voice-1")
                upload.write_bytes(data)
                with patch.object(tools, "_event_key", return_value=voice_key):
                    tools.inbound(voice_message, gateway)
                    voice_result = tools._job(store, "revise", {
                        "job_id": job_id, "rev": 2, "import_voice": True,
                    })
                self.assertEqual(voice_result["rev"], 3)
                voice_asset = store.get_revision_payload(job_id, 3)["voice_asset"]
                self.assertEqual((root / "jobs" / job_id / "voice" / voice_asset).read_bytes(), data)

                unauthorized_key = (home, "42", "42", "message-unauthorized")
                unauthorized_message = SimpleNamespace(
                    source=source, raw_message=SimpleNamespace(), message_id="message-unauthorized",
                    text=caption, message_type="voice", media_urls=[str(upload)], media_types=["audio/wav"],
                )
                unauthorized_gateway = SimpleNamespace(
                    _is_user_authorized_for_source=lambda _source, allow_adapter_delegation=False: False,
                )
                with patch.object(tools, "_event_key", return_value=unauthorized_key):
                    tools.inbound(unauthorized_message, unauthorized_gateway)
                    with self.assertRaises(DomainError) as denied:
                        tools._authorization(caption, code="ATTACHMENT_REQUIRED")
                self.assertEqual(denied.exception.code, "ATTACHMENT_REQUIRED")
            with tools._EVENT_LOCK:
                tools._EVENTS.clear()


if __name__ == "__main__":
    unittest.main()
