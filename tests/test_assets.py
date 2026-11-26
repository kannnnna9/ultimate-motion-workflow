import base64
import hashlib
import json
import stat
import struct
import sys
import tempfile
import threading
import types
import unittest
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from ump_core import tools
import ump_core.assets as asset_module

from ump_core.assets import AssetStore, _unescape_css
from ump_core.jobs import DomainError


SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="8" '
    'viewBox="0 0 10 8"><path d="M0 0h10v8H0z" fill="#345678"/></svg>'
)
PNG_2X3 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAADCAIAAAA2iEnWAAAAFElEQVR4nGMUDJ7KwMDAxMDAgKAAEksA/8TvPSQAAAAASUVORK5CYII="
)
JPEG_2X3 = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDABsSFBcUERsXFhceHBsgKEIrKCUlKFE6PTBCYFVlZF9VXVtqeJmBanGQc1tdhbWGkJ6jq62rZ4C8ybqmx5moq6T/2wBDARweHigjKE4rK06kbl1upKSkpKSkpKSkpKSkpKSkpKSkpKSkpKSkpKSkpKSkpKSkpKSkpKSkpKSkpKSkpKSkpKT/wAARCAADAAIDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAX/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFAEBAAAAAAAAAAAAAAAAAAAABP/EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAMAwEAAhEDEQA/AJwBoj//2Q=="
)

def _png_chunk(kind, payload):
    return (struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff))


EMPTY_IDAT_PNG = (
    b"\x89PNG\r\n\x1a\n"
    + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
    + _png_chunk(b"IDAT", b"")
    + _png_chunk(b"IEND", b"")
)


class AssetStoreTests(unittest.TestCase):
    def test_asset_metadata_has_schema_limits_and_catalogue_budget(self):
        cases = [
            ({"source": "s" * 257}, "source"),
            ({"parameters": {"prompt": "x" * 16385}}, "parameters"),
        ]
        for overrides, name in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                store = AssetStore(Path(tmp))
                values = {"source": "generated", "provenance": "original", "license": "MIT",
                          "parameters": None}
                values.update(overrides)
                with self.assertRaises(DomainError) as raised:
                    store.add_svg("metadata", SVG, **values)
                self.assertEqual(raised.exception.code, "INVALID_ASSET_METADATA")
                self.assertEqual(store.list(), [])

    def test_first_catalogue_initialization_cannot_erase_concurrent_asset(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_waiting = threading.Event()
            release_first = threading.Event()
            second_done = threading.Event()
            original_write = AssetStore._write_catalog

            def controlled_write(instance, records):
                if not records and threading.current_thread().name == "asset-init-first":
                    first_waiting.set()
                    release_first.wait(timeout=3)
                return original_write(instance, records)

            def register(asset_id, name):
                store = AssetStore(root)
                store.add_svg(asset_id, SVG, source="test", provenance=name, license="MIT")
                if name == "second":
                    second_done.set()

            with patch.object(AssetStore, "_write_catalog", new=controlled_write):
                first = threading.Thread(target=register, args=("first", "first"), name="asset-init-first")
                first.start()
                self.assertTrue(first_waiting.wait(timeout=2))
                second = threading.Thread(target=register, args=("second", "second"), name="asset-init-second")
                second.start()
                second_done.wait(timeout=0.5)
                release_first.set()
                first.join(timeout=3)
                second.join(timeout=3)
            self.assertFalse(first.is_alive())
            self.assertFalse(second.is_alive())
            self.assertEqual({item["id"] for item in AssetStore(root).list()}, {"first", "second"})

    def test_current_image_reader_rejects_symlinks_and_reads_cache_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "profile"
            cache = home / "cache" / "images"
            cache.mkdir(parents=True)
            image = cache / "img_012345abcdef.png"
            outside = Path(tmp) / "outside.png"
            outside.write_bytes(PNG_2X3)
            image.symlink_to(outside)
            base = types.ModuleType("gateway.platforms.base")
            base.get_image_cache_dir = lambda: cache
            gateway = types.ModuleType("gateway")
            gateway.__path__ = []
            platforms = types.ModuleType("gateway.platforms")
            platforms.__path__ = []
            modules = {"gateway": gateway, "gateway.platforms": platforms,
                       "gateway.platforms.base": base}
            with patch.dict(sys.modules, modules), patch.object(tools, "_home", return_value=home):
                with self.assertRaises(DomainError) as raised:
                    tools._read_image_attachment(image)
                self.assertEqual(raised.exception.code, "PATH_OUTSIDE_ROOT")
                image.unlink()
                image.write_bytes(PNG_2X3)
                data, suffix = tools._read_image_attachment(image)
            self.assertEqual((data, suffix), (PNG_2X3, ".png"))

    def test_store_rejects_asset_root_replacement_by_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            outside = Path(tmp) / "outside"
            store = AssetStore(root)
            outside.mkdir()
            (outside / "catalog.json").write_text(
                '{"schema_version":1,"assets":[]}', encoding="utf-8"
            )
            (root / "assets").rename(root / "assets-original")
            (root / "assets").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(DomainError) as raised:
                store.list()
            self.assertEqual(raised.exception.code, "ASSET_PATH_INVALID")
            self.assertFalse((outside / ".catalog.lock").exists())

    def test_catalogue_refuses_symlinked_catalog_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            outside = Path(tmp) / "outside.json"
            store = AssetStore(root)
            outside.write_text('{"schema_version":1,"assets":[]}', encoding="utf-8")
            store.catalog_path.unlink()
            store.catalog_path.symlink_to(outside)
            with self.assertRaises(DomainError) as raised:
                store.list()
            self.assertEqual(raised.exception.code, "ASSET_CATALOG_INVALID")

    def test_catalogue_rejects_invalid_record_types_and_paths(self):
        cases = (("version", "bad"), ("path", "../../etc/hostname"), ("sha256", "not-a-digest"))
        for field, value in cases:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                store = AssetStore(Path(tmp))
                store.add_svg("valid", SVG, source="test", provenance="test", license="MIT")
                catalog = json.loads(store.catalog_path.read_text(encoding="utf-8"))
                catalog["assets"][0][field] = value
                store.catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
                with self.assertRaises(DomainError) as raised:
                    store.list()
                self.assertEqual(raised.exception.code, "ASSET_CATALOG_INVALID")
                with self.assertRaises(DomainError) as write_raised:
                    store.add_svg("next", SVG, source="test", provenance="test", license="MIT")
                self.assertEqual(write_raised.exception.code, "ASSET_CATALOG_INVALID")

    def test_catalogue_rejects_records_missing_required_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AssetStore(Path(tmp))
            store.catalog_path.write_text(
                '{"schema_version":1,"assets":[{}]}', encoding="utf-8"
            )
            with self.assertRaises(DomainError) as raised:
                store.list()
            self.assertEqual(raised.exception.code, "ASSET_CATALOG_INVALID")

    def test_catalogue_rejects_malformed_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AssetStore(Path(tmp))
            store.catalog_path.write_text(
                '{"schema_version":1,"assets":[null]}', encoding="utf-8"
            )
            with self.assertRaises(DomainError) as raised:
                store.list()
            self.assertEqual(raised.exception.code, "ASSET_CATALOG_INVALID")

    def test_constructor_refuses_symlinked_asset_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            outside = Path(tmp) / "outside"
            root.mkdir()
            outside.mkdir()
            (root / "assets").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(DomainError) as raised:
                AssetStore(root)
            self.assertEqual(raised.exception.code, "ASSET_PATH_INVALID")
            self.assertEqual(list(outside.iterdir()), [])

    def test_catalogue_directories_and_asset_files_are_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = AssetStore(root)
            record = store.add_svg(
                "private", SVG, source="test", provenance="test", license="MIT",
            )
            expected_modes = {
                root / "assets": 0o700,
                root / "assets" / "library": 0o700,
                root / "assets" / "library" / "private": 0o700,
                root / "assets" / "catalog.json": 0o600,
                root / record["path"]: 0o600,
            }
            for path, expected in expected_modes.items():
                with self.subTest(path=path):
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), expected)

    def test_svg_rejects_traversal_ids_and_unbounded_dimensions(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AssetStore(Path(tmp))
            with self.assertRaises(DomainError) as raised:
                store.add_svg("../outside", SVG, source="test", provenance="test", license="MIT")
            self.assertEqual(raised.exception.code, "INVALID_ASSET_ID")
            for svg in (
                '<svg xmlns="http://www.w3.org/2000/svg"/>',
                '<svg xmlns="http://www.w3.org/2000/svg" width="5000" height="1"/>',
                '<svg xmlns="http://www.w3.org/2000/svg" width="100000" height="100000" viewBox="0 0 10 10"/>',
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4096 4096"/>',
            ):
                with self.subTest(svg=svg):
                    with self.assertRaises(DomainError) as raised:
                        store.add_svg("bounded", svg, source="test", provenance="test", license="MIT")
                    self.assertEqual(raised.exception.code, "INVALID_ASSET_DIMENSIONS")
            self.assertEqual(store.list(), [])

    def test_raster_rejects_bad_signature_crc_dimensions_and_size(self):
        zero_dimensions = bytearray(PNG_2X3)
        struct.pack_into(">I", zero_dimensions, 16, 0)
        struct.pack_into(">I", zero_dimensions, 29, zlib.crc32(zero_dimensions[12:29]) & 0xffffffff)
        oversized_dimensions = bytearray(PNG_2X3)
        struct.pack_into(">II", oversized_dimensions, 16, 16001, 1000)
        struct.pack_into(">I", oversized_dimensions, 29, zlib.crc32(oversized_dimensions[12:29]) & 0xffffffff)
        bad_crc = bytearray(PNG_2X3)
        bad_crc[20] ^= 1
        cases = (
            (b"not an image", "bad.png", "INVALID_ASSET"),
            (bytes(zero_dimensions), "zero.png", "INVALID_ASSET_DIMENSIONS"),
            (bytes(oversized_dimensions), "large.png", "INVALID_ASSET_DIMENSIONS"),
            (EMPTY_IDAT_PNG, "empty-idat.png", "INVALID_ASSET"),
            (bytes(bad_crc), "crc.png", "INVALID_ASSET"),
            (PNG_2X3[:-10], "truncated.png", "INVALID_ASSET"),
            (b"x" * (10 * 1024 * 1024 + 1), "oversize.png", "ASSET_TOO_LARGE"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = AssetStore(Path(tmp))
            for data, filename, code in cases:
                with self.subTest(filename=filename):
                    with self.assertRaises(DomainError) as raised:
                        store.add_image(
                            filename.split(".")[0], data, filename, source="test",
                            provenance="test", license="MIT",
                        )
                    self.assertEqual(raised.exception.code, code)
            self.assertEqual(store.list(), [])

    def test_asset_library_version_count_is_capped(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AssetStore(Path(tmp))
            with patch.object(asset_module, "_MAX_ASSET_RECORDS", 1):
                store.add_svg("first", SVG, source="test", provenance="first", license="MIT")
                with self.assertRaises(DomainError) as raised:
                    store.add_svg("second", SVG, source="test", provenance="second", license="MIT")
                self.assertEqual(raised.exception.code, "ASSET_LIBRARY_FULL")
                self.assertEqual([item["id"] for item in store.list()], ["first"])

    def test_asset_library_quota_rejects_additional_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AssetStore(Path(tmp))
            with patch.object(asset_module, "_MAX_LIBRARY_BYTES", len(SVG.encode()), create=True):
                store.add_svg("first", SVG, source="test", provenance="first", license="MIT")
                with self.assertRaises(DomainError) as raised:
                    store.add_svg("second", SVG, source="test", provenance="second", license="MIT")
                self.assertEqual(raised.exception.code, "ASSET_LIBRARY_FULL")
                self.assertEqual([item["id"] for item in store.list()], ["first"])

    def test_asset_library_quota_counts_orphan_blobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = AssetStore(root)
            with patch.object(asset_module, "_MAX_LIBRARY_BYTES", len(SVG.encode()), create=True):
                with patch.object(store, "_write_catalog", side_effect=KeyboardInterrupt):
                    with self.assertRaises(KeyboardInterrupt):
                        store.add_svg("orphan", SVG, source="test", provenance="orphan", license="MIT")
                with self.assertRaises(DomainError) as raised:
                    AssetStore(root).add_svg("another", SVG, source="test", provenance="new", license="MIT")
                self.assertEqual(raised.exception.code, "ASSET_LIBRARY_FULL")

    def test_svg_rejects_over_limit_encoded_content(self):
        oversized_svg = '<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1">' + (
            " " * (10 * 1024 * 1024)
        ) + "</svg>"
        with tempfile.TemporaryDirectory() as tmp:
            store = AssetStore(Path(tmp))
            with self.assertRaises(DomainError) as raised:
                store.add_svg(
                    "too-large", oversized_svg, source="test", provenance="test", license="MIT",
                )
            self.assertEqual(raised.exception.code, "ASSET_TOO_LARGE")

    def test_concurrent_writers_allocate_distinct_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stores = [AssetStore(root), AssetStore(root)]
            barrier = threading.Barrier(2)
            payloads = [SVG.replace("#345678", "#112233"), SVG.replace("#345678", "#aabbcc")]

            def register(index):
                barrier.wait()
                return stores[index].add_svg(
                    "shared", payloads[index], source="test", provenance=f"writer-{index}",
                    license="MIT", locked=False,
                )

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(register, range(2)))
            self.assertEqual(sorted(item["version"] for item in results), [1, 2])
            self.assertEqual([item["version"] for item in stores[0].list()], [1, 2])

    def test_css_hex_escape_consumes_crlf_terminator(self):
        self.assertEqual(_unescape_css(r"\75" + "\r\n" + "rl"), "url")
        self.assertEqual(_unescape_css(r"\0"), "\ufffd")

    def test_svg_rejects_active_external_and_animated_content(self):
        malicious = [
            '<script>alert(1)</script>',
            '<foreignObject><div>external HTML</div></foreignObject>',
            '<image href="https://example.test/image.png"/>',
            '<rect fill="url(https://example.test/external.svg#paint)"/>',
            '<g onclick="alert(1)"/>',
            '<style>.x{fill:url(https://example.test/p.svg#x)}</style>',
            '<style>.x{fill:u\\72l(https://example.test/x.svg#paint)}</style>',
            ('<style>.x{fill:u' + '\\' + '\n'
             + 'rl(https://example.test/x.svg#paint)}</style>'),
            ('<style>.x{fill:u' + '\\' + '\r\n'
             + 'rl(https://example.test/x.svg#paint)}</style>'),
            ('<style>.x{fill:' + chr(92) + '75' + '\r\n'
             + 'rl(https://example.test/x.svg#paint)}</style>'),
            '<rect fill="u\\72l(file:///etc/hostname)"/>',
            '<animate attributeName="x" from="0" to="1"/>',
            '<style>@keyframes blink{to{opacity:0}}.x{animation:blink 1s infinite}</style>',
            '<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/hostname">]><text>&x;</text>',
        ]
        with tempfile.TemporaryDirectory() as tmp:
            store = AssetStore(Path(tmp))
            for body in malicious:
                svg = (
                    '<svg xmlns="http://www.w3.org/2000/svg" width="8" height="6" '
                    'viewBox="0 0 8 6">' + body + '</svg>'
                )
                with self.subTest(body=body[:30]):
                    with self.assertRaises(DomainError) as raised:
                        store.add_svg(
                            "unsafe", svg, source="test", provenance="test", license="test",
                        )
                    self.assertEqual(raised.exception.code, "UNSAFE_ASSET")

    def test_jpeg_rejects_frame_without_components_or_scan(self):
        malformed = bytes.fromhex("ffd8ffc00008080001000100ffd9")
        with tempfile.TemporaryDirectory() as tmp:
            store = AssetStore(Path(tmp))
            with self.assertRaises(DomainError) as raised:
                store.add_image(
                    "malformed", malformed, "malformed.jpg", source="test",
                    provenance="test", license="MIT",
                )
            self.assertEqual(raised.exception.code, "INVALID_ASSET")
            self.assertEqual(store.list(), [])

    def test_jpeg_import_detects_dimensions_and_canonical_mime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = AssetStore(root)
            record = store.add_image(
                "wing", JPEG_2X3, "wing.jpg", source="operator-upload",
                provenance="operator supplied raster", license="operator-declared", locked=False,
            )
            self.assertEqual((record["type"], record["format"], record["media_type"]),
                             ("raster", "jpeg", "image/jpeg"))
            self.assertEqual((record["width"], record["height"]), (2, 3))
            self.assertEqual(record["sha256"], hashlib.sha256(JPEG_2X3).hexdigest())
            self.assertTrue((root / record["path"]).is_file())

    def test_png_import_detects_dimensions_and_records_operator_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = AssetStore(root)
            record = store.add_image(
                "leaf", PNG_2X3, "leaf.png", source="operator-upload",
                provenance="operator supplied image", license="operator-declared", locked=True,
            )
            self.assertEqual((record["type"], record["format"], record["media_type"]),
                             ("raster", "png", "image/png"))
            self.assertEqual((record["width"], record["height"]), (2, 3))
            self.assertEqual(record["sha256"], hashlib.sha256(PNG_2X3).hexdigest())
            self.assertTrue(record["locked"])
            self.assertEqual((root / record["path"]).read_bytes(), PNG_2X3)

    def test_svg_record_contains_seed_provenance_hash_and_safe_relative_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = AssetStore(root)
            record = store.add_svg(
                "moth-wing", SVG, source="hermes-procedural",
                provenance="original geometry authored for this project",
                license="MIT", seed=23, parameters={"palette": ["#345678"]}, locked=False,
            )
            self.assertEqual(record["id"], "moth-wing")
            self.assertEqual(record["version"], 1)
            self.assertEqual(record["type"], "vector")
            self.assertEqual(record["format"], "svg")
            self.assertEqual((record["width"], record["height"]), (10, 8))
            self.assertEqual(record["source"], "hermes-procedural")
            self.assertEqual(record["provenance"], "original geometry authored for this project")
            self.assertEqual(record["license"], "MIT")
            self.assertEqual(record["seed"], 23)
            self.assertEqual(record["parameters"], {"palette": ["#345678"]})
            self.assertFalse(record["locked"])
            self.assertEqual(record["sha256"], hashlib.sha256(SVG.encode()).hexdigest())
            self.assertEqual(record["path"], "assets/library/moth-wing/v1.svg")
            self.assertEqual((root / record["path"]).read_bytes(), SVG.encode())
            self.assertEqual(store.list(), [record])

    def test_identical_registration_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AssetStore(Path(tmp))
            first = store.add_svg(
                "leaf", SVG, source="hermes-procedural", provenance="original",
                license="MIT", locked=False,
            )
            repeated = store.add_svg(
                "leaf", SVG, source="hermes-procedural", provenance="original",
                license="MIT", locked=False,
            )
            self.assertEqual(repeated, first)
            self.assertEqual(store.list(), [first])

    def test_interrupted_conflicting_orphan_is_preserved_and_version_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = AssetStore(root)
            with patch.object(store, "_write_catalog", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    store.add_svg("recovery", SVG, source="test", provenance="old", license="MIT")
            changed = SVG.replace("#345678", "#abcdef")
            recovered = AssetStore(root).add_svg(
                "recovery", changed, source="test", provenance="new", license="MIT",
            )
            self.assertEqual(recovered["version"], 2)
            self.assertEqual((root / "assets/library/recovery/v1.svg").read_bytes(), SVG.encode())
            self.assertEqual((root / recovered["path"]).read_bytes(), changed.encode())
            self.assertEqual(AssetStore(root).list(), [recovered])

    def test_interrupted_catalogue_commit_recovers_same_hash_orphan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = AssetStore(root)
            with patch.object(store, "_write_catalog", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    store.add_svg(
                        "recovery", SVG, source="generated", provenance="original", license="MIT",
                    )
            recovered = AssetStore(root).add_svg(
                "recovery", SVG, source="generated", provenance="original", license="MIT",
            )
            self.assertEqual(recovered["version"], 1)
            self.assertEqual(store.list(), [recovered])
            self.assertEqual((root / recovered["path"]).read_bytes(), SVG.encode())

    def test_idempotent_registration_rejects_missing_or_tampered_blob(self):
        for corruption in ("missing", "tampered"):
            with self.subTest(corruption=corruption), tempfile.TemporaryDirectory() as tmp:
                store = AssetStore(Path(tmp))
                original = store.add_svg(
                    "leaf", SVG, source="generated", provenance="original", license="MIT",
                )
                blob = store.root / original["path"]
                if corruption == "missing":
                    blob.unlink()
                else:
                    blob.write_bytes(b"tampered")
                with self.assertRaises(DomainError) as raised:
                    store.add_svg(
                        "leaf", SVG, source="generated", provenance="original", license="MIT",
                    )
                self.assertEqual(raised.exception.code, "ASSET_INTEGRITY")
                self.assertEqual(store.list(), [original])

    def test_locked_asset_allows_metadata_correction_as_a_locked_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = AssetStore(root)
            original = store.add_svg(
                "locked-leaf", SVG, source="provider", provenance="old description",
                license="MIT", locked=True,
            )
            corrected = store.add_svg(
                "locked-leaf", SVG, source="provider", provenance="corrected description",
                license="MIT", locked=False,
            )
            self.assertEqual((original["version"], corrected["version"]), (1, 2))
            self.assertEqual(original["sha256"], corrected["sha256"])
            self.assertTrue(corrected["locked"])
            self.assertEqual((root / original["path"]).read_bytes(), (root / corrected["path"]).read_bytes())
            self.assertEqual(store.list(), [original, corrected])

    def test_locked_latest_version_rejects_changed_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AssetStore(Path(tmp))
            locked = store.add_svg(
                "leaf", SVG, source="operator-upload", provenance="approved asset",
                license="operator-declared", locked=True,
            )
            before = store.list()
            with self.assertRaises(DomainError) as raised:
                store.add_svg(
                    "leaf", SVG.replace("#345678", "#abcdef"), source="operator-upload",
                    provenance="changed content", license="operator-declared", locked=False,
                )
            self.assertEqual(raised.exception.code, "ASSET_LOCKED")
            self.assertEqual(store.list(), before)
            self.assertTrue((store.root / locked["path"]).is_file())

    def test_changed_unlocked_asset_creates_a_new_immutable_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = AssetStore(root)
            original = store.add_svg(
                "leaf", SVG, source="hermes-procedural", provenance="original",
                license="MIT", locked=False,
            )
            changed = SVG.replace("#345678", "#abcdef")
            next_version = store.add_svg(
                "leaf", changed, source="hermes-procedural", provenance="revised",
                license="MIT", locked=False,
            )
            self.assertEqual((original["version"], next_version["version"]), (1, 2))
            self.assertEqual((root / original["path"]).read_bytes(), SVG.encode())
            self.assertEqual((root / next_version["path"]).read_bytes(), changed.encode())
            self.assertEqual([item["version"] for item in store.list()], [1, 2])


if __name__ == "__main__":
    unittest.main()
