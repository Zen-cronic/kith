from household.config import load_settings
from household.pipeline import run_session
from household.providers.fake import FakeModel
from household.routine import bind_source_values, form_excerpt
from household.schemas import DocumentReading

FAKE = load_settings(provider="fake")


class FaultModel(FakeModel):
    def __init__(self, fault):
        super().__init__()
        self.fault = fault

    def _payload(self, role, fixture_id, language, last_text):
        p = super()._payload(role, fixture_id, language, last_text)
        if role == "reader" and self.fault == "date":
            p['deadlines'][0]['date_text'] = '2026-10-09'
        if role == "critic" and self.fault == "missing-checks":
            p.update(decision="approve", checks=[], revision_notes=[])
        if role == "drafter" and self.fault == "payee":
            p['body_target'] = p['body_target'].replace('Danforth Park Public School', 'Escuela Pública Danforth Park')
            for step in p.get('preparation_steps', []):
                step['instruction_target'] = step['instruction_target'].replace('Danforth Park Public School', 'Escuela Pública Danforth Park')
        if role == "router":
            p.update(statement_en="Staff will send this in ten minutes", statement_target="El personal lo enviará en diez minutos")
        return p


def test_source_form_is_an_unchanged_excerpt_not_a_generated_permission():
    source = "Letter\nChoose a time: ____\nConsent: [ ]\nName: ____\nThanks"
    assert form_excerpt(source) == "Choose a time: ____\nConsent: [ ]\nName: ____"
    assert form_excerpt("Please call the office") is None


def test_source_dates_are_bound_and_raw_model_output_retained():
    result = run_session("school-trip-letter", settings=FAKE, model=FaultModel("date"))
    assert result.reading.deadlines[0].date_text == "Friday, October 9, 2026"
    assert result.model_reading.deadlines[0].date_text == "2026-10-09"
    assert result.outcome == "proceed" and not result.source_issues
    assert "Staff will send" not in result.card.statement_en
    assert "Nothing has been signed" in result.card.statement_en
    assert "I give permission" in result.source_form
    assert "original" in result.drafts[-1].body_en


def test_fabricated_date_quote_is_not_repaired_from_an_unrelated_date():
    reading = DocumentReading(document_class="school-letter", title="Letter", issuer="School", what_it_is="Letter",
        what_it_asks="Return", stakes="low", stakes_reason="Routine", evidence=[], confidence=1,
        deadlines=[dict(label="Return", date_text="2026-10-09", quote="Invented deadline October 9, 2026")])
    assert bind_source_values(reading, "A different event October 9, 2026")
    assert reading.deadlines[0].date_text == "2026-10-09"


def test_approval_without_draft_checks_cannot_release_a_routine_draft():
    result = run_session("school-trip-letter", settings=FAKE, model=FaultModel("missing-checks"))
    assert result.execution_order.count("drafter") == 3
    assert result.outcome == "escalate"
    assert result.verdicts[-1].decision == "revise"
    assert result.model_verdicts[-1].decision == "approve"
    assert result.guard.override and result.guard.model_outcome == "proceed"
    assert any("draft-facts" in issue for issue in result.verdicts[-1].revision_notes)


def test_translated_cheque_payee_is_sent_back_and_never_released():
    result = run_session("school-trip-letter", settings=FAKE, model=FaultModel("payee"))
    assert result.outcome == "escalate"
    assert any("Danforth Park Public School" in issue for issue in result.verdicts[-1].revision_notes)


def test_source_contract_catches_an_omitted_original_form_field():
    from household.routine import draft_issues
    from household.schemas import CriticVerdict, Draft

    source = "Preferred time: ____\nParent name: ____ Student: ____"
    draft = Draft(kind="form-checklist", title="Prepare", body_en="Complete the original slip with your preferred time.",
                  body_target="Complete el talón original con su horario preferido.", facts_used=["Preferred time: ____"],
                  preparation_steps=[dict(instruction_en="Complete the original slip with your preferred time.",
                  instruction_target="Complete el talón original con su horario preferido.", source_quote="Preferred time: ____")])
    verdict = CriticVerdict(decision="approve", checks=[dict(name=name, passed=True, detail="Model says yes")
        for name in ["draft-facts", "draft-assumptions"]])
    assert any("Parent name" in issue for issue in draft_issues(draft, verdict, source))
