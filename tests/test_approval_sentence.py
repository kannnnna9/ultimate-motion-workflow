import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from ump_core.jobs import STAGES, DomainError, JobStore
from ump_core import tools


def _make_event(text, message_id, media_urls=(), media_types=(), message_type="text"):
    """A minimal inbound Telegram DM event, shaped like the gateway's MessageEvent."""
    from gateway.platforms.event import MessageType
    platform = type("Platform", (), {"value": "telegram"})()
    source = type("Source", (), {
        "platform": platform, "chat_type": "dm", "user_id": "892369397",
        "chat_id": "892369397", "is_bot": False,
    })()
    kind = getattr(MessageType, str(message_type).upper(), message_type)
    return type("Event", (), {
        "source": source, "message_id": message_id, "text": text,
        "media_urls": list(media_urls), "media_types": list(media_types),
        "message_type": kind, "raw_message": None,
    })()


def _make_gateway():
    return type("Gateway", (), {"_is_user_authorized_for_source": lambda self, source, **kw: True})()


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
            "storyboard",
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
        ):
            with self.subTest(text=text):
                verdict, _ = tools.classify_approval(text, awaiting="brief")
                self.assertEqual(verdict, "unclear", f"{text!r} must not approve")

    def test_revision_request_is_a_rejection_not_an_approval(self):
        """Asking for a change is the opposite of approving what exists."""
        for text in ("ganti warnanya jadi biru", "revisi dulu dong", "kurang pas", "perbaiki beat 3"):
            with self.subTest(text=text):
                verdict, _ = tools.classify_approval(text, awaiting="brief")
                self.assertEqual(verdict, "reject", f"{text!r} must read as a change request")

    def test_short_plain_approvals_are_accepted(self):
        """The operator should not have to type identifiers or a command form."""
        for text in ("setuju", "ya setuju", "oke lanjut", "gas", "sip", "mantap", "lanjutkan",
                     "oke", "ok", "Oke", "Setuju", "Gas", "oke deh", "oke aja", "ok sip"):
            with self.subTest(text=text):
                verdict, stage = tools.classify_approval(text, awaiting="naskah")
                self.assertEqual((verdict, stage), ("approve", "naskah"))

    def test_bare_oke_is_an_approval_not_a_hedge(self):
        """"Oke" is the most common Indonesian yes; requiring "oke lanjut" was a defect."""
        verdict, stage = tools.classify_approval("Oke", awaiting="brief")
        self.assertEqual((verdict, stage), ("approve", "brief"))

    def test_oke_inside_a_question_still_does_not_approve(self):
        """Adding a bare "oke" must not make question or hedge lines approve."""
        for text in ("oke, tapi apa brief-nya sudah benar?", "oke kalau memang begitu",
                     "oke nanti aja", "oke belum"):
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

    def test_inbound_hook_records_a_plain_approval_sentence(self):
        """The hook must capture "Setuju", not only the exact UMP APPROVE command.

        It used to return early for anything that was not the command form, so the
        sentence classifier downstream never received the operator's message and
        every short approval failed with APPROVAL_UNCLEAR.
        """
        for text in ("Setuju", "Oke", "Gas", "setuju", "gas lanjut"):
            with self.subTest(text=text):
                tools._EVENTS.clear()
                tools.inbound(_make_event(text, "4242"), _make_gateway())
                self.assertEqual(tools._turn_operator_text(), text)

    def test_inbound_hook_still_ignores_non_text_and_media(self):
        """Widening the hook must not make stickers, photos or empty edits approve."""
        cases = [
            _make_event("", "4243"),
            _make_event("Setuju", "4244", media_urls=["x.jpg"], media_types=["image/jpeg"]),
            _make_event("Setuju", "4245", message_type="photo"),
        ]
        for event in cases:
            with self.subTest(event=event.message_id):
                tools._EVENTS.clear()
                tools.inbound(event, _make_gateway())
                self.assertEqual(tools._turn_operator_text(), "")

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

    def test_explicit_command_form_is_classified_as_approval(self):
        """The documented `UMP APPROVE …` line must work even without the event map.

        The event map is per-process: when the tool call runs in an interpreter
        that never saw the inbound update, the exact command form still has to
        be accepted. Regression: it fell through to prose classification, where
        `style_frame` was not a known stage name and `APPROVE` alone was read as
        an ambiguous sentence.
        """
        for stage in ("brief", "research", "naskah", "style", "script_style", "preview"):
            with self.subTest(stage=stage):
                text = f"UMP APPROVE jPqXBh_NicNHLJkh 6 {stage} wBPkfJYBn3wOCRHd"
                verdict, found = tools.classify_approval(text, awaiting=stage)
                self.assertEqual((verdict, found), ("approve", stage))

    def test_explicit_command_for_another_stage_does_not_approve_the_awaiting_one(self):
        text = "UMP APPROVE jPqXBh_NicNHLJkh 6 preview wBPkfJYBn3wOCRHd"
        verdict, stage = tools.classify_approval(text, awaiting="script_style")
        self.assertEqual((verdict, stage), ("approve", "preview"))

    def test_frame_synonyms_map_to_the_script_style_stage(self):
        for text in ("setuju style frame", "setuju style_frame", "setuju frame-nya"):
            with self.subTest(text=text):
                verdict, stage = tools.classify_approval(text, awaiting="script_style")
                self.assertEqual((verdict, stage), ("approve", "script_style"))
        # A bare "gas" approves whatever is awaiting, which here is the frame.
        verdict, stage = tools.classify_approval("gas", awaiting="script_style")
        self.assertEqual((verdict, stage), ("approve", "script_style"))

    def test_explicit_command_survives_a_fresh_process(self):
        """The line must still authorise when the tool call runs elsewhere.

        Regression: the evidence lived only in the gateway process's dict, and
        the entry stored the whole phrase while `_authorization` compared it to
        a bare stage name, so the documented command form never authorised
        anything.
        """
        job_id, rev = self._job_awaiting_brief()
        artifact_id = "wBPkfJYBn3wOCRHd"
        with mock.patch.object(tools, "_home", lambda: Path(self.tmp.name)), \
                mock.patch.dict(os.environ, {"HERMES_SESSION_PLATFORM": "telegram",
                                             "HERMES_SESSION_CHAT_TYPE": "dm",
                                             "HERMES_SESSION_CHAT_ID": "42",
                                             "HERMES_SESSION_USER_ID": "42",
                                             "HERMES_SESSION_MESSAGE_ID": "9001"}):
            tools._remember_command((tools._home(), "42", "42", "9001"),
                                    f"UMP APPROVE {job_id} {rev} brief {artifact_id}", time.monotonic())
            # setUp stubs the gateway away; this test is about the real lookup.
            from gateway.session_context import get_session_env
            with mock.patch.object(tools, "_EVENTS", {}), \
                    mock.patch.object(tools, "_turn_operator_text", lambda: ""), \
                    mock.patch.object(tools, "_event_key",
                                      lambda: (tools._home(), "42", "42", "9001")):
                self.assertEqual(tools._authorization("brief", job_id, rev), "ok")
                # One message authorises one artefact, never a second time.
                with self.assertRaises(DomainError):
                    tools._authorization("brief", job_id, rev)

    def test_command_for_another_stage_never_authorises(self):
        job_id, rev = self._job_awaiting_brief()
        artifact_id = "wBPkfJYBn3wOCRHd"
        with mock.patch.object(tools, "_home", lambda: Path(self.tmp.name)), \
                mock.patch.dict(os.environ, {"HERMES_SESSION_PLATFORM": "telegram",
                                             "HERMES_SESSION_CHAT_TYPE": "dm",
                                             "HERMES_SESSION_CHAT_ID": "42",
                                             "HERMES_SESSION_USER_ID": "42",
                                             "HERMES_SESSION_MESSAGE_ID": "9002"}):
            tools._remember_command((tools._home(), "42", "42", "9002"),
                                    f"UMP APPROVE {job_id} {rev} preview {artifact_id}", time.monotonic())
            with mock.patch.object(tools, "_EVENTS", {}), \
                    mock.patch.object(tools, "_turn_operator_text", lambda: ""):
                with self.assertRaises(DomainError):
                    tools._authorization("brief", job_id, rev)

    def test_inbound_hook_records_operator_text_without_crashing(self):
        """The hook mixes stamps and the turn text in one dict; it must survive both.

        Regression: the prune loop did `now - v[0]` over every value, so the
        first plain text message raised TypeError and no later approval could
        ever be recorded.
        """
        from types import SimpleNamespace
        from gateway.platforms.event import MessageType

        artifact_id = "wBPkfJYBn3wOCRHd"

        def event(mid, text):
            return SimpleNamespace(
                text=text, message_id=mid, message_type=MessageType.TEXT,
                media_urls=[], media_types=[], raw_message=None,
                source=SimpleNamespace(platform=SimpleNamespace(value="telegram"),
                                       chat_type="dm", user_id="42", chat_id="42", is_bot=False))

        gateway = SimpleNamespace(_is_user_authorized_for_source=lambda *a, **k: True)
        with mock.patch.object(tools, "_home", lambda: Path(self.tmp.name)), \
                mock.patch.object(tools, "_EVENTS", {}):
            tools.inbound(event("1", "halo"), gateway)
            tools.inbound(event("2", f"UMP APPROVE jPqXBh_NicNHLJkh 6 script_style {artifact_id}"), gateway)
            recorded = json.loads(tools._command_path().read_text())
        self.assertEqual(recorded["42:42:2"]["stage"], "script_style")

    def test_model_generated_text_cannot_approve_itself(self):
        """Only the operator's inbound message is evidence, never assistant text."""
        job_id, rev = self._job_awaiting_brief()
        with mock.patch.object(tools, "_turn_operator_text", lambda: ""):
            with self.assertRaises(DomainError) as caught:
                tools._authorization("brief", job_id, rev)
        self.assertIn(caught.exception.code, ("APPROVAL_UNCLEAR", "EXPLICIT_APPROVAL_REQUIRED"))


if __name__ == "__main__":
    unittest.main()
