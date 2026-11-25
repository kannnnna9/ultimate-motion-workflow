import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ump_core import tools
from ump_core.jobs import JobStore


COMPOSITION = {
    "html": "<!doctype html><html><body>tool test</body></html>",
    "width": 1080,
    "height": 1920,
    "fps": 30,
    "duration_frames": 30,
    "audio_enabled": False,
    "subtitles_enabled": False,
}


class CompositionToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = JobStore(Path(self.tmp.name))
        store_patch = patch.object(tools, "_store", return_value=self.store)
        store_patch.start()
        self.addCleanup(store_patch.stop)

    def test_job_tool_creates_mode_and_writes_composition_revision(self):
        created = json.loads(tools.dispatch("ump_job", {
            "action": "create",
            "payload": {
                "brief": "custom silent clip",
                "mode": "directed",
                "requirements": {"checkpoints": ["preview"]},
            },
        }))
        self.assertTrue(created["ok"], created)
        job = created["data"]
        self.assertEqual((job["mode"], job["requirements"]["checkpoints"]),
                         ("directed", ["preview"]))

        written = json.loads(tools.dispatch("ump_job", {
            "action": "write_composition",
            "payload": {"job_id": job["job_id"], "rev": 1, "value": COMPOSITION},
        }))
        self.assertTrue(written["ok"], written)
        self.assertEqual((written["data"]["rev"], written["data"]["stage"]),
                         (2, "composition_draft"))

    def test_composition_tool_schema_forbids_filesystem_paths(self):
        schema = tools.SCHEMAS["ump_job"]["parameters"]
        self.assertIn("write_composition", schema["properties"]["action"]["enum"])
        payload = schema["properties"]["payload"]
        self.assertFalse(payload["additionalProperties"])
        value = payload["properties"]["value"]
        self.assertFalse(value["additionalProperties"])
        self.assertNotIn("path", value["properties"])

    def test_composition_dispatch_rejects_path_fields(self):
        job = self.store.create("path rejection")
        response = json.loads(tools.dispatch("ump_job", {
            "action": "write_composition",
            "payload": {
                "job_id": job["job_id"],
                "rev": 1,
                "value": {**COMPOSITION, "path": "/etc/passwd"},
            },
        }))
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "INVALID_COMPOSITION")

    def test_custom_render_dispatch_skips_legacy_episode_documents(self):
        job = self.store.create("custom render", mode="autonomous")
        custom = self.store.write_composition(job["job_id"], job["rev"], COMPOSITION)
        with patch.object(tools, "episode_docs", side_effect=AssertionError("legacy documents were requested")):
            with patch.object(tools, "submit", return_value={"run_id": "opaque-run-id"}) as submit:
                response = json.loads(tools.dispatch("ump_render", {
                    "job_id": custom["job_id"], "rev": custom["rev"], "target": "preview",
                }))
        self.assertTrue(response["ok"], response)
        submit.assert_called_once_with(self.store, custom["job_id"], custom["rev"], "preview")


if __name__ == "__main__":
    unittest.main()
