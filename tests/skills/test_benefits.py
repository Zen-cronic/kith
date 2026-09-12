import dataclasses
import json
from datetime import UTC, datetime

import pytest

from household.fixtures import FixtureStore
from household.skills import SKILLS, match_skill
from household.skills.base import ToolContext
from household.skills.benefits import BENEFITS
from household.skills.benefits.rules import CLHIA_URL, RULES, cob_order, plan_for, residual

NOW = datetime(2026, 9, 11, 16, 0, tzinfo=UTC)


def demo():
    return FixtureStore().household("demo")


def test_contract_is_frozen_with_clhia_citations_and_a_verify_label():
    assert SKILLS[1] is BENEFITS
    with pytest.raises(dataclasses.FrozenInstanceError):
        BENEFITS.name = "x"
    assert BENEFITS.action_types == ("benefits:claim", "email:send")
    assert BENEFITS.template("benefits:claim").rail == "official-form" and "PREPARE-ONLY" in BENEFITS.template("benefits:claim").label
    assert BENEFITS.template("email:send").rail == "ses-email"
    assert [c.url for c in RULES.citations] == [CLHIA_URL, CLHIA_URL]
    quotes = [c.quote for c in RULES.citations]
    assert "In single custody situations, the custodial parent's plan pays first" in quotes[0]
    assert "The combined payments from all plans cannot exceed 100 per cent of the eligible expense" in quotes[1]
    assert RULES.params["submission window"] == "12 months (insurer-specific, verify)"
    assert [t.name for t in BENEFITS.tools] == ["plan_lookup", "cob_order", "residual"]
    assert BENEFITS.matcher_hints == ("EOB", "dental", "benefits", "Sun Life", "Manulife", "claim")
    assert match_skill("Here is the dental EOB").id == "benefits"
    assert match_skill("allowance for the dental book fair").id == "allowance"  # tie-break: earlier skill wins


def test_birthday_rule_orders_the_parents_plans_for_a_child():
    household = demo()
    for child in ("kofi", "mei"):
        order = cob_order(household, child, "September 3, 2026")
        assert (order.primary["plan_id"], order.secondary["plan_id"]) == ("SL-1", "ML-7")
        assert "03-14" in " ".join(order.reasons) and "earlier in the year" in " ".join(order.reasons)


def test_an_adults_own_plan_pays_first_and_the_spouse_plan_second():
    household = demo()
    daniel = cob_order(household, "daniel", "September 3, 2026")
    assert (daniel.primary["plan_id"], daniel.secondary["plan_id"]) == ("ML-7", "SL-1")
    ama = cob_order(household, "ama", "September 3, 2026")
    assert (ama.primary["plan_id"], ama.secondary["plan_id"]) == ("SL-1", "ML-7")
    assert "12 months (insurer-specific, verify)" in daniel.reasons[0]
    assert cob_order(household, "zed", "x").primary is None
    household.plans = [p for p in household.plans if p["member"] != "daniel"]
    assert cob_order(household, "daniel", "x").primary["plan_id"] == "SL-1"
    assert cob_order(household, "kofi", "x").primary["plan_id"] == "SL-1" and cob_order(household, "kofi", "x").secondary is None


def test_residual_never_goes_below_zero():
    assert residual("180.00", "144.00") == "36.00"
    assert residual("100.00", "120.00") == "0.00"
    with pytest.raises(ValueError):
        residual("abc", "1")


def test_tools_are_read_only_and_deterministic():
    household = demo()
    context = ToolContext(household=household, actor=household.member("ama"), now=NOW)
    tools = BENEFITS.build_tools(context)
    assert json.loads(tools["plan_lookup"](member_id="daniel")) == plan_for(household, "daniel")
    assert "error" in json.loads(tools["plan_lookup"](member_id="kofi"))
    order = json.loads(tools["cob_order"](subject_member_id="daniel", service_date="September 3, 2026"))
    assert order["primary"]["plan_id"] == "ML-7" and order["secondary"]["plan_id"] == "SL-1"
    assert json.loads(tools["residual"](amount="180.00", primary_paid="144.00"))["residual"] == "36.00"
    assert "error" in json.loads(tools["residual"](amount="x", primary_paid="1"))
    assert household == demo()
    assert [c["tool"] for c in context.log] == ["plan_lookup", "plan_lookup", "cob_order", "residual", "residual"]
