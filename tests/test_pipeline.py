"""End-to-end through the real Strands Graph with the fake provider."""

from household.config import load_settings
from household.pipeline import run_session
from household.providers.fake import FakeModel

FAKE = load_settings(provider="fake")


class NeverApprovedModel(FakeModel):
    def _payload(self, role, fixture_id, language, last_text):
        payload = super()._payload(role, fixture_id, language, last_text)
        if role == "critic":
            payload.update(decision="revise", rule_id=None, revision_notes=["Remove the unverified fact."])
        if role == "router":
            payload.update(outcome="proceed", statement_en="The draft is approved.")
        return payload


def test_revision_exhaustion_never_releases_an_unapproved_draft() -> None:
    result = run_session("school-trip-letter", "es", settings=FAKE, model=NeverApprovedModel())
    assert result.graph_status == "completed"
    assert result.execution_order.count("drafter") == FAKE.max_revisions + 1
    assert result.verdicts[-1].decision == "revise"
    assert result.outcome == "escalate"
    assert result.guard.rule_id == "DRAFT-REVIEW-REQUIRED"
    assert result.card and result.card.outcome == "escalate"
    assert "not approved" in result.card.statement_en


def test_n4_is_refused_by_the_named_rule_and_the_drafter_is_bypassed() -> None:
    r = run_session("ltb-n4", "es", settings=FAKE)
    assert r.graph_status == "completed"
    assert r.execution_order == ["reader", "interpreter", "critic", "router"]
    assert r.outcome == "escalate"
    assert r.guard.rule_id == "ON-LTB-N4" and not r.guard.override
    assert r.verdicts[-1].decision == "refuse" and r.verdicts[-1].rule_id == "ON-LTB-N4"
    assert r.fidelity["band"] == "reliable"
    assert r.card and r.card.outcome == "escalate"
    assert "Form N4" in r.card.statement_en and "Formulario N4" in r.card.statement_target
    assert "interpreters" in r.card.statement_en
    assert r.card.when and r.card.who and r.card.safe_today_target


def test_school_letter_loops_through_the_critic_then_proceeds() -> None:
    r = run_session("school-trip-letter", "es", settings=FAKE)
    assert r.graph_status == "completed"
    assert r.execution_order == ["reader", "interpreter", "drafter", "critic", "drafter", "critic", "router"]
    assert [v.decision for v in r.verdicts] == ["revise", "approve"]
    assert [d.revision for d in r.drafts] == [1, 2]
    assert "416-555-0199" in r.drafts[0].body_en and "416-555-0199" not in r.drafts[1].body_en
    assert r.outcome == "proceed" and not r.guard.override
    assert r.card and r.card.outcome == "proceed"


def test_lossy_back_translation_trips_the_fidelity_floor() -> None:
    r = run_session("benefits-appointment-letter", "es", settings=FAKE)
    assert r.fidelity["band"] == "unreliable"
    assert r.fidelity["numbers_missing"]
    assert r.outcome == "escalate"
    assert r.guard.rule_id == "FIDELITY-FLOOR" and not r.guard.override
    assert r.card and r.card.outcome == "escalate" and "not confident" in r.card.statement_en


def test_derived_fallback_runs_for_a_real_form_without_canned_output() -> None:
    r = run_session("ltb-n5", "es", settings=FAKE)
    assert r.graph_status == "completed"
    assert r.outcome == "escalate" and r.guard.rule_id == "ON-LTB-EVICTION-NOTICE"
    assert "derived from fixture metadata" in (r.reading.what_it_is if r.reading else "")


def test_spanish_handoff_fields_are_available_for_routine_and_escalated_letters() -> None:
    for fixture in ["school-trip-letter", "ltb-n4", "benefits-appointment-letter", "ltb-n5"]:
        result = run_session(fixture, "es", settings=FAKE)
        assert result.card
        assert result.card.who_target and result.card.who_target != result.card.who
        assert result.card.when_target and result.card.when_target != result.card.when
        if result.outcome == "escalate":
            assert "No appointment or response time has been confirmed" in result.card.when
            assert "No se ha confirmado" in result.card.when_target


def test_unapproved_draft_gets_a_translated_code_handoff_without_a_booking_promise() -> None:
    result = run_session("school-trip-letter", "es", settings=FAKE, model=NeverApprovedModel())
    assert result.card
    assert "personal" in result.card.who_target
    assert "No se ha confirmado" in result.card.when_target
    assert "se sentará" not in result.card.next_step_target


def test_n4_demo_preserves_notice_order_distinction_in_reading_and_takeaway() -> None:
    result = run_session("ltb-n4", "es", settings=FAKE)
    assert result.reading and result.interpretation and result.card
    assert "not an eviction order" in result.reading.what_it_is
    assert "do not have to leave" in result.reading.what_it_asks
    assert "no es una orden de desalojo" in result.interpretation.target_text
    assert "no es una orden de desalojo" in result.card.summary_target
    assert "21/09/2026" in result.card.summary_target and "$1,850.00" in result.card.summary_target
    assert "15 minutes" not in result.card.model_dump_json()


class CapturingInputModel(FakeModel):
    def __init__(self):
        super().__init__()
        self.inputs = {}

    def _payload(self, role, fixture_id, language, last_text):
        self.inputs.setdefault(role, []).append(last_text)
        return super()._payload(role, fixture_id, language, last_text)


def test_downstream_graph_inputs_use_typed_outputs_and_one_translation_source() -> None:
    from household.agents.context import reading_text

    model = CapturingInputModel()
    result = run_session("school-trip-letter", "es", settings=FAKE, model=model)
    assert result.reading
    interpreter_input = model.inputs["interpreter"][0]
    assert reading_text(result.reading) in interpreter_input
    assert "Original Task:" not in interpreter_input
    assert "41 Hillside Road" not in interpreter_input
    assert "Visitor language:" not in model.inputs["reader"][0]
    critic_inputs = model.inputs["critic"]
    assert any('"body_target"' in text and '"back_translation"' in text for text in critic_inputs)
    assert any('"revision_notes"' in text for text in model.inputs["drafter"])
    assert result.execution_order.count("drafter") == 2


def test_drafting_and_review_receive_source_and_previous_draft_without_changing_translation_scope() -> None:
    import json

    model = CapturingInputModel()
    result = run_session("school-trip-letter", "es", settings=FAKE, model=model)
    # The summary omits this original-form field. Both author and reviewer need it.
    assert "Emergency contact name" in model.inputs["drafter"][0]
    assert "Emergency contact name" in model.inputs["critic"][0]
    assert "Emergency contact name" not in model.inputs["interpreter"][0]
    revision_input = model.inputs["drafter"][1]
    previous_json = revision_input.split("Previous draft to revise:\n", 1)[1].split("\n\n", 1)[0]
    assert json.loads(previous_json) == result.drafts[0].model_dump()
    assert '"revision_notes"' in revision_input


class UnsafeN4Model(CapturingInputModel):
    def _payload(self, role, fixture_id, language, last_text):
        payload = super()._payload(role, fixture_id, language, last_text)
        if role == "reader":
            payload.update(what_it_is="The tenant owes $9,999.00.", what_it_asks="The tenant must pay or move out tomorrow.", amounts=["$9,999.00"])
            payload["deadlines"] = [{"label": "Must leave", "date_text": "01/01/2099", "quote": "Invented date"}]
        if role == "router":
            payload.update(summary_en="You must move out.", summary_target="Debe mudarse.",
                           when="Staff will call in 15 minutes", when_target="15 minutos", rule_citation=None)
        return payload


def test_n4_policy_binds_before_translation_and_preserves_raw_model_evidence() -> None:
    model = UnsafeN4Model()
    result = run_session("ltb-n4", "es", settings=FAKE, model=model)
    assert result.model_reading.what_it_asks == "The tenant must pay or move out tomorrow."
    assert result.reading_policy["rule_id"] == "ON-LTB-N4"
    assert "not an eviction order" in result.reading.what_it_is
    assert "do not have to leave" in result.reading.what_it_asks
    assert result.reading.amounts == ["$1,850.00"]
    assert result.reading.deadlines[0].date_text == "21/09/2026"
    source_to_interpreter = model.inputs["interpreter"][0]
    assert "do not have to leave" in source_to_interpreter
    assert "$9,999.00" not in source_to_interpreter and "01/01/2099" not in source_to_interpreter
    assert "You must move out" not in result.card.summary_en
    assert "no es una orden de desalojo" in result.card.summary_target
    assert "no tiene que mudarse" in result.card.summary_target
    assert "No appointment or response time has been confirmed" in result.card.when
    assert "Form N4" in result.card.rule_citation
    assert "drafter" not in result.execution_order


def test_n4_policy_never_borrows_invented_values_when_source_fields_are_absent() -> None:
    from household.reading_policy import bind_reading
    from household.schemas import DocumentReading

    payload = UnsafeN4Model()._payload("reader", "ltb-n4", "es", "")
    reading = DocumentReading.model_validate(payload)
    bound = bind_reading(reading, "Notice to End your Tenancy For Non-payment of Rent N4")
    assert bound.reading.deadlines == [] and bound.reading.amounts == []
    assert "could not be verified" in bound.summary("en")
    assert "$9,999.00" not in bound.summary("en")
    assert bind_reading(reading, "Please book a school trip. N4 is a classroom code.") is None


def test_streamed_n4_reading_is_bound_before_it_is_exposed() -> None:
    import asyncio

    from household.pipeline import stream_session

    async def collect():
        return [event async for event in stream_session("ltb-n4", "es", settings=FAKE, model=UnsafeN4Model())]
    events = asyncio.run(collect())
    reader = next(event for event in events if event.get("node_id") == "reader" and event["event"] == "node_done")
    assert "not an eviction order" in reader["output"]["what_it_is"]
    assert reader["reading_policy"]["rule_id"] == "ON-LTB-N4"
    assert "must pay or move" not in reader["output"]["what_it_asks"]
