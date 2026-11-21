import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.cleanup_research import plan_cleanup


class CleanupTests(unittest.TestCase):
    def test_dry_run_accepts_matching_clean_checkout(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "checkout"
            root.mkdir()
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "README").write_text("x")
            subprocess.run(["git", "-C", str(root), "add", "README"], check=True)
            subprocess.run(["git", "-C", str(root), "-c", "user.email=test@example.com", "-c", "user.name=test", "commit", "-qm", "fixture"], check=True)
            sha = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
            manifest = Path(raw) / "manifest.json"
            manifest.write_text(json.dumps({"checkouts": [{"source_id": "fixture", "path": str(root), "remote": "", "sha": sha, "ref": "refs/heads/master"}]}))
            result = plan_cleanup(manifest)
            self.assertEqual(result["eligible"], ["fixture"])
            self.assertTrue(root.exists())

    def test_dirty_checkout_and_disallowed_path_are_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "checkout"
            root.mkdir()
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "README").write_text("x")
            subprocess.run(["git", "-C", str(root), "add", "README"], check=True)
            subprocess.run(["git", "-C", str(root), "-c", "user.email=test@example.com", "-c", "user.name=test", "commit", "-qm", "fixture"], check=True)
            (root / "dirty").write_text("x")
            manifest = Path(raw) / "manifest.json"
            manifest.write_text(json.dumps({"checkouts": [{"source_id": "fixture", "path": str(root), "remote": "", "sha": "bad", "ref": "refs/heads/master"}, {"source_id": "outside", "path": "/tmp", "remote": "", "sha": "bad", "ref": "refs/heads/master"}]}))
            result = plan_cleanup(manifest)
            self.assertEqual(result["eligible"], [])
            self.assertEqual(len(result["rejected"]), 2)


if __name__ == "__main__":
    unittest.main()
