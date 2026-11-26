"""Versioned, data-root asset library for custom compositions."""
from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import re
import stat
import struct
import tempfile
import zlib
from contextlib import contextmanager
from pathlib import Path
from xml.etree import ElementTree as ET

from .jobs import DomainError


_ID = re.compile(r"[a-z][a-z0-9_-]{0,47}\Z")
_MAX_ASSET_BYTES = 10 * 1024 * 1024
_MAX_IMAGE_PIXELS = 16_000_000
_MAX_LIBRARY_BYTES = 128 * 1024 * 1024
_MAX_ASSET_RECORDS = 256


def _catalog_record_valid(record: dict) -> bool:
    asset_id = record.get("id")
    version = record.get("version")
    asset_format = record.get("format")
    formats = {
        "svg": ("vector", "image/svg+xml", ".svg"),
        "png": ("raster", "image/png", ".png"),
        "jpeg": ("raster", "image/jpeg", ".jpeg"),
    }
    if not isinstance(asset_id, str) or not _ID.fullmatch(asset_id):
        return False
    if type(version) is not int or not 0 < version <= 2_147_483_647:
        return False
    if not isinstance(asset_format, str):
        return False
    expected = formats.get(asset_format)
    if expected is None or (record.get("type"), record.get("media_type")) != expected[:2]:
        return False
    if record.get("path") != f"assets/library/{asset_id}/v{version}{expected[2]}":
        return False
    digest = record.get("sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        return False
    size = record.get("size_bytes")
    width, height = record.get("width"), record.get("height")
    if type(size) is not int or not 0 < size <= _MAX_ASSET_BYTES:
        return False
    if (type(width) is not int or type(height) is not int or width < 1 or height < 1
            or width * height > _MAX_IMAGE_PIXELS):
        return False
    if asset_format == "svg" and (width > 4096 or height > 4096):
        return False
    limits = ((record.get("source"), 256), (record.get("provenance"), 1024),
              (record.get("license"), 256))
    if any(not isinstance(value, str) or not value or value != value.strip() or len(value) > maximum
           for value, maximum in limits):
        return False
    if type(record.get("locked")) is not bool:
        return False
    seed, parameters = record.get("seed"), record.get("parameters")
    if seed is not None and type(seed) is not int:
        return False
    if asset_format != "svg" and (seed is not None or parameters is not None):
        return False
    if parameters is not None:
        if not isinstance(parameters, dict):
            return False
        try:
            encoded = json.dumps(parameters, ensure_ascii=False, sort_keys=True,
                                 allow_nan=False, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError, RecursionError, UnicodeEncodeError):
            return False
        if len(encoded) > 16 * 1024:
            return False
    return True


def _png_dimensions(data: bytes) -> tuple[int, int]:
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise DomainError("INVALID_ASSET", "PNG signature required")
    offset = 8
    width = height = None
    bit_depth = color_type = interlace = None
    saw_idat = saw_iend = idat_closed = has_palette = False
    idat_parts = []
    while offset + 12 <= len(data):
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        kind = data[offset + 4:offset + 8]
        end = offset + 12 + length
        if length > _MAX_ASSET_BYTES or end > len(data):
            raise DomainError("INVALID_ASSET", "truncated PNG chunk")
        payload = data[offset + 8:offset + 8 + length]
        expected_crc = struct.unpack(">I", data[offset + 8 + length:end])[0]
        if zlib.crc32(kind + payload) & 0xffffffff != expected_crc:
            raise DomainError("INVALID_ASSET", "PNG chunk checksum mismatch")
        if offset == 8:
            if kind != b"IHDR" or length != 13:
                raise DomainError("INVALID_ASSET", "PNG must begin with IHDR")
            width, height = struct.unpack(">II", payload[:8])
            bit_depth, color_type, compression, filtering, interlace = payload[8:]
            valid_depths = {0: {1, 2, 4, 8, 16}, 2: {8, 16}, 3: {1, 2, 4, 8},
                            4: {8, 16}, 6: {8, 16}}
            if (color_type not in valid_depths or bit_depth not in valid_depths[color_type]
                    or compression != 0 or filtering != 0 or interlace not in (0, 1)):
                raise DomainError("INVALID_ASSET", "unsupported PNG header fields")
        elif kind == b"IHDR":
            raise DomainError("INVALID_ASSET", "duplicate PNG header")
        elif kind == b"PLTE":
            if saw_idat or has_palette or not length or length % 3 or length > 768:
                raise DomainError("INVALID_ASSET", "invalid PNG palette")
            has_palette = True
        elif kind == b"IDAT":
            if idat_closed:
                raise DomainError("INVALID_ASSET", "PNG IDAT chunks must be consecutive")
            saw_idat = True
            idat_parts.append(payload)
        elif kind == b"IEND":
            if length != 0 or not saw_idat:
                raise DomainError("INVALID_ASSET", "invalid PNG end chunk")
            saw_iend = True
            offset = end
            break
        else:
            if saw_idat:
                idat_closed = True
            if kind[0] & 0x20 == 0:
                raise DomainError("INVALID_ASSET", "unknown critical PNG chunk")
        if saw_idat and kind not in {b"IDAT", b"IEND"}:
            idat_closed = True
        offset = end
    if not saw_idat or not saw_iend or offset != len(data):
        raise DomainError("INVALID_ASSET", "incomplete PNG image")
    if color_type == 3 and not has_palette:
        raise DomainError("INVALID_ASSET", "indexed PNG requires a palette")
    if color_type in {0, 4} and has_palette:
        raise DomainError("INVALID_ASSET", "grayscale PNG cannot contain a palette")
    if width is None or height is None or bit_depth is None or color_type is None or interlace is None:
        raise DomainError("INVALID_ASSET", "PNG header is missing required fields")
    if width < 1 or height < 1 or width * height > _MAX_IMAGE_PIXELS:
        raise DomainError("INVALID_ASSET_DIMENSIONS", "PNG dimensions outside supported bounds")

    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
    bits_per_pixel = channels * bit_depth
    passes = ((0, 0, 1, 1),) if interlace == 0 else (
        (0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4),
        (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2),
    )
    rows = []
    expected_size = 0
    for x_start, y_start, x_step, y_step in passes:
        pass_width = max(0, (width - x_start + x_step - 1) // x_step)
        pass_height = max(0, (height - y_start + y_step - 1) // y_step)
        if pass_width and pass_height:
            stride = (pass_width * bits_per_pixel + 7) // 8 + 1
            rows.append((stride, pass_height))
            expected_size += stride * pass_height
    try:
        decompressor = zlib.decompressobj()
        raw = decompressor.decompress(b"".join(idat_parts), expected_size + 1)
    except zlib.error as exc:
        raise DomainError("INVALID_ASSET", "invalid PNG compressed image stream") from exc
    if (len(raw) != expected_size or not decompressor.eof or decompressor.unused_data
            or decompressor.unconsumed_tail):
        raise DomainError("INVALID_ASSET", "PNG scanline stream is incomplete or oversized")
    cursor = 0
    for stride, row_count in rows:
        for _ in range(row_count):
            if raw[cursor] > 4:
                raise DomainError("INVALID_ASSET", "invalid PNG scanline filter")
            cursor += stride
    return width, height


def _jpeg_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 4 or not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
        raise DomainError("INVALID_ASSET", "JPEG start/end markers required")
    offset = 2
    frame = None
    frame_components = set()
    saw_scan = False
    saw_entropy = False
    sof_markers = {0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7,
                   0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf}
    while offset < len(data):
        if data[offset] != 0xff:
            raise DomainError("INVALID_ASSET", "invalid JPEG marker sequence")
        marker_start = offset
        while offset < len(data) and data[offset] == 0xff:
            offset += 1
        if offset >= len(data):
            break
        marker = data[offset]
        offset += 1
        if marker == 0xd9:
            if frame is None or not saw_scan or not saw_entropy or offset != len(data):
                raise DomainError("INVALID_ASSET", "JPEG frame, scan, or end marker is missing")
            return frame
        if marker in {0x00, 0xd8, *range(0xd0, 0xd8)}:
            raise DomainError("INVALID_ASSET", "unexpected standalone JPEG marker")
        if offset + 2 > len(data):
            raise DomainError("INVALID_ASSET", "truncated JPEG segment")
        length = struct.unpack(">H", data[offset:offset + 2])[0]
        if length < 2 or offset + length > len(data):
            raise DomainError("INVALID_ASSET", "invalid JPEG segment length")
        payload = data[offset + 2:offset + length]
        offset += length
        if marker in sof_markers:
            if frame is not None or length < 11:
                raise DomainError("INVALID_ASSET", "invalid JPEG frame header")
            precision = payload[0]
            height = struct.unpack(">H", payload[1:3])[0]
            width = struct.unpack(">H", payload[3:5])[0]
            component_count = payload[5]
            if precision not in {8, 12} or not 1 <= component_count <= 4 or length != 8 + 3 * component_count:
                raise DomainError("INVALID_ASSET", "invalid JPEG frame components")
            if width == 0 or height == 0 or width * height > _MAX_IMAGE_PIXELS:
                raise DomainError("INVALID_ASSET_DIMENSIONS", "JPEG dimensions outside supported bounds")
            component_ids = set()
            for index in range(component_count):
                start = 6 + index * 3
                component_id, sampling, table = payload[start:start + 3]
                if (component_id in component_ids or not 1 <= sampling >> 4 <= 4
                        or not 1 <= sampling & 0x0f <= 4 or table > 3):
                    raise DomainError("INVALID_ASSET", "invalid JPEG frame component table")
                component_ids.add(component_id)
            frame = (width, height)
            frame_components = component_ids
        elif marker == 0xda:
            if frame is None or len(payload) < 4:
                raise DomainError("INVALID_ASSET", "JPEG scan precedes its frame")
            scan_components = payload[0]
            if (not 1 <= scan_components <= len(frame_components)
                    or length != 6 + 2 * scan_components):
                raise DomainError("INVALID_ASSET", "invalid JPEG scan header")
            scan_ids = [payload[1 + index * 2] for index in range(scan_components)]
            if len(set(scan_ids)) != len(scan_ids) or any(item not in frame_components for item in scan_ids):
                raise DomainError("INVALID_ASSET", "JPEG scan references unknown components")
            saw_scan = True
            entropy_start = offset
            while offset < len(data):
                if data[offset] != 0xff:
                    saw_entropy = True
                    offset += 1
                    continue
                marker_start = offset
                offset += 1
                while offset < len(data) and data[offset] == 0xff:
                    offset += 1
                if offset >= len(data):
                    break
                entropy_marker = data[offset]
                offset += 1
                if entropy_marker == 0x00 or entropy_marker in range(0xd0, 0xd8):
                    saw_entropy = True
                    continue
                offset = marker_start
                break
            if offset == entropy_start or not saw_entropy:
                raise DomainError("INVALID_ASSET", "JPEG scan contains no entropy data")
    raise DomainError("INVALID_ASSET", "JPEG frame, scan, or end marker not found")


def _asset_metadata(source, provenance, license):
    limits = ((source, 256), (provenance, 1024), (license, 256))
    if any(not isinstance(value, str) or not value.strip() or len(value) > maximum
           for value, maximum in limits):
        raise DomainError("INVALID_ASSET_METADATA", "source, provenance, or license exceeds its limit")
    return source.strip(), provenance.strip(), license.strip()


_CSS_ESCAPE = re.compile(
    r"\\([0-9a-fA-F]{1,6})(?:\r\n|[ \t\r\n\f])?"
    r"|\\(?:\r\n|[\r\n\f])"
    r"|\\(.)",
    re.DOTALL,
)


def _unescape_css(value: str) -> str:
    def replace(match):
        if match.group(1):
            codepoint = int(match.group(1), 16)
            if codepoint == 0 or codepoint > 0x10ffff or 0xd800 <= codepoint <= 0xdfff:
                return chr(0xfffd)
            return chr(codepoint)
        return match.group(2) or ""
    return _CSS_ESCAPE.sub(replace, value)


def _validated_svg_root(svg: str):
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", svg, re.IGNORECASE):
        raise DomainError("UNSAFE_ASSET", "SVG declarations and entities are not allowed")
    without_xml_declaration = re.sub(r"^\s*<\?xml\b.*?\?>", "", svg, count=1, flags=re.IGNORECASE | re.DOTALL)
    if "<?" in without_xml_declaration:
        raise DomainError("UNSAFE_ASSET", "SVG processing instructions are not allowed")
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as exc:
        raise DomainError("INVALID_ASSET", "SVG is not well-formed XML") from exc
    if root.tag.rsplit("}", 1)[-1].lower() != "svg":
        raise DomainError("INVALID_ASSET", "SVG root element required")
    blocked = {"script", "foreignobject", "animate", "animatemotion", "animatetransform", "set", "discard"}
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1].lower()
        if tag in blocked:
            raise DomainError("UNSAFE_ASSET", f"SVG element {tag} is not allowed")
        css_values = [element.text or ""] if tag == "style" else []
        for key, value in element.attrib.items():
            name = key.rsplit("}", 1)[-1].lower()
            content = value.strip()
            if re.fullmatch(r"on[a-z][a-z0-9_-]*", name):
                raise DomainError("UNSAFE_ASSET", "SVG event handlers are not allowed")
            if name in {"href", "src"} and content and not content.startswith("#"):
                raise DomainError("UNSAFE_ASSET", "SVG external references are not allowed")
            if name == "style" or "\\" in value or re.search(r"url\s*\(", value, re.IGNORECASE):
                css_values.append(value)
        for raw_css in css_values:
            css = _unescape_css(raw_css)
            if re.search(
                r"@import|expression\s*\(|@(?:-webkit-)?keyframes\b|\banimation(?:-[a-z-]+)?\s*:|\btransition(?:-[a-z-]+)?\s*:",
                css, re.IGNORECASE,
            ):
                raise DomainError("UNSAFE_ASSET", "SVG external, executable, or animated CSS is not allowed")
            openings = re.findall(r"url\s*\(", css, re.IGNORECASE)
            targets = re.findall(r"url\s*\(([^)]*)\)", css, re.IGNORECASE)
            if len(openings) != len(targets):
                raise DomainError("UNSAFE_ASSET", "malformed SVG CSS URL")
            for target in targets:
                if not target.strip().strip("\"'").strip().startswith("#"):
                    raise DomainError("UNSAFE_ASSET", "SVG external references are not allowed")
    return root


def _svg_dimensions(svg: str) -> tuple[int, int]:
    root = _validated_svg_root(svg)
    view_width = view_height = None
    view_box = root.get("viewBox") or root.get("viewbox")
    if view_box:
        try:
            _, _, view_width, view_height = (float(part) for part in re.split(r"[\s,]+", view_box.strip()))
        except (ValueError, TypeError) as exc:
            raise DomainError("INVALID_ASSET_DIMENSIONS", "invalid SVG viewBox") from exc
        if (not math.isfinite(view_width) or not math.isfinite(view_height) or view_width <= 0
                or view_height <= 0 or view_width > 4096 or view_height > 4096
                or math.ceil(view_width) * math.ceil(view_height) > _MAX_IMAGE_PIXELS):
            raise DomainError("INVALID_ASSET_DIMENSIONS", "SVG viewBox outside supported bounds")

    def parse_dimension(name):
        value = root.get(name)
        if value is None:
            return None
        match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(?:px)?", value.strip())
        if not match:
            raise DomainError("INVALID_ASSET_DIMENSIONS", f"SVG {name} must be a bounded pixel value")
        return float(match.group(1))

    declared_width, declared_height = parse_dimension("width"), parse_dimension("height")
    for value in (declared_width, declared_height):
        if value is not None and (not math.isfinite(value) or value <= 0 or value > 4096):
            raise DomainError("INVALID_ASSET_DIMENSIONS", "SVG declared dimensions outside supported bounds")
    if (declared_width is not None and declared_height is not None
            and math.ceil(declared_width) * math.ceil(declared_height) > _MAX_IMAGE_PIXELS):
        raise DomainError("INVALID_ASSET_DIMENSIONS", "SVG declared area outside supported bounds")

    width = declared_width if declared_width is not None else view_width
    height = declared_height if declared_height is not None else view_height
    if width is None or height is None:
        raise DomainError("INVALID_ASSET_DIMENSIONS", "SVG width/height or viewBox required")
    if (not math.isfinite(width) or not math.isfinite(height) or width <= 0 or height <= 0
            or math.ceil(width) * math.ceil(height) > _MAX_IMAGE_PIXELS):
        raise DomainError("INVALID_ASSET_DIMENSIONS", "SVG dimensions outside supported bounds")
    return math.ceil(width), math.ceil(height)


class AssetStore:
    def __init__(self, root: str | os.PathLike[str]):
        self.root = Path(root).resolve()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.asset_root = self.root / "assets"
        self.library = self.asset_root / "library"
        self.catalog_path = self.asset_root / "catalog.json"
        for directory in (self.asset_root, self.library):
            try:
                directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            except OSError as exc:
                raise DomainError("ASSET_PATH_INVALID", "asset storage directory unavailable") from exc
            if directory.is_symlink() or not directory.is_dir():
                raise DomainError("ASSET_PATH_INVALID", "asset storage directories must not be symlinks")
            os.chmod(directory, 0o700)
        with self._catalog_lock():
            if not self.catalog_path.exists():
                self._write_catalog([])

    def _write_catalog(self, records: list[dict]) -> None:
        self.asset_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".catalog-", suffix=".tmp", dir=self.asset_root)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump({"schema_version": 1, "assets": records}, stream,
                          sort_keys=True, ensure_ascii=False, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.catalog_path)
        except Exception:
            Path(temporary).unlink(missing_ok=True)
            raise

    def _records(self) -> list[dict]:
        descriptor = None
        try:
            descriptor = os.open(self.catalog_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise DomainError("ASSET_CATALOG_INVALID", "asset catalogue must be a regular file")
            stream = os.fdopen(descriptor, "r", encoding="utf-8")
            descriptor = None
            with stream:
                value = json.load(stream)
        except DomainError:
            raise
        except (OSError, ValueError, TypeError, UnicodeError, RecursionError) as exc:
            raise DomainError("ASSET_CATALOG_INVALID", "asset catalogue cannot be read") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
        if not isinstance(value, dict):
            raise DomainError("ASSET_CATALOG_INVALID", "unsupported or malformed asset catalogue")
        records = value.get("assets")
        required_fields = {
            "id", "version", "sha256", "type", "format", "media_type", "size_bytes",
            "width", "height", "source", "provenance", "license", "seed", "parameters",
            "locked", "path",
        }
        if (type(value.get("schema_version")) is not int or value["schema_version"] != 1
                or not isinstance(records, list)):
            raise DomainError("ASSET_CATALOG_INVALID", "unsupported or malformed asset catalogue")
        seen_versions = set()
        for record in records:
            if (not isinstance(record, dict) or not required_fields.issubset(record)
                    or not _catalog_record_valid(record)):
                raise DomainError("ASSET_CATALOG_INVALID", "asset catalogue record is malformed")
            key = (record["id"], record["version"])
            if key in seen_versions:
                raise DomainError("ASSET_CATALOG_INVALID", "asset catalogue contains duplicate versions")
            seen_versions.add(key)
        return records

    @contextmanager
    def _catalog_lock(self):
        try:
            self.asset_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError as exc:
            raise DomainError("ASSET_PATH_INVALID", "asset storage directory unavailable") from exc
        if any(path.is_symlink() or not path.is_dir() for path in (self.asset_root, self.library)):
            raise DomainError("ASSET_PATH_INVALID", "asset storage directories must not be symlinks")
        lock_path = self.asset_root / ".catalog.lock"
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(lock_path, flags, 0o600)
        except OSError as exc:
            raise DomainError("ASSET_CATALOG_INVALID", "asset catalogue lock cannot be opened") from exc
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise DomainError("ASSET_CATALOG_INVALID", "asset catalogue lock must be a regular file")
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    def _library_usage(self) -> tuple[int, int]:
        total_bytes = file_count = 0
        pending = [self.library]
        try:
            while pending:
                directory = pending.pop()
                with os.scandir(directory) as entries:
                    for entry in entries:
                        if entry.is_symlink():
                            raise DomainError("ASSET_PATH_INVALID", "asset library contains a symlink")
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            info = entry.stat(follow_symlinks=False)
                            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                                raise DomainError("ASSET_PATH_INVALID", "asset library contains an unsafe file")
                            total_bytes += info.st_size
                            file_count += 1
                        else:
                            raise DomainError("ASSET_PATH_INVALID", "asset library contains a special file")
        except DomainError:
            raise
        except OSError as exc:
            raise DomainError("ASSET_PATH_INVALID", "asset library cannot be inspected") from exc
        return total_bytes, file_count

    def _verify_blob(self, record: dict) -> None:
        path = self.root / record["path"]
        try:
            if (path.parent.is_symlink()
                    or not path.parent.resolve().is_relative_to(self.library.resolve())):
                raise DomainError("ASSET_INTEGRITY", "asset blob path escapes the library")
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except DomainError:
            raise
        except OSError as exc:
            raise DomainError("ASSET_INTEGRITY", "asset blob is missing or unsafe") from exc
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_nlink != 1 or info.st_size != record["size_bytes"]
                    or stat.S_IMODE(info.st_mode) != 0o600):
                raise DomainError("ASSET_INTEGRITY", "asset blob type, owner, mode, or size changed")
            digest = hashlib.sha256()
            while chunk := stream.read(64 * 1024):
                digest.update(chunk)
        if digest.hexdigest() != record["sha256"]:
            raise DomainError("ASSET_INTEGRITY", "asset blob hash changed")

    def _publish(self, template: dict, encoded: bytes, extension: str) -> dict:
        asset_id = template["id"]
        with self._catalog_lock():
            records = self._records()
            prior = [item for item in records if item.get("id") == asset_id]
            latest = max(prior, key=lambda item: item.get("version", 0), default=None)
            if latest:
                self._verify_blob(latest)
            if latest and latest.get("locked") is True:
                if latest.get("sha256") != template["sha256"]:
                    raise DomainError("ASSET_LOCKED", "locked asset bytes cannot be changed")
                template = {**template, "locked": True}
            version = latest["version"] + 1 if latest else 1
            if latest:
                same_record = all(
                    latest.get(key) == value for key, value in template.items()
                    if key != "locked"
                ) and latest.get("locked") == template.get("locked", False)
                if same_record:
                    return latest
            try:
                json.dumps(template, ensure_ascii=False, allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise DomainError("INVALID_ASSET_METADATA", "asset metadata must be JSON") from exc
            library_bytes, library_files = self._library_usage()
            for _ in range(_MAX_ASSET_RECORDS + 1):
                record = {**template, "version": version,
                          "path": f"assets/library/{asset_id}/v{version}{extension}"}
                destination = self.root / record["path"]
                if not destination.exists() and not destination.is_symlink():
                    if (len(records) >= _MAX_ASSET_RECORDS or library_files >= _MAX_ASSET_RECORDS
                            or library_bytes + len(encoded) > _MAX_LIBRARY_BYTES):
                        raise DomainError("ASSET_LIBRARY_FULL", "asset library quota reached")
                    break
                try:
                    self._verify_blob(record)
                except DomainError as exc:
                    if exc.code != "ASSET_INTEGRITY":
                        raise
                    version += 1
                    continue
                if len(records) >= _MAX_ASSET_RECORDS:
                    raise DomainError("ASSET_LIBRARY_FULL", "asset library version limit reached")
                self._write_catalog([*records, record])
                return record
            else:
                raise DomainError("ASSET_CONFLICT", "too many orphan asset versions")
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if destination.parent.is_symlink() or not destination.parent.resolve().is_relative_to(self.library.resolve()):
                raise DomainError("ASSET_PATH_INVALID", "asset destination escapes the library")
            os.chmod(destination.parent, 0o700)
            descriptor, temporary = tempfile.mkstemp(prefix=f".{destination.name}-", suffix=".tmp",
                                                       dir=destination.parent)
            published = False
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, destination)
                published = True
                self._write_catalog([*records, record])
            except Exception:
                Path(temporary).unlink(missing_ok=True)
                if published:
                    destination.unlink(missing_ok=True)
                raise
            return record

    def add_svg(self, asset_id: str, svg: str, *, source: str, provenance: str,
                license: str, seed: int | None = None, parameters=None,
                locked: bool = False) -> dict:
        if not isinstance(asset_id, str) or not _ID.fullmatch(asset_id):
            raise DomainError("INVALID_ASSET_ID", "use a lowercase asset ID")
        source, provenance, license = _asset_metadata(source, provenance, license)
        if type(locked) is not bool:
            raise DomainError("INVALID_ASSET_METADATA", "locked must be boolean")
        if seed is not None and type(seed) is not int:
            raise DomainError("INVALID_ASSET_METADATA", "seed must be an integer")
        if parameters is not None:
            if not isinstance(parameters, dict):
                raise DomainError("INVALID_ASSET_METADATA", "parameters must be an object")
            try:
                encoded_parameters = json.dumps(
                    parameters, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":")
                ).encode("utf-8")
            except (TypeError, ValueError, RecursionError, UnicodeEncodeError) as exc:
                raise DomainError("INVALID_ASSET_METADATA", "parameters must be finite JSON") from exc
            if len(encoded_parameters) > 16 * 1024:
                raise DomainError("INVALID_ASSET_METADATA", "parameters exceed 16 KiB")
            parameters = json.loads(encoded_parameters)
        try:
            encoded = svg.encode("utf-8", "strict")
        except (AttributeError, UnicodeEncodeError) as exc:
            raise DomainError("INVALID_ASSET", "SVG must be UTF-8 text") from exc
        if not encoded:
            raise DomainError("INVALID_ASSET", "SVG must not be empty")
        if len(encoded) > _MAX_ASSET_BYTES:
            raise DomainError("ASSET_TOO_LARGE", "asset exceeds 10 MiB")
        width, height = _svg_dimensions(svg)
        digest = hashlib.sha256(encoded).hexdigest()
        template = {
            "id": asset_id,
            "sha256": digest,
            "type": "vector",
            "format": "svg",
            "media_type": "image/svg+xml",
            "size_bytes": len(encoded),
            "width": width,
            "height": height,
            "source": source.strip(),
            "provenance": provenance.strip(),
            "license": license.strip(),
            "seed": seed,
            "parameters": parameters,
            "locked": locked,
        }
        return self._publish(template, encoded, ".svg")

    def add_image(self, asset_id: str, data: bytes, filename: str, *, source: str,
                  provenance: str, license: str, locked: bool = False) -> dict:
        if not isinstance(asset_id, str) or not _ID.fullmatch(asset_id):
            raise DomainError("INVALID_ASSET_ID", "use a lowercase asset ID")
        source, provenance, license = _asset_metadata(source, provenance, license)
        if type(locked) is not bool:
            raise DomainError("INVALID_ASSET_METADATA", "locked must be boolean")
        suffix = Path(filename).suffix.lower() if isinstance(filename, str) else ""
        if not isinstance(filename, str) or Path(filename).name != filename or suffix not in {".png", ".jpg", ".jpeg"}:
            raise DomainError("INVALID_ASSET", "use a plain .png, .jpg, or .jpeg filename")
        if not isinstance(data, (bytes, bytearray)):
            raise DomainError("INVALID_ASSET", "image content must be bytes")
        encoded = bytes(data)
        if not encoded:
            raise DomainError("INVALID_ASSET", "image must not be empty")
        if len(encoded) > _MAX_ASSET_BYTES:
            raise DomainError("ASSET_TOO_LARGE", "asset exceeds 10 MiB")
        if suffix == ".png":
            width, height = _png_dimensions(encoded)
            asset_format, media_type, storage_suffix = "png", "image/png", ".png"
        else:
            width, height = _jpeg_dimensions(encoded)
            asset_format, media_type, storage_suffix = "jpeg", "image/jpeg", ".jpeg"
        digest = hashlib.sha256(encoded).hexdigest()
        template = {
            "id": asset_id,
            "sha256": digest,
            "type": "raster",
            "format": asset_format,
            "media_type": media_type,
            "size_bytes": len(encoded),
            "width": width,
            "height": height,
            "source": source.strip(),
            "provenance": provenance.strip(),
            "license": license.strip(),
            "seed": None,
            "parameters": None,
            "locked": locked,
        }
        return self._publish(template, encoded, storage_suffix)

    def list(self) -> list[dict]:
        with self._catalog_lock():
            return self._records()
