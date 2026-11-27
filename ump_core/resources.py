"""Per-render disk and host-resource probes for the shared VM budget."""
from __future__ import annotations

import os
import resource as process_resource
import shutil
from pathlib import Path
from typing import Any

from scripts.asr.preflight import (
    DATA_ROOT_HARD_BYTES,
    DATA_ROOT_SOFT_BYTES,
    REPORT_RESERVE_BYTES,
    ROOT_RESERVE_BYTES,
)

from .composition import (
    MAX_COMPOSITION_BYTES,
    MAX_PINNED_ASSET_BYTES,
    MAX_PINNED_AUDIO_BYTES,
    MAX_PROJECT_BYTES,
    MAX_REVISION_BYTES,
)

MIB = 1024 ** 2
MAX_OUTPUT_BYTES = 512 * MIB
CACHE_RESERVE_BYTES = 256 * MIB
BROWSER_TEMP_RESERVE_BYTES = 128 * MIB


def _physical_memory_bytes() -> int:
    try:
        return int(os.sysconf("SC_PAGE_SIZE")) * int(os.sysconf("SC_PHYS_PAGES"))
    except (AttributeError, OSError, ValueError):
        return 0


def _physical_available_bytes() -> int:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    try:
        return int(os.sysconf("SC_PAGE_SIZE")) * int(os.sysconf("SC_AVPHYS_PAGES"))
    except (AttributeError, OSError, ValueError):
        return _physical_memory_bytes()


def worker_peak_memory_bytes() -> int | None:
    """Return this worker process's lifetime RSS high-water mark, not child RSS."""
    try:
        value = process_resource.getrusage(process_resource.RUSAGE_SELF).ru_maxrss
    except (AttributeError, OSError, ValueError):
        return None
    # Linux reports ru_maxrss in KiB.
    return int(value * 1024)


def _parse_cpu_set_count(value: str) -> int | None:
    intervals = []
    try:
        for field in value.split(','):
            bounds = field.split('-', 1)
            start = int(bounds[0])
            end = int(bounds[1]) if len(bounds) == 2 else start
            if start < 0 or end < start:
                return None
            intervals.append((start, end))
    except (ValueError, IndexError):
        return None
    if not intervals:
        return None
    intervals.sort()
    total = 0
    first, last = intervals[0]
    for start, end in intervals[1:]:
        if start <= last + 1:
            last = max(last, end)
        else:
            total += last - first + 1
            first, last = start, end
    return total + last - first + 1


def _cgroup_limits(cgroup_root: str | os.PathLike[str], membership: str | None) -> dict[str, Any]:
    """Read finite v2 limits from the leaf and every visible ancestor."""
    result: dict[str, Any] = {
        "memory_limit_bytes": None,
        "memory_current_bytes": None,
        "memory_peak_bytes": None,
        "memory_headroom_bytes": None,
        "memory_headroom_complete": False,
        "cpu_limit_cores": None,
        "cpuset_limit_cores": None,
        "errors": [],
    }
    if not isinstance(membership, str) or not membership.startswith("/"):
        return result
    relative = Path(membership.lstrip("/"))
    if ".." in relative.parts:
        result["errors"].append("invalid cgroup membership path")
        return result

    root = Path(cgroup_root)
    leaf_to_root = [root.joinpath(*relative.parts[:count]) for count in range(len(relative.parts), -1, -1)]
    memory_limits: list[int] = []
    memory_headrooms: list[int] = []
    missing_limited_current = False
    cpu_limits: list[float] = []
    cpuset_limits: list[int] = []

    def read(directory: Path, filename: str) -> str | None:
        try:
            return (directory / filename).read_text(encoding="ascii").strip()
        except FileNotFoundError:
            return None
        except OSError:
            result["errors"].append(f"unreadable {filename}")
            return None

    for index, directory in enumerate(leaf_to_root):
        memory_max = read(directory, "memory.max")
        finite_memory_limit = None
        if memory_max and memory_max != "max":
            try:
                value = int(memory_max)
                if value >= 0:
                    finite_memory_limit = value
                    memory_limits.append(value)
            except ValueError:
                result["errors"].append("invalid memory.max")

        raw_current = read(directory, "memory.current")
        current_value = None
        if raw_current:
            try:
                current_value = int(raw_current)
                if current_value < 0:
                    current_value = None
                    result["errors"].append("invalid memory.current")
            except ValueError:
                result["errors"].append("invalid memory.current")
        if index == 0:
            result["memory_current_bytes"] = current_value
            raw_peak = read(directory, "memory.peak")
            if raw_peak:
                try:
                    peak_value = int(raw_peak)
                    if peak_value >= 0:
                        result["memory_peak_bytes"] = peak_value
                    else:
                        result["errors"].append("invalid memory.peak")
                except ValueError:
                    result["errors"].append("invalid memory.peak")
        if finite_memory_limit is not None:
            if current_value is None:
                missing_limited_current = True
            else:
                memory_headrooms.append(max(0, finite_memory_limit - current_value))

        cpu_max = read(directory, "cpu.max")
        if cpu_max:
            fields = cpu_max.split()
            if len(fields) == 2 and fields[0] != "max":
                try:
                    quota, period = int(fields[0]), int(fields[1])
                    if quota >= 0 and period > 0:
                        cpu_limits.append(quota / period)
                    else:
                        result["errors"].append("invalid cpu.max")
                except ValueError:
                    result["errors"].append("invalid cpu.max")
            elif len(fields) != 2:
                result["errors"].append("invalid cpu.max")

        cpuset = read(directory, "cpuset.cpus.effective")
        if cpuset:
            count = _parse_cpu_set_count(cpuset)
            if count is None:
                result["errors"].append("invalid cpuset.cpus.effective")
            else:
                cpuset_limits.append(count)

    if memory_limits:
        result["memory_limit_bytes"] = min(memory_limits)
        if memory_headrooms and not missing_limited_current:
            result["memory_headroom_bytes"] = min(memory_headrooms)
            result["memory_headroom_complete"] = True
    if cpu_limits:
        result["cpu_limit_cores"] = min(cpu_limits)
    if cpuset_limits:
        result["cpuset_limit_cores"] = min(cpuset_limits)
    return result


def _unified_membership(cgroup_text: str | None) -> str | None:
    if not isinstance(cgroup_text, str):
        return None
    for line in cgroup_text.splitlines():
        fields = line.split(":", 2)
        if len(fields) == 3 and fields[0] == "0" and fields[1] == "":
            return fields[2]
    return None


def _directory_bytes(root: Path) -> int:
    if root.is_symlink():
        raise ValueError("data root must be a real directory")
    if not root.exists():
        return 0
    if not root.is_dir():
        raise ValueError("data root must be a real directory")
    total = 0

    def walk_error(exc):
        raise exc

    for current, directories, files in os.walk(root, followlinks=False, onerror=walk_error):
        for name in directories + files:
            path = Path(current, name)
            total += path.lstat().st_size
    return total


def probe_resources(
    data_root: str | os.PathLike[str],
    *,
    root: str | os.PathLike[str] = "/",
    temp_root: str | os.PathLike[str] = "/dev/shm",
    cgroup_root: str | os.PathLike[str] = "/sys/fs/cgroup",
    cgroup_text: str | None = None,
) -> dict[str, Any]:
    """Measure disk, physical memory/CPU, and effective visible cgroup-v2 limits."""
    data_path = Path(data_root)
    root_path = Path(root)
    temp_path = Path(temp_root)
    if cgroup_text is None:
        try:
            cgroup_text = Path("/proc/self/cgroup").read_text(encoding="ascii")
        except OSError:
            cgroup_text = None
    membership = _unified_membership(cgroup_text)
    cgroup = _cgroup_limits(cgroup_root, membership)
    physical_memory = _physical_memory_bytes()
    physical_available = _physical_available_bytes()
    physical_cpus = os.cpu_count() or 1

    cgroup_memory_limit = cgroup["memory_limit_bytes"]
    memory_limit = min(physical_memory, cgroup_memory_limit) if cgroup_memory_limit is not None and physical_memory else cgroup_memory_limit
    if memory_limit is None:
        memory_limit = physical_memory or None
    if cgroup_memory_limit is not None:
        cgroup_available = cgroup["memory_headroom_bytes"]
        memory_available = min(physical_available, cgroup_available) if cgroup_available is not None and physical_available else cgroup_available
        memory_source = "cgroup_v2"
    else:
        memory_available = physical_available or None
        memory_source = "physical_fallback" if memory_limit is not None else "unavailable"
    cgroup_cpus = [value for value in (cgroup["cpu_limit_cores"], cgroup["cpuset_limit_cores"]) if value is not None]
    cpu_limit = min([float(physical_cpus), *cgroup_cpus])
    root_usage = shutil.disk_usage(root_path)
    data_usage = shutil.disk_usage(data_path)
    temp_usage = shutil.disk_usage(temp_path)
    return {
        "root_path": str(root_path),
        "root_free_bytes": root_usage.free,
        "root_device": root_path.stat().st_dev,
        "data_root_path": str(data_path),
        "data_root_used_bytes": _directory_bytes(data_path),
        "data_root_free_bytes": data_usage.free,
        "data_root_device": data_path.stat().st_dev,
        "temp_path": str(temp_path),
        "temp_free_bytes": temp_usage.free,
        "projected_temp_bytes": 0,
        "cpu_limit_cores": cpu_limit,
        "cpu_source": "cgroup_v2" if cgroup_cpus else "physical_fallback",
        "cpuset_limit_cores": cgroup["cpuset_limit_cores"],
        "memory_limit_bytes": memory_limit,
        "memory_available_bytes": memory_available,
        "memory_current_bytes": cgroup["memory_current_bytes"],
        "memory_peak_bytes": cgroup["memory_peak_bytes"],
        "memory_peak_scope": "cgroup_leaf" if cgroup["memory_peak_bytes"] is not None else None,
        "memory_headroom_bytes": cgroup["memory_headroom_bytes"],
        "memory_headroom_complete": cgroup["memory_headroom_complete"],
        "memory_source": memory_source,
        "cgroup_path": membership,
        "cgroup_errors": cgroup["errors"],
        "physical_memory_bytes": physical_memory or None,
        "physical_memory_available_bytes": physical_available or None,
        "physical_cpu_count": physical_cpus,
    }


def estimate_render_projection(workload: str, *, audio_enabled: bool = False) -> dict[str, Any]:
    """Bound anticipated output/cache writes and concurrent temporary render storage."""
    source_bytes = (
        MAX_COMPOSITION_BYTES + MAX_PROJECT_BYTES + MAX_REVISION_BYTES
        + MAX_PINNED_ASSET_BYTES + MAX_PINNED_AUDIO_BYTES
    )
    cache_bytes = CACHE_RESERVE_BYTES
    report_bytes = REPORT_RESERVE_BYTES
    if not isinstance(workload, str) or not workload:
        raise ValueError("workload must be a non-empty string")
    if workload.startswith("custom"):
        output_bytes = MAX_OUTPUT_BYTES
        data_root_bytes = output_bytes + source_bytes + cache_bytes + report_bytes
        temporary_bytes = output_bytes * (2 if audio_enabled else 1) + source_bytes + BROWSER_TEMP_RESERVE_BYTES
        remaining_temp = output_bytes * (2 if audio_enabled else 1) + BROWSER_TEMP_RESERVE_BYTES
        phases = {
            "initial": {"data_root_bytes": data_root_bytes, "temporary_bytes": temporary_bytes},
            "custom_workspace": {"data_root_bytes": data_root_bytes, "temporary_bytes": temporary_bytes},
            "custom_asr": {"data_root_bytes": data_root_bytes, "temporary_bytes": remaining_temp},
            "custom_capture": {"data_root_bytes": data_root_bytes, "temporary_bytes": remaining_temp},
            "custom_mux": {"data_root_bytes": data_root_bytes,
                            "temporary_bytes": MAX_OUTPUT_BYTES + 32 * MIB if audio_enabled else 0},
            "custom_publish": {"data_root_bytes": output_bytes + source_bytes + report_bytes, "temporary_bytes": 0},
            "custom_source_bundle": {"data_root_bytes": source_bytes + report_bytes, "temporary_bytes": 0},
            "custom_ready": {"data_root_bytes": 0, "temporary_bytes": 0},
        }
    else:
        # Legacy keeps a full intermediate video alongside the published output.
        output_bytes = MAX_OUTPUT_BYTES * 2
        legacy_source_bytes = MAX_PINNED_AUDIO_BYTES
        data_root_bytes = output_bytes + legacy_source_bytes + cache_bytes + report_bytes
        temporary_bytes = legacy_source_bytes + 32 * MIB
        phases = {
            "initial": {"data_root_bytes": data_root_bytes, "temporary_bytes": temporary_bytes},
            "legacy_setup": {"data_root_bytes": data_root_bytes, "temporary_bytes": temporary_bytes},
            "legacy_audio": {"data_root_bytes": data_root_bytes, "temporary_bytes": temporary_bytes},
            "legacy_capture": {"data_root_bytes": data_root_bytes, "temporary_bytes": temporary_bytes},
            "legacy_mux": {"data_root_bytes": MAX_OUTPUT_BYTES + report_bytes, "temporary_bytes": 0},
            "legacy_ready": {"data_root_bytes": 0, "temporary_bytes": 0},
        }
    return {
        "output_bytes": output_bytes,
        "source_bytes": source_bytes if workload.startswith("custom") else MAX_PINNED_AUDIO_BYTES,
        "cache_bytes": cache_bytes,
        "data_root_bytes": data_root_bytes,
        "temporary_bytes": temporary_bytes,
        "total_bytes": data_root_bytes + temporary_bytes,
        "phases": phases,
    }


def check_budget(sample: dict[str, Any], projected_bytes: int, workload: str) -> dict[str, Any]:
    """Return allow/warn/stop with explicit before/after disk numbers and reasons."""
    values = {
        "root_free_bytes": sample.get("root_free_bytes") if isinstance(sample, dict) else None,
        "data_root_free_bytes": sample.get("data_root_free_bytes") if isinstance(sample, dict) else None,
        "data_root_used_bytes": sample.get("data_root_used_bytes") if isinstance(sample, dict) else None,
        "temp_free_bytes": sample.get("temp_free_bytes") if isinstance(sample, dict) else None,
        "projected_temp_bytes": sample.get("projected_temp_bytes", 0) if isinstance(sample, dict) else None,
    }
    valid_sizes = all(type(value) is int and value >= 0 for value in values.values())
    valid_projection = type(projected_bytes) is int and projected_bytes >= 0
    valid_workload = isinstance(workload, str) and bool(workload.strip())
    root_device = sample.get("root_device") if isinstance(sample, dict) else None
    data_device = sample.get("data_root_device") if isinstance(sample, dict) else None
    devices_present = isinstance(sample, dict) and ("root_device" in sample or "data_root_device" in sample)
    valid_devices = (
        not devices_present or (
            type(root_device) is int and root_device >= 0
            and type(data_device) is int and data_device >= 0
        )
    )
    memory_headroom = sample.get("memory_headroom_bytes") if isinstance(sample, dict) else None
    memory_complete = sample.get("memory_headroom_complete", False) if isinstance(sample, dict) else False
    valid_memory = (
        type(memory_complete) is bool
        and (memory_headroom is None or (type(memory_headroom) is int and memory_headroom >= 0))
        and not (memory_complete and memory_headroom is None)
    )
    root_before = values["root_free_bytes"] if type(values["root_free_bytes"]) is int and values["root_free_bytes"] >= 0 else None
    data_free_before = values["data_root_free_bytes"] if type(values["data_root_free_bytes"]) is int and values["data_root_free_bytes"] >= 0 else None
    data_before = values["data_root_used_bytes"] if type(values["data_root_used_bytes"]) is int and values["data_root_used_bytes"] >= 0 else None
    temp_before = values["temp_free_bytes"] if type(values["temp_free_bytes"]) is int and values["temp_free_bytes"] >= 0 else None
    temp_projection = values["projected_temp_bytes"] if type(values["projected_temp_bytes"]) is int and values["projected_temp_bytes"] >= 0 else None
    same_filesystem = not devices_present or root_device == data_device
    root_projection = projected_bytes if same_filesystem and valid_projection else 0
    root_after = root_before - root_projection if root_before is not None and valid_projection else None
    data_free_after = data_free_before - projected_bytes if data_free_before is not None and valid_projection else None
    data_after = data_before + projected_bytes if data_before is not None and valid_projection else None
    temp_after = temp_before - temp_projection if temp_before is not None and temp_projection is not None else None

    reasons: list[str] = []
    if not valid_sizes or not valid_devices or not valid_memory or not valid_workload:
        reasons.append("invalid_sample")
    if not valid_projection:
        reasons.append("invalid_projection")
    if valid_sizes and valid_devices and valid_memory and valid_projection and valid_workload:
        if root_after is not None and root_after < ROOT_RESERVE_BYTES:
            reasons.append("root_reserve")
        if data_free_after is not None and data_free_after < 0:
            reasons.append("data_root_space")
        if data_after is not None and data_after >= DATA_ROOT_HARD_BYTES:
            reasons.append("data_root_hard_limit")
        if temp_after is not None and temp_after < 0:
            reasons.append("temporary_space")
        if data_after is not None and DATA_ROOT_SOFT_BYTES <= data_after < DATA_ROOT_HARD_BYTES:
            reasons.append("data_root_soft_threshold")
        if memory_complete and memory_headroom == 0:
            reasons.append("cgroup_memory_exhausted")

    if any(reason in reasons for reason in (
        "invalid_sample", "invalid_projection", "root_reserve", "data_root_space",
        "data_root_hard_limit", "temporary_space", "cgroup_memory_exhausted",
    )):
        decision = "stop"
    elif "data_root_soft_threshold" in reasons:
        decision = "warn"
    else:
        decision = "allow"
    return {
        "decision": decision,
        "workload": workload if valid_workload else None,
        "projected_bytes": projected_bytes if valid_projection else None,
        "projected_temp_bytes": temp_projection,
        "root_projected_bytes": root_projection if valid_projection else None,
        "root_free_before_bytes": root_before,
        "root_free_after_bytes": root_after,
        "data_root_free_before_bytes": data_free_before,
        "data_root_free_after_bytes": data_free_after,
        "data_root_used_before_bytes": data_before,
        "data_root_used_after_bytes": data_after,
        "temp_free_before_bytes": temp_before,
        "temp_free_after_bytes": temp_after,
        "memory_headroom_bytes": memory_headroom if valid_memory else None,
        "root_reserve_bytes": ROOT_RESERVE_BYTES,
        "data_root_soft_bytes": DATA_ROOT_SOFT_BYTES,
        "data_root_hard_bytes": DATA_ROOT_HARD_BYTES,
        "reasons": reasons,
    }
