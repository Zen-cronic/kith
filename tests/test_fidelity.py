from household.fidelity import score_fidelity


def test_faithful_round_trip_is_reliable() -> None:
    src = "Pay $1,850.00 by the termination date, 21/09/2026, or move out by that date."
    back = "You must pay $1,850.00 by the termination date, 21/09/2026, or move out by that date."
    s = score_fidelity(src, back)
    assert s.band == "reliable"
    assert s.numbers_missing == []


def test_dropped_date_can_never_be_reliable() -> None:
    src = "Your appointment is on Tuesday, September 22, 2026 at 10:30 a.m. Bring photo ID."
    back = "You have an appointment. Bring photo ID."
    s = score_fidelity(src, back)
    assert s.numbers_missing
    assert s.band in {"caution", "unreliable"}
    assert s.score < 0.70


def test_unrelated_text_is_unreliable() -> None:
    s = score_fidelity("Pay $12.00 by Friday, October 9, 2026.", "The weather is pleasant today.")
    assert s.band == "unreliable"


def test_dropped_or_added_negation_requires_review() -> None:
    source = "You must not sign this form before September 30, 2026."
    changed = "You must sign this form before September 30, 2026."
    for original, back in [(source, changed), (changed, source)]:
        result = score_fidelity(original, back)
        assert result.band == "unreliable"
        assert result.meaning_warnings == ["Negation changed; a person must check the meaning."]


def test_negation_contractions_preserve_the_check() -> None:
    result = score_fidelity("Do not sign the form.", "Don't sign the form.")
    assert result.meaning_warnings == []
    assert result.band == "reliable"


def test_empty_round_trip_is_not_reliable() -> None:
    assert score_fidelity("", "").band == "unreliable"
