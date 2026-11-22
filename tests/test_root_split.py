import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ump_core import worker


class RootSplitTests(unittest.TestCase):
    """Code and data must not live in the same directory tree.

    `ROOT` was `Path(__file__).parents[1]`, so when the plugin is installed it
    points inside ~/.hermes/plugins/ump. Two things break there that never break
    in a checkout:

    1. The worker executes `scripts/` and reads `styles/` from a directory that a
       reinstall can replace at any moment, including mid-render.
    2. `demo/` fixtures ship inside that same tree, so a reinstall silently
       overwrites a fixture the operator corrected.
    """

    def test_code_root_follows_the_module(self):
        self.assertEqual(worker.CODE_ROOT, Path(worker.__file__).resolve().parents[1])

    def test_data_root_is_outside_the_plugin_tree(self):
        self.assertNotEqual(worker.DATA_ROOT, worker.CODE_ROOT)
        self.assertNotIn(
            worker.CODE_ROOT.as_posix(), worker.DATA_ROOT.as_posix(),
            "data must not live under the code tree",
        )

    def test_data_root_honours_an_explicit_override(self):
        """A profile-scoped override keeps tests and alternate installs hermetic."""
        with tempfile.TemporaryDirectory() as raw:
            with mock.patch.dict(os.environ, {"UMP_DATA_ROOT": raw}):
                import importlib

                reloaded = importlib.reload(worker)
                try:
                    self.assertEqual(reloaded.DATA_ROOT, Path(raw).resolve())
                finally:
                    os.environ.pop("UMP_DATA_ROOT", None)
                    importlib.reload(worker)

    def test_fixtures_resolve_under_the_data_root(self):
        for name in worker.FIXTURES:
            path = worker.fixture_dir(name)
            self.assertTrue(
                path.as_posix().startswith(worker.DATA_ROOT.as_posix()),
                f"fixture {name} resolves outside DATA_ROOT: {path}",
            )

    def test_unknown_fixture_is_refused(self):
        with self.assertRaises(worker.DomainError):
            worker.fixture_dir("../../etc")

    def test_seeding_never_overwrites_a_corrected_fixture(self):
        """A fixture the operator fixed must survive every upgrade.

        This is the whole reason the fixtures left the code tree: shipping them
        inside the plugin meant each reinstall reverted local corrections.
        """
        with tempfile.TemporaryDirectory() as raw:
            data = Path(raw)
            src = worker.CODE_ROOT / "demo"
            seeded = worker.seed_fixtures(src, data)
            self.assertTrue(seeded, "nothing was seeded")
            name = seeded[0]
            marker = worker.fixture_dir(name, data) / "OPERATOR_EDIT.txt"
            marker.write_text("kept", encoding="utf-8")
            again = worker.seed_fixtures(src, data)
            self.assertEqual(again, [], "a second seed must not copy again")
            self.assertTrue(marker.is_file(), "local correction was overwritten")

    def test_seeding_is_idempotent(self):
        with tempfile.TemporaryDirectory() as raw:
            data = Path(raw)
            worker.seed_fixtures(worker.CODE_ROOT / "demo", data)
            self.assertEqual(worker.seed_fixtures(worker.CODE_ROOT / "demo", data), [])

    def test_code_root_keeps_the_things_the_worker_executes(self):
        """scripts/ and styles/ are code: they stay with the module."""
        self.assertTrue((worker.CODE_ROOT / "scripts").is_dir())
        self.assertTrue((worker.CODE_ROOT / "styles" / "registry.json").is_file())


if __name__ == "__main__":
    unittest.main()
