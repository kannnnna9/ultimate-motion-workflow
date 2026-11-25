import errno
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from typing import cast
from unittest.mock import patch

from ump_core.jobs import DomainError
from ump_core.sandbox import (
    cleanup_workspace,
    create_workspace,
    render_lease,
    start_isolated,
    wait_isolated,
)


ENABLED = os.environ.get("UMP_SANDBOX_TESTS") == "1"


@unittest.skipUnless(ENABLED, "set UMP_SANDBOX_TESTS=1 to run Linux sandbox integration tests")
class SandboxIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("systemd-run") or not shutil.which("systemctl"):
            raise unittest.SkipTest("user systemd tools are unavailable")
        probe = subprocess.run(
            ["systemctl", "--user", "show-environment"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if probe.returncode:
            raise unittest.SkipTest("user systemd manager is unavailable")

    def setUp(self):
        self.workspace = create_workspace("test-" + uuid.uuid4().hex)
        self.addCleanup(self._cleanup_workspace)

    def _cleanup_workspace(self):
        if self.workspace.exists():
            cleanup_workspace(self.workspace)

    def _start(self, source, *args, timeout_s=10):
        return start_isolated(
            [sys.executable, "-c", source, *map(str, args)],
            workspace=self.workspace,
            read_paths=(),
            timeout_s=timeout_s,
        )

    def _run(self, source, *args, timeout_s=10):
        process, unit = self._start(source, *args, timeout_s=timeout_s)
        status = wait_isolated(process, unit, timeout_s=timeout_s)
        stdout, stderr = process.communicate()
        return status, stdout, stderr

    def test_missing_systemd_fails_closed(self):
        def resolve(command):
            return None if command == "systemd-run" else sys.executable

        with patch("ump_core.sandbox.shutil.which", side_effect=resolve):
            with self.assertRaises(DomainError) as raised:
                self._start("pass")
        self.assertEqual(raised.exception.code, "ISOLATION_UNAVAILABLE")

    def test_unit_has_required_resource_and_isolation_properties(self):
        process, unit = self._start("import time; print('ready',flush=True); time.sleep(2)", timeout_s=10)
        self.assertIsNotNone(process.stdout)
        self.assertEqual(process.stdout.readline().strip(), b"ready")
        raw = subprocess.check_output(
            [
                "systemctl", "--user", "show", unit,
                "--property=MemoryAccounting", "--property=MemoryMax",
                "--property=MemorySwapMax", "--property=CPUQuotaPerSecUSec",
                "--property=TasksMax", "--property=IPAddressDeny",
                "--property=RestrictAddressFamilies", "--property=ProtectProc",
                "--property=NoNewPrivileges",
            ],
            text=True,
        )
        values = dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)
        self.assertEqual(values["MemoryAccounting"], "yes")
        self.assertEqual(values["MemoryMax"], str(2 * 1024**3))
        self.assertEqual(values["MemorySwapMax"], "0")
        self.assertTrue(values["CPUQuotaPerSecUSec"])
        self.assertEqual(values["TasksMax"], "64")
        self.assertTrue(values["IPAddressDeny"])
        self.assertEqual(values["RestrictAddressFamilies"], "AF_UNIX")
        self.assertEqual(values["ProtectProc"], "ptraceable")
        self.assertEqual(values["NoNewPrivileges"], "yes")
        status = wait_isolated(process, unit, timeout_s=5)
        _, stderr = process.communicate()
        self.assertEqual(status, 0, stderr)

    def test_workspace_write_and_clean_environment(self):
        source = (
            "import os,pathlib,sys; "
            "pathlib.Path(sys.argv[1],'allowed.bin').write_bytes(b'workspace'); "
            "print('clean' if 'UMP_TEST_SECRET' not in os.environ else 'dirty')"
        )
        with patch.dict(os.environ, {"UMP_TEST_SECRET": "not-for-child"}):
            status, stdout, _ = self._run(source, self.workspace)
        self.assertEqual(status, 0)
        self.assertEqual((self.workspace / "allowed.bin").read_bytes(), b"workspace")
        self.assertEqual(stdout.strip(), b"clean")

    def test_config_file_read_is_denied_without_reading_contents(self):
        config = Path.home() / ".hermes" / "config.yaml"
        if not config.is_file():
            self.skipTest("Hermes config file is not present on this host")
        source = (
            "import errno, os, sys\n"
            "p = sys.argv[1]\n"
            "try:\n"
            "    fd = os.open(p, os.O_RDONLY)\n"
            "except OSError as e:\n"
            "    denied = e.errno in (errno.EACCES, errno.EPERM)\n"
            "    print('denied' if denied else 'wrong-error')\n"
            "    sys.exit(0 if denied else 3)\n"
            "else:\n"
            "    os.close(fd)\n"
            "    print('allowed')\n"
            "    sys.exit(4)\n"
        )
        status, stdout, _ = self._run(source, config)
        self.assertEqual(status, 0)
        self.assertEqual(stdout.strip(), b"denied")

    def test_same_uid_process_metadata_is_not_visible(self):
        sentinel = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            source = (
                "import errno, os, sys\n"
                "p = '/proc/' + sys.argv[1] + '/cmdline'\n"
                "try:\n"
                "    fd = os.open(p, os.O_RDONLY)\n"
                "except OSError as e:\n"
                "    hidden = e.errno in (errno.EACCES, errno.EPERM, errno.ENOENT)\n"
                "    print('hidden' if hidden else 'wrong-error')\n"
                "    sys.exit(0 if hidden else 3)\n"
                "else:\n"
                "    os.close(fd)\n"
                "    print('visible')\n"
                "    sys.exit(4)\n"
            )
            status, stdout, _ = self._run(source, sentinel.pid)
            self.assertEqual(status, 0)
            self.assertEqual(stdout.strip(), b"hidden")
        finally:
            sentinel.terminate()
            sentinel.wait(timeout=5)

    def test_sibling_workspace_read_and_listing_are_denied(self):
        sibling = self.workspace.parent / ("sibling-" + uuid.uuid4().hex)
        sibling.mkdir(mode=0o700)
        target = sibling / "private.bin"
        target.write_bytes(b"not-for-sandbox")
        try:
            source = (
                "import errno,os,sys\n"
                "root, target = sys.argv[1], sys.argv[2]\n"
                "def denied(action):\n"
                "    try:\n"
                "        action()\n"
                "    except OSError as e:\n"
                "        return e.errno in (errno.EACCES, errno.EPERM)\n"
                "    return False\n"
                "def open_target():\n"
                "    fd = os.open(target, os.O_RDONLY)\n"
                "    os.close(fd)\n"
                "listed_denied = denied(lambda: os.listdir(root))\n"
                "read_denied = denied(open_target)\n"
                "ok = listed_denied and read_denied\n"
                "print('denied' if ok else 'visible')\n"
                "sys.exit(0 if ok else 4)\n"
            )
            status, stdout, _ = self._run(source, sibling, target)
            self.assertEqual(status, 0)
            self.assertEqual(stdout.strip(), b"denied")
        finally:
            shutil.rmtree(sibling)

    def test_sibling_workspace_write_is_denied(self):
        sibling = self.workspace.parent / ("sibling-" + uuid.uuid4().hex)
        sibling.mkdir(mode=0o700)
        try:
            source = (
                "import errno,pathlib,sys; p=pathlib.Path(sys.argv[1])\n"
                "try: p.write_bytes(b'escape')\n"
                "except OSError as e: print('denied' if e.errno in (errno.EACCES,errno.EPERM) else 'wrong-error'); sys.exit(0 if e.errno in (errno.EACCES,errno.EPERM) else 3)\n"
                "else: print('allowed'); sys.exit(4)"
            )
            status, stdout, _ = self._run(source, sibling / "escape.bin")
            self.assertEqual(status, 0)
            self.assertEqual(stdout.strip(), b"denied")
            self.assertFalse((sibling / "escape.bin").exists())
        finally:
            shutil.rmtree(sibling)

    def test_inet_socket_creation_is_denied(self):
        source = (
            "import errno,socket,sys\n"
            "try: sock=socket.socket(socket.AF_INET,socket.SOCK_STREAM)\n"
            "except OSError as e: print('denied' if e.errno in (errno.EAFNOSUPPORT,errno.EPERM) else 'wrong-error'); sys.exit(0 if e.errno in (errno.EAFNOSUPPORT,errno.EPERM) else 3)\n"
            "else: sock.close(); print('allowed'); sys.exit(4)"
        )
        status, stdout, _ = self._run(source)
        self.assertEqual(status, 0)
        self.assertEqual(stdout.strip(), b"denied")

    def test_stdout_preserves_binary_bytes(self):
        process, unit = self._start("import os; os.write(1,b'\\x00\\xff\\x7f')")
        payload = process.stdout.read() if process.stdout else b""
        status = wait_isolated(process, unit, timeout_s=10)
        process.communicate()
        self.assertEqual(status, 0)
        self.assertEqual(payload, b"\x00\xff\x7f")

    def test_render_lease_is_nonblocking(self):
        with render_lease():
            with self.assertRaises(DomainError) as raised:
                with render_lease():
                    pass
        self.assertEqual(raised.exception.code, "RENDER_BUSY")

    def test_host_timeout_reports_124_even_if_runner_exits_nonzero(self):
        class Runner:
            calls = 0

            def wait(self, timeout: float | None = None) -> int:
                self.calls += 1
                if self.calls == 1:
                    raise subprocess.TimeoutExpired("systemd-run", float(timeout or 0.01))
                return 7

        unit = "ump-v2-render-test.service"
        with patch("ump_core.sandbox._stop_unit") as stop:
            status = wait_isolated(cast(subprocess.Popen[bytes], Runner()), unit, timeout_s=0.01)
        stop.assert_called_once_with(unit)
        self.assertEqual(status, 124)

    def test_workspace_id_and_cleanup_reject_escape(self):
        with self.assertRaises(DomainError):
            create_workspace("../escape")
        with tempfile.TemporaryDirectory() as raw:
            outside = Path(raw)
            alias = self.workspace.parent / ("alias-" + uuid.uuid4().hex)
            alias.symlink_to(outside, target_is_directory=True)
            try:
                with self.assertRaises(DomainError):
                    cleanup_workspace(alias)
                self.assertTrue(outside.is_dir())
            finally:
                alias.unlink(missing_ok=True)

    def test_timeout_stops_exact_unit_and_child(self):
        source = (
            "import os,sys,time; print(os.getpid(),flush=True); "
            "time.sleep(30)"
        )
        process, unit = self._start(source, timeout_s=5)
        self.assertIsNotNone(process.stdout)
        line = process.stdout.readline().strip()
        self.assertTrue(line.isdigit())
        child_pid = int(line)
        status = wait_isolated(process, unit, timeout_s=0.5)
        _, stderr = process.communicate()
        self.assertEqual(status, 124)
        self.assertIsNotNone(process.poll())
        with self.assertRaises(ProcessLookupError):
            os.kill(child_pid, 0)

    def test_64m_memory_limit_caps_tmpfs_growth(self):
        unit = "ump-v2-m1-memory-" + uuid.uuid4().hex + ".service"
        output = self.workspace / "fill.bin"
        source = (
            "import pathlib,sys; p=pathlib.Path(sys.argv[1]); "
            "f=p.open('wb'); block=b'x'*(1024*1024); "
            "\nwhile True: f.write(block); f.flush()"
        )
        command = [
            "systemd-run", "--user", "--pipe", "--wait", "--unit", unit,
            "--property=MemoryAccounting=yes", "--property=MemoryMax=64M",
            "--property=MemorySwapMax=0", "--property=RuntimeMaxSec=30s",
            "--property=TasksMax=16", "--", sys.executable, "-c", source, str(output),
        ]
        try:
            result = subprocess.run(command, capture_output=True, timeout=40)
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(output.is_file())
            size = output.stat().st_size
            self.assertGreater(size, 32 * 1024 * 1024)
            self.assertLess(size, 64 * 1024 * 1024)
            result_text = subprocess.check_output(
                ["systemctl", "--user", "show", unit, "--property=Result", "--value"],
                text=True,
            ).strip()
            self.assertEqual(result_text, "oom-kill")
        finally:
            subprocess.run(
                ["systemctl", "--user", "reset-failed", unit],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            output.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
