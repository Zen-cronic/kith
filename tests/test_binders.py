from household.binders import bind_source_values, form_excerpt, normalized
from household.schemas import IntakeReading


def reading(**overrides) -> IntakeReading:
    base = dict(document_class="text-request", issuer="School", subject_hint="", summary_en="Letter", evidence=[],
                confidence="high", transcribed_lines=[], amounts=[], dates=[])
    return IntakeReading(**{**base, **overrides})


def test_source_form_is_an_unchanged_excerpt_not_a_generated_permission():
    source = "Letter\nChoose a time: ____\nConsent: [ ]\nName: ____\nThanks"
    assert form_excerpt(source) == "Choose a time: ____\nConsent: [ ]\nName: ____"
    assert form_excerpt("Please call the office") is None


def test_iso_date_is_restored_only_from_the_one_verbatim_source_date():
    source = "Please return the form by Friday, October 9, 2026. The trip is Thursday, October 15, 2026."
    r = reading(dates=[dict(label="return by", date_text="2026-10-09", quote="Please return the form by Friday, October 9, 2026.")])
    assert bind_source_values(r, source) == []
    assert r.dates[0].date_text == "Friday, October 9, 2026"


def test_fabricated_date_quote_is_not_repaired_from_an_unrelated_date():
    r = reading(dates=[dict(label="Return", date_text="2026-10-09", quote="Invented deadline October 9, 2026")])
    assert bind_source_values(r, "A different event October 9, 2026")
    assert r.dates[0].date_text == "2026-10-09"


def test_amounts_must_appear_verbatim_and_their_quotes_must_be_in_the_source():
    source = "billed $180.00, Manulife paid $144.00"
    ok = reading(amounts=[dict(label="billed", amount_text="$180.00", quote="billed $180.00, Manulife paid $144.00")])
    assert bind_source_values(ok, source) == []
    bad = reading(amounts=[dict(label="billed", amount_text="$999.00", quote=source), dict(label="paid", amount_text="$144.00", quote="paid $9")])
    issues = bind_source_values(bad, source)
    assert any("$999.00" in i for i in issues) and any("quote is not in the source" in i for i in issues)
    assert normalized("  a \n b ") == "a b"
