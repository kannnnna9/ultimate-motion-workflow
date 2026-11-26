"""Fail-closed per-render filesystem, network, and resource isolation."""
from __future__ import annotations

import argparse
import ctypes
import fcntl
import math
import os
import platform
import pwd
import re
import shutil
import signal
import stat
import subprocess
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Sequence

from .jobs import DomainError

CODE_ROOT = Path(__file__).resolve().parents[1]
SHM_ROOT = Path("/dev/shm").resolve()
WORKSPACE_PARENT = SHM_ROOT / "ump-v2"
WORKSPACE_ROOT = WORKSPACE_PARENT / str(os.getuid())
ASR_USER_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)
ASR_RUNTIME_ROOT = ASR_USER_HOME / ".local" / "share" / "ump-v2" / "asr-runtime"
ASR_MODEL_ROOT = ASR_USER_HOME / ".cache" / "ump-v2" / "whisperx"
MAX_RUNTIME_S = 900
_MEMORY_MAX = "2G"
_TASKS_MAX = "64"
CPU_QUOTA = "200%"
_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_UNIT_RE = re.compile(r"[A-Za-z0-9_.@-]+\.service\Z")

_LANDLOCK_CREATE_RULESET_VERSION = 1
_LANDLOCK_RULE_PATH_BENEATH = 1
_PR_SET_NO_NEW_PRIVS = 38
_FS_EXECUTE = 1 << 0
_FS_WRITE_FILE = 1 << 1
_FS_READ_FILE = 1 << 2
_FS_READ_DIR = 1 << 3
_FS_ALL_ABI4 = (1 << 15) - 1
_NET_BIND_TCP = 1 << 0
_NET_CONNECT_TCP = 1 << 1
_NET_ALL_ABI4 = _NET_BIND_TCP | _NET_CONNECT_TCP
_READ_ONLY = _FS_EXECUTE | _FS_READ_FILE | _FS_READ_DIR


class _RulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64), ("handled_access_net", ctypes.c_uint64)]


class _PathBeneathAttr(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]


def _private_directory(path: Path) -> None:
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    try:
        info = path.lstat()
    except OSError as exc:
        raise DomainError("WORKSPACE_UNAVAILABLE", str(exc)) from exc
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_uid != os.getuid():
        raise DomainError("WORKSPACE_UNAVAILABLE", "unsafe workspace parent")
    if stat.S_IMODE(info.st_mode) != 0o700:
        os.chmod(path, 0o700)


def _workspace_root() -> Path:
    if not SHM_ROOT.is_dir() or SHM_ROOT.is_symlink():
        raise DomainError("WORKSPACE_UNAVAILABLE", "/dev/shm is not a real directory")
    _private_directory(WORKSPACE_PARENT)
    _private_directory(WORKSPACE_ROOT)
    if WORKSPACE_ROOT.resolve(strict=True) != WORKSPACE_ROOT:
        raise DomainError("WORKSPACE_UNAVAILABLE", "workspace root resolves through a symlink")
    return WORKSPACE_ROOT


def _validated_workspace(value: str | os.PathLike[str]) -> Path:
    candidate = Path(value)
    root = _workspace_root()
    if not candidate.is_absolute() or candidate.parent != root or not _ID_RE.fullmatch(candidate.name):
        raise DomainError("INVALID_WORKSPACE", "workspace must be one direct run directory")
    try:
        info = candidate.lstat()
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise DomainError("INVALID_WORKSPACE", str(exc)) from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise DomainError("INVALID_WORKSPACE", "workspace must be an owned directory, not a symlink")
    if resolved != candidate or stat.S_IMODE(info.st_mode) != 0o700:
        raise DomainError("INVALID_WORKSPACE", "workspace path or permissions are unsafe")
    return candidate


def create_workspace(run_id: str) -> Path:
    if not isinstance(run_id, str) or not _ID_RE.fullmatch(run_id):
        raise DomainError("INVALID_WORKSPACE", "invalid run identifier")
    root = _workspace_root()
    workspace = root / run_id
    try:
        workspace.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise DomainError("WORKSPACE_EXISTS", "run workspace already exists") from exc
    try:
        os.chmod(workspace, 0o700)
        for name in ("home", "tmp", "cache", "config", "runtime", "profile"):
            child = workspace / name
            child.mkdir(mode=0o700)
            os.chmod(child, 0o700)
    except OSError as exc:
        shutil.rmtree(workspace, ignore_errors=True)
        raise DomainError("WORKSPACE_UNAVAILABLE", str(exc)) from exc
    return _validated_workspace(workspace)


def cleanup_workspace(workspace: str | os.PathLike[str]) -> None:
    candidate = Path(workspace)
    root = _workspace_root()
    if not candidate.is_absolute() or candidate.parent != root or not _ID_RE.fullmatch(candidate.name):
        raise DomainError("INVALID_WORKSPACE", "refusing cleanup outside the run root")
    if candidate.is_symlink():
        raise DomainError("INVALID_WORKSPACE", "refusing to follow a workspace symlink")
    if not candidate.exists():
        return
    shutil.rmtree(_validated_workspace(candidate))


@contextmanager
def render_lease() -> Iterator[None]:
    root = _workspace_root()
    lock_path = root / ".render.lock"
    flags = os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise DomainError("ISOLATION_UNAVAILABLE", f"cannot open render lease: {exc}") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise DomainError("ISOLATION_UNAVAILABLE", "unsafe render lease file")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DomainError("RENDER_BUSY", "another render holds the global lease") from exc
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _landlock_syscalls() -> tuple[int, int, int]:
    if platform.machine().lower() not in {"x86_64", "amd64", "aarch64", "arm64"}:
        raise DomainError("ISOLATION_UNAVAILABLE", "unsupported Landlock syscall architecture")
    return 444, 445, 446


def _safe_extra_paths(read_paths: Sequence[str | os.PathLike[str]]) -> list[Path]:
    if isinstance(read_paths, (str, bytes)):
        raise DomainError("PATH_OUTSIDE_ROOT", "read_paths must be a sequence of paths")
    result = []
    for raw in read_paths:
        path = Path(raw)
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise DomainError("PATH_OUTSIDE_ROOT", str(exc)) from exc
        if not path.is_absolute() or resolved != path or not path.is_relative_to(CODE_ROOT):
            raise DomainError("PATH_OUTSIDE_ROOT", "extra read paths must be real paths inside the code root")
        result.append(path)
    return result


def _safe_asr_roots(
    runtime_root: str | os.PathLike[str],
    model_root: str | os.PathLike[str],
) -> tuple[Path, Path]:
    roots = ((Path(runtime_root), ASR_RUNTIME_ROOT), (Path(model_root), ASR_MODEL_ROOT))
    resolved_roots: list[Path] = []
    for path, base in roots:
        try:
            resolved = path.resolve(strict=True)
            info = path.lstat()
        except OSError as exc:
            raise DomainError("ASR_ROOT_UNAVAILABLE", str(exc)) from exc
        if not path.is_absolute() or resolved != path or not path.is_relative_to(base):
            raise DomainError("PATH_OUTSIDE_ROOT", "ASR roots must be canonical descendants of their dedicated roots")
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise DomainError("ASR_ROOT_UNAVAILABLE", "ASR roots must be owned real directories")
        if stat.S_IMODE(info.st_mode) != 0o700:
            raise DomainError("ASR_ROOT_UNAVAILABLE", "ASR roots must have mode 0700")
        resolved_roots.append(path)
    if resolved_roots[0] == resolved_roots[1] or resolved_roots[0].is_relative_to(resolved_roots[1]) or resolved_roots[1].is_relative_to(resolved_roots[0]):
        raise DomainError("PATH_OUTSIDE_ROOT", "ASR runtime and model roots must be separate")
    return resolved_roots[0], resolved_roots[1]


def _landlock_rules(workspace: Path, read_paths: Sequence[Path], *, private_proc: bool = False) -> dict[Path, int]:
    required = (
        Path("/usr"), Path("/etc"), Path("/sys"),
        Path("/dev"), Path("/dev/shm"), CODE_ROOT, Path(sys.base_prefix), workspace,
    )
    rules: dict[Path, int] = {}

    def add(path: Path, rights: int, *, required_path: bool = False) -> None:
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            if required_path:
                raise DomainError("ISOLATION_UNAVAILABLE", f"required path missing: {path}") from exc
            return
        rules[resolved] = rules.get(resolved, 0) | rights

    for path in required:
        if path == workspace:
            rights = _FS_ALL_ABI4
        elif path in {Path("/dev"), Path("/dev/shm")}:
            rights = _FS_EXECUTE
        else:
            rights = _READ_ONLY
        add(path, rights, required_path=True)
    for path in (Path("/lib"), Path("/lib64"), Path("/opt/google/chrome"), Path("/opt/node/22.22.2")):
        add(path, _READ_ONLY)
    for path in read_paths:
        add(path, _READ_ONLY, required_path=True)
    if private_proc:
        add(Path("/proc"), _FS_EXECUTE | _FS_READ_DIR | _FS_READ_FILE, required_path=True)
    for path, rights in (
        (Path("/dev/null"), _FS_READ_FILE | _FS_WRITE_FILE),
        (Path("/dev/zero"), _FS_READ_FILE),
        (Path("/dev/random"), _FS_READ_FILE),
        (Path("/dev/urandom"), _FS_READ_FILE),
    ):
        add(path, rights)

    # Permit path traversal to explicit read roots without exposing their parents.
    for root in tuple(rules):
        parent = root.parent
        while parent != Path("/"):
            if parent not in rules:
                rules[parent] = _FS_EXECUTE
            parent = parent.parent
    return rules


def _apply_landlock(workspace: Path, read_paths: Sequence[Path], *, private_proc: bool = False) -> None:
    create_nr, add_nr, restrict_nr = _landlock_syscalls()
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    abi = libc.syscall(create_nr, None, 0, 1)
    if abi != 4:
        raise DomainError("ISOLATION_UNAVAILABLE", f"required Landlock ABI 4, found {abi}")

    attr = _RulesetAttr(_FS_ALL_ABI4, _NET_ALL_ABI4)
    ruleset_fd = libc.syscall(create_nr, ctypes.byref(attr), ctypes.sizeof(attr), 0)
    if ruleset_fd < 0:
        error = ctypes.get_errno()
        raise DomainError("ISOLATION_UNAVAILABLE", f"landlock_create_ruleset: {os.strerror(error)}")
    try:
        for path, rights in _landlock_rules(workspace, read_paths, private_proc=private_proc).items():
            path_fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
            try:
                rule = _PathBeneathAttr(rights, path_fd)
                if libc.syscall(add_nr, ruleset_fd, 1, ctypes.byref(rule), 0) < 0:
                    error = ctypes.get_errno()
                    raise DomainError("ISOLATION_UNAVAILABLE", f"landlock_add_rule {path}: {os.strerror(error)}")
            finally:
                os.close(path_fd)
        if libc.prctl(38, 1, 0, 0, 0) != 0:
            error = ctypes.get_errno()
            raise DomainError("ISOLATION_UNAVAILABLE", f"PR_SET_NO_NEW_PRIVS: {os.strerror(error)}")
        if libc.syscall(restrict_nr, ruleset_fd, 0) < 0:
            error = ctypes.get_errno()
            raise DomainError("ISOLATION_UNAVAILABLE", f"landlock_restrict_self: {os.strerror(error)}")
    finally:
        os.close(ruleset_fd)


def _wrapper_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--read-path", action="append", default=[])
    parser.add_argument("--asr-runtime-root")
    parser.add_argument("--asr-model-root")
    parser.add_argument("--allow-private-proc", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(list(argv))
    command = list(args.command)
    if command and command[0] == "--":
        command.pop(0)
    if not command:
        raise DomainError("INVALID_PAYLOAD", "sandbox command is empty")
    workspace = _validated_workspace(args.workspace)
    read_paths = _safe_extra_paths(args.read_path)
    if bool(args.asr_runtime_root) != bool(args.asr_model_root):
        raise DomainError("INVALID_PAYLOAD", "both ASR roots must be supplied together")
    if args.asr_runtime_root:
        if not args.allow_private_proc:
            raise DomainError("ISOLATION_UNAVAILABLE", "ASR roots require a private PID namespace")
        read_paths.extend(_safe_asr_roots(args.asr_runtime_root, args.asr_model_root))
    if args.allow_private_proc and os.getpid() != 1:
        raise DomainError("ISOLATION_UNAVAILABLE", "proc access requires a private PID namespace")
    os.chdir(workspace)
    _apply_landlock(workspace, read_paths, private_proc=args.allow_private_proc)
    os.execvpe(command[0], command, os.environ.copy())
    return 127


def _normalize_command(argv: Sequence[str], *, runtime_root: Path | None = None) -> list[str]:
    if isinstance(argv, (str, bytes)) or not argv:
        raise DomainError("INVALID_PAYLOAD", "command must be a non-empty argument list")
    command = [os.fspath(arg) for arg in argv]
    if any(not isinstance(arg, str) or "\x00" in arg for arg in command):
        raise DomainError("INVALID_PAYLOAD", "command arguments must be NUL-free strings")
    executable = shutil.which(command[0]) if not os.path.isabs(command[0]) else command[0]
    if not executable:
        raise DomainError("ISOLATION_UNAVAILABLE", "sandbox executable not found")
    lexical_path = Path(os.path.abspath(executable))
    executable_path = lexical_path.resolve(strict=True)
    roots = (
        Path("/usr").resolve(), Path(sys.base_prefix).resolve(), CODE_ROOT,
        Path("/opt/google/chrome"), Path("/opt/node/22.22.2"),
    )
    if runtime_root is not None:
        roots += (runtime_root,)
    if not any(executable_path == root or root in executable_path.parents for root in roots):
        raise DomainError("PATH_OUTSIDE_ROOT", "sandbox executable is outside approved read roots")
    # Keep a venv's bin/python path so Python can discover its adjacent pyvenv.cfg.
    command[0] = str(lexical_path if runtime_root is not None and lexical_path.is_relative_to(runtime_root) else executable_path)
    return command


def _positive_timeout(timeout_s: float) -> int:
    if not isinstance(timeout_s, (int, float)) or not math.isfinite(timeout_s) or timeout_s <= 0 or timeout_s > MAX_RUNTIME_S:
        raise DomainError("INVALID_PAYLOAD", f"timeout must be between 0 and {MAX_RUNTIME_S} seconds")
    return max(1, math.ceil(timeout_s))


def start_isolated(
    argv: Sequence[str],
    *,
    workspace: str | os.PathLike[str],
    read_paths: Sequence[str | os.PathLike[str]] = (),
    stdin: int = subprocess.DEVNULL,
    timeout_s: float = MAX_RUNTIME_S,
    private_pid_namespace: bool = False,
    _asr_roots: tuple[Path, Path] | None = None,
) -> tuple[subprocess.Popen[bytes], str]:
    timeout = _positive_timeout(timeout_s)
    if stdin not in (subprocess.DEVNULL, subprocess.PIPE):
        raise DomainError("INVALID_PAYLOAD", "sandbox stdin must be closed or piped")
    systemd_run = shutil.which("systemd-run")
    if not systemd_run:
        raise DomainError("ISOLATION_UNAVAILABLE", "systemd-run is required")
    work = _validated_workspace(workspace)
    asr_roots = _safe_asr_roots(*_asr_roots) if _asr_roots is not None else None
    if asr_roots is not None and not private_pid_namespace:
        raise DomainError("ISOLATION_UNAVAILABLE", "ASR inference requires a private PID namespace")
    command = _normalize_command(argv, runtime_root=asr_roots[0] if asr_roots else None)
    extra_paths = _safe_extra_paths(read_paths)
    unit_prefix = "priv-ump-v2-asr-" if asr_roots else ("priv-ump-v2-render-" if private_pid_namespace else "ump-v2-render-")
    unit_name = unit_prefix + uuid.uuid4().hex + ".service"
    env_values = {
        "HOME": str(work / "home"),
        "TMPDIR": str(work / "tmp"),
        "XDG_CACHE_HOME": str(work / "cache"),
        "XDG_CONFIG_HOME": str(work / "config"),
        "XDG_RUNTIME_DIR": str(work / "runtime"),
        "PYTHONPATH": str(CODE_ROOT),
        "PATH": "/opt/node/22.22.2/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }
    if asr_roots:
        env_values.update({
            "HF_HOME": str(work / "cache" / "huggingface"),
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "CUDA_VISIBLE_DEVICES": "",
            "OMP_NUM_THREADS": "2",
            "MKL_NUM_THREADS": "2",
            "TOKENIZERS_PARALLELISM": "false",
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        })
    if private_pid_namespace:
        sudo = shutil.which("sudo")
        unshare = shutil.which("unshare")
        setpriv = shutil.which("setpriv")
        if not sudo or not unshare or not setpriv:
            raise DomainError("ISOLATION_UNAVAILABLE", "sudo, unshare, and setpriv are required for a private PID namespace")
        try:
            privilege = subprocess.run([sudo, "-n", "true"], capture_output=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DomainError("ISOLATION_UNAVAILABLE", "passwordless system-manager access is required") from exc
        if privilege.returncode:
            raise DomainError("ISOLATION_UNAVAILABLE", "passwordless system-manager access is required")
        launch_command = [
            unshare, "--pid", "--fork", "--mount-proc",
            setpriv, f"--reuid={os.getuid()}", f"--regid={os.getgid()}", "--clear-groups", "--",
            "/usr/bin/env", "-i", *(f"{key}={value}" for key, value in env_values.items()),
            sys.executable, "-m", "ump_core.sandbox", "--workspace", str(work), "--allow-private-proc",
        ]
        for path in extra_paths:
            launch_command.extend(("--read-path", str(path)))
        if asr_roots:
            launch_command.extend(("--asr-runtime-root", str(asr_roots[0]), "--asr-model-root", str(asr_roots[1])))
        launch_command.extend(("--", *command))
        unit_command = [sudo, "-n", systemd_run, "--system", "--pipe", "--wait", "--collect", "--quiet", f"--unit={unit_name}"]
    else:
        launch_command = [
            "/usr/bin/env", "-i", *(f"{key}={value}" for key, value in env_values.items()),
            sys.executable, "-m", "ump_core.sandbox", "--workspace", str(work),
        ]
        for path in extra_paths:
            launch_command.extend(("--read-path", str(path)))
        if asr_roots:
            launch_command.extend(("--asr-runtime-root", str(asr_roots[0]), "--asr-model-root", str(asr_roots[1])))
        launch_command.extend(("--", *command))
        unit_command = [systemd_run, "--user", "--pipe", "--wait", "--collect", "--quiet", f"--unit={unit_name}"]
    properties = (
        "MemoryAccounting=yes", "MemoryMax=2G", "MemorySwapMax=0", "CPUQuota=200%",
        f"TasksMax={256 if private_pid_namespace else 64}",
        *(("TimeoutStopSec=3s",) if private_pid_namespace else ()),
        f"RuntimeMaxSec={timeout}s", "IPAddressDeny=any",
        "RestrictAddressFamilies=AF_UNIX", "ProtectProc=ptraceable",
        "ProtectSystem=strict", "ProtectHome=read-only", "NoNewPrivileges=yes",
        "UMask=0077", f"WorkingDirectory={work}", f"ReadWritePaths={work}",
    )
    unit_command.extend(f"--property={value}" for value in properties)
    unit_command.extend(("--", *launch_command))
    try:
        process = subprocess.Popen(
            unit_command, cwd=CODE_ROOT, stdin=stdin,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
        )
    except OSError as exc:
        raise DomainError("ISOLATION_UNAVAILABLE", f"could not start transient unit: {exc}") from exc
    return process, unit_name


def start_asr_isolated(
    argv: Sequence[str],
    *,
    workspace: str | os.PathLike[str],
    runtime_root: str | os.PathLike[str],
    model_root: str | os.PathLike[str],
    timeout_s: float = MAX_RUNTIME_S,
) -> tuple[subprocess.Popen[bytes], str]:
    roots = _safe_asr_roots(runtime_root, model_root)
    return start_isolated(
        argv,
        workspace=workspace,
        timeout_s=timeout_s,
        private_pid_namespace=True,
        _asr_roots=roots,
    )


def _stop_unit(unit_name: str) -> None:
    if not _UNIT_RE.fullmatch(unit_name):
        raise DomainError("INVALID_PAYLOAD", "invalid transient unit name")
    manager = ["sudo", "-n", "systemctl", "--system"] if unit_name.startswith("priv-") else ["systemctl", "--user"]
    stop = subprocess.run([*manager, "stop", unit_name], capture_output=True, text=True, timeout=15)
    if stop.returncode == 0:
        return
    state = subprocess.run(
        [*manager, "show", unit_name, "--property=ActiveState", "--value"],
        capture_output=True, text=True, timeout=15,
    )
    if state.returncode != 0 or state.stdout.strip() in {"inactive", "failed", "not-found"}:
        return
    subprocess.run(
        [*manager, "kill", "--kill-who=all", "--signal=KILL", unit_name],
        capture_output=True, text=True, timeout=15,
    )
    retry = subprocess.run([*manager, "stop", unit_name], capture_output=True, text=True, timeout=15)
    if retry.returncode != 0:
        raise DomainError("ISOLATION_STOP_FAILED", retry.stderr.strip() or stop.stderr.strip())


def wait_isolated(process: subprocess.Popen[bytes], unit_name: str, timeout_s: float) -> int:
    _positive_timeout(timeout_s)
    if not _UNIT_RE.fullmatch(unit_name):
        raise DomainError("INVALID_PAYLOAD", "invalid transient unit name")
    try:
        return process.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        _stop_unit(unit_name)
        try:
            process.wait(timeout=15)
            return 124
        except subprocess.TimeoutExpired as exc:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)
            raise DomainError("ISOLATION_STOP_FAILED", "systemd-run client did not exit after unit stop") from exc


if __name__ == "__main__":
    try:
        raise SystemExit(_wrapper_main(sys.argv[1:]))
    except DomainError as exc:
        print(str(exc), file=sys.stderr, flush=True)
        raise SystemExit(125)
