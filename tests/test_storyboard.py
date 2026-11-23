import json
import tempfile
import unittest
from pathlib import Path

from ump_core import tools
from ump_core.jobs import STAGES, DomainError, JobStore


class StoryboardFlowTests(unittest.TestCase):
    """The storyboard is an optional reference image supplied after the script.

    The operator writes the script first, then hands over a storyboard drawn
    elsewhere; frames and the video are derived from it. Attaching one is what
    moves the job out of the waiting stage.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = JobStore(self.root / "ump")
        self.job = self.store.create("storyboard flow")

    def tearDown(self):
        self.tmp.cleanup()

    def test_storyboard_sits_between_the_script_and_the_frames(self):
        order = list(STAGES)
        self.assertLess(order.index("naskah_approved"), order.index("storyboard"))
        self.assertLess(order.index("storyboard"), order.index("style_frame"))

    def test_frames_wait_for_the_storyboard(self):
        job = self.job["job_id"]
        self.store.set_stage(job, 1, "naskah_approved")
        with self.assertRaisesRegex(DomainError, "STAGE_ORDER"):
            self.store.claim(job, 1, "style_frame")
        self.store.set_stage(job, 1, "storyboard")
        run = self.store.claim(job, 1, "style_frame")
        self.assertEqual(run["target"], "style_frame")

    def test_a_new_storyboard_invalidates_rendered_frames(self):
        job = self.job["job_id"]
        self.store.set_stage(job, 1, "storyboard")
        self.store.set_stage(job, 1, "storyboard")
        revised = self.store.revise(job, 1, {"storyboard": "second.png"})
        self.assertEqual(revised["stage"], "storyboard")

    def test_attaching_before_the_script_is_refused(self):
        with self.assertRaisesRegex(DomainError, "STAGE_ORDER"):
            tools._job(self.store, "attach_storyboard",
                       {"job_id": self.job["job_id"], "rev": 1, "storyboard": "/tmp/x.png"})

    def test_storyboard_stage_accepts_a_plain_approval_sentence(self):
        verdict, stage = tools.classify_approval("setuju storyboardnya", awaiting="storyboard")
        self.assertEqual((verdict, stage), ("approve", "storyboard"))


class PlainApprovalTests(unittest.TestCase):
    """Approval is a sentence, not a command string with identifiers."""

    def test_plain_sentences_approve_the_awaiting_stage(self):
        for text in ("setuju", "ya setuju", "oke lanjut", "gas", "sip", "lanjutkan", "mantap"):
            with self.subTest(text=text):
                self.assertEqual(tools.classify_approval(text, awaiting="preview"),
                                 ("approve", "preview"))

    def test_command_form_still_parses_without_being_required(self):
        self.assertIsNotNone(tools._APPROVAL.fullmatch(
            "UMP APPROVE jPqXBh_NicNHLJkh 9 storyboard g_hMdpYZoN75d6Ci"))

    def test_no_identifier_is_needed_in_chat(self):
        for text in ("setuju", "gas", "lanjut"):
            self.assertIsNone(tools._APPROVAL.fullmatch(text))


if __name__ == "__main__":
    unittest.main()
