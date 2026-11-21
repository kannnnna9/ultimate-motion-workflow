import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ump_core.jobs import STAGES, DomainError, JobStore
from ump_core import tools


class StageReorderTests(unittest.TestCase):
    """Style is chosen before the script is written, not after it is approved.

    The script already has to name a visual register: once you know the style,
    the words are written to fit it. Approving the script first forced the
    writer to guess the look and then patch the script to match a later choice.
    """

    def test_style_comes_before_naskah(self):
        self.assertLess(STAGES.index("style_approved"), STAGES.index("naskah_draft"))
        self.assertEqual(STAGES.index("style_approved"), STAGES.index("research_approved") + 1)

    def test_full_order_is_linear_and_complete(self):
        self.assertEqual(STAGES, (
            "intake", "brief_draft", "brief_approved",
            "research_draft", "research_approved",
            "style_approved",
            "naskah_draft", "naskah_approved",
            "style_frame", "preview", "final_ready",
        ))

    def test_select_style_is_allowed_from_research_approved(self):
        self.assertEqual(tools._STAGE_ROUTES["select_style"], (("research_approved",), "style_approved"))

    def test_write_naskah_requires_style_approved(self):
        self.assertEqual(tools._STAGE_ROUTES["write_naskah"], (("style_approved",), "naskah_draft"))

    def test_import_naskah_still_needs_a_style_decision(self):
        """An imported script skips writing, not the style choice."""
        allowed, target = tools._STAGE_ROUTES["import_naskah"]
        self.assertIn("style_approved", allowed)
        self.assertEqual(target, "naskah_draft")


class SentenceApprovalTests(unittest.TestCase):
    """A clear sentence of approval must work; an unclear one must be asked about.

    Requiring an exact `UMP APPROVE <job> <rev> <stage> <artifact>` line pushed
    the operator into typing identifiers they never see in the chat. The guard
    that mattered was always that the approval could not be fabricated by the
    model, not that the operator typed sixteen characters.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = JobStore(Path(self.tmp.name))
        self._orig_store = tools._store
        tools._store = lambda: self.store
        # No gateway in this process: the exact-command path finds no event and
        # falls through to sentence classification, which is what these tests cover.
        self._orig_key = tools._event_key
        tools._event_key = lambda: (_ for _ in ()).throw(DomainError("OPERATOR_REQUIRED"))

    def tearDown(self):
        tools._store = self._orig_store
        tools._event_key = self._orig_key
        self.tmp.cleanup()

    # ---- classification -------------------------------------------------

    def test_clear_yes_lines_are_approvals(self):
        for text in (
            "ya setujui brief-nya",
            "setuju",
            "approve",
            "ya, lanjut",
            "gas lanjut aja",
            "ok setujui",
            "sip lanjut",
        ):
            with self.subTest(text=text):
                verdict, stage = tools.classify_approval(text, awaiting="brief")
                self.assertEqual(verdict, "approve", f"{text!r} should approve")
                self.assertEqual(stage, "brief")

    def test_clear_no_lines_are_rejections(self):
        for text in ("tolak", "jangan lanjut", "gak setuju", "batal", "gak usah"):
            with self.subTest(text=text):
                verdict, _ = tools.classify_approval(text, awaiting="brief")
                self.assertEqual(verdict, "reject", f"{text!r} should reject")

    def test_questions_and_prose_are_not_approvals(self):
        """"Setujui?" is a question about approving, not an approval."""
        for text in (
            "setujui?",
            "harus setuju gak sih",
            "kenapa aku harus setuju",
            "kalau setuju berarti lanjut ya?",
            "napas",
            "berapa lama render",
            "ganti warnanya jadi biru",
        ):
            with self.subTest(text=text):
                verdict, _ = tools.classify_approval(text, awaiting="brief")
                self.assertEqual(verdict, "unclear", f"{text!r} must not approve")

    def test_topic_mention_disambiguates(self):
        """'setuju' with an explicit topic commits that topic, not the awaiting one."""
        verdict, stage = tools.classify_approval("setuju naskahnya", awaiting="brief")
        self.assertEqual((verdict, stage), ("approve", "naskah"))
        verdict, stage = tools.classify_approval("setuju riset", awaiting="brief")
        self.assertEqual((verdict, stage), ("approve", "research"))

    def test_one_sentence_never_approves_two_stages(self):
        verdict, stage = tools.classify_approval("setuju brief dan riset", awaiting="brief")
        self.assertEqual(verdict, "unclear")

    def test_hedged_or_delegated_sentences_never_approve(self):
        """A conditional, hypothetical or delegated line is not consent.

        These passed as approvals on the first implementation because they only
        contain an approve word. "anggap saja" and "bisa dianggap" are the model
        being asked to infer consent, which is exactly what the gate exists to
        stop.
        """
        for text in (
            "ini bisa dianggap setuju",
            "anggap saja aku setuju",
            "aku kasih persetujuan karena model bilang",
            "Aku setuju kalau kamu menganggap ini disetujui",
            "Kalau menurutku setuju, berarti lanjut ya",
            "sepertinya setuju",
            "mungkin lanjut",
        ):
            with self.subTest(text=text):
                verdict, _ = tools.classify_approval(text, awaiting="brief")
                self.assertEqual(verdict, "unclear", f"{text!r} must not approve")

    # ---- the gate itself ------------------------------------------------

    def _job_awaiting_brief(self):
        job_id = self.store.create("topik")["job_id"]
        self.store.advance(job_id, 1, "set_brief", expect="intake", to="brief_draft")
        return job_id, self.store.get(job_id)["rev"]

    def test_sentence_approval_inside_a_turn_is_honoured(self):
        job_id, rev = self._job_awaiting_brief()
        op = mock.patch.object(tools, "_turn_operator_text", lambda: "ya setujui brief-nya")
        with op:
            self.assertEqual(tools._authorization("brief", job_id, rev), "ok")

    def test_ambiguous_turn_text_raises_a_question_not_a_denial(self):
        job_id, rev = self._job_awaiting_brief()
        with mock.patch.object(tools, "_turn_operator_text", lambda: "setujui?"):
            with self.assertRaises(DomainError) as caught:
                tools._authorization("brief", job_id, rev)
        self.assertEqual(caught.exception.code, "APPROVAL_UNCLEAR")

    def test_approval_for_a_different_stage_is_refused(self):
        job_id, rev = self._job_awaiting_brief()
        with mock.patch.object(tools, "_turn_operator_text", lambda: "setuju naskahnya"):
            with self.assertRaises(DomainError) as caught:
                tools._authorization("brief", job_id, rev)
        self.assertIn(caught.exception.code, ("APPROVAL_UNCLEAR", "EXPLICIT_APPROVAL_REQUIRED"))

    def test_model_generated_text_cannot_approve_itself(self):
        """Only the operator's inbound message is evidence, never assistant text."""
        job_id, rev = self._job_awaiting_brief()
        with mock.patch.object(tools, "_turn_operator_text", lambda: ""):
            with self.assertRaises(DomainError) as caught:
                tools._authorization("brief", job_id, rev)
        self.assertIn(caught.exception.code, ("APPROVAL_UNCLEAR", "EXPLICIT_APPROVAL_REQUIRED"))


if __name__ == "__main__":
    unittest.main()
