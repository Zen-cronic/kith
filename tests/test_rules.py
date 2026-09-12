from household.rules import FIDELITY_RULE, RULES, lookup, policy_for


def test_n4_rule_is_bound_to_the_real_form_and_the_ltb_brochure() -> None:
    rule = lookup("ltb-n4")
    assert rule is not None and rule.id == "ON-LTB-N4"
    assert rule.policy == "read-and-explain-only"
    assert "es" in rule.statement and "en" in rule.statement
    sources = " ".join(c.source for c in rule.citations)
    assert "Form N4" in sources and "Important Information about Your Hearing" in sources
    assert any("does not usually provide interpreters" in c.quote for c in rule.citations)


def test_every_rule_has_bilingual_statement_and_handoff() -> None:
    for rule in (*RULES, FIDELITY_RULE):
        assert rule.statement["en"] and rule.statement["es"]
        assert rule.handoff["en"] and rule.handoff["es"]


def test_unknown_class_defaults_to_assist() -> None:
    assert lookup("school-letter") is None
    assert policy_for("school-letter") == "assist"
