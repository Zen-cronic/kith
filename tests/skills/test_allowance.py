import dataclasses
import json
from datetime import UTC, datetime, timedelta

import pytest

from household.fixtures import FixtureStore
from household.model import ActionProposal, ActionRecord, Receipt
from household.skills import SKILLS, skill_for
from household.skills.allowance import ALLOWANCE
from household.skills.allowance.rules import allowance_rule
from household.skills.base import Skill, ToolContext

NOW = datetime(2026, 9, 11, 16, 0, tzinfo=UTC)


def demo():
    return FixtureStore().household("demo")


def ctx(household=None, actor="kofi"):
    household = household or demo()
    return ToolContext(household=household, actor=household.member(actor), now=NOW)


def test_skill_contract_is_frozen_and_ordered():
    assert SKILLS[0] is ALLOWANCE and skill_for("allowance") is ALLOWANCE
    assert isinstance(ALLOWANCE, Skill) and dataclasses.is_dataclass(ALLOWANCE)
    with pytest.raises(dataclasses.FrozenInstanceError):
        ALLOWANCE.id = "other"
    with pytest.raises(dataclasses.FrozenInstanceError):
        ALLOWANCE.rules.title = "other"
    assert ALLOWANCE.action_types == ("allowance:transfer",)
    assert [t.name for t in ALLOWANCE.tools] == ["allowance_balance", "propose_allowance"]
    assert ALLOWANCE.template("allowance:transfer").rail == "internal-ledger"
    assert ALLOWANCE.matcher_hints == ("allowance", "pocket money", "book fair", "chores")
    assert len(ALLOWANCE.prompt_block.splitlines()) <= 25 and "allowance_balance" in ALLOWANCE.prompt()
    assert set(ALLOWANCE.evidence_schema.model_fields) == {"member_id", "amount_text", "purpose", "when_text"}
    assert ALLOWANCE.form_builders == {}  # the allowance skill owns no official form


def test_rules_come_from_the_childs_account_and_count_this_weeks_receipts():
    household = demo()
    rule = allowance_rule(household, "kofi", NOW)
    assert rule["auto_limit"] == "10.00" and rule["weekly"] == "15.00" and rule["spent_this_week"] == "0.00"
    proposal = ActionProposal(id="hist", skill_id="allowance", action_type="allowance:transfer", rail="internal-ledger",
                              actor_member_id="kofi", subject_member_id="kofi", amount="6.00", idempotency_key="hist")
    at = (NOW - timedelta(days=2)).isoformat()
    household.actions.append(ActionRecord(proposal=proposal, created_at=at))
    household.receipts.append(Receipt(id="r-hist", action_id="hist", rail="internal-ledger", mode="SIMULATED", request_digest="d",
                                      response_digest="", at=at, executed_under_grant="rule:minor-allowance", label_reason="history"))
    rule = allowance_rule(household, "kofi", NOW)
    assert rule["spent_this_week"] == "6.00" and rule["left_this_week"] == "9.00"
    assert allowance_rule(household, "ama", NOW) is None


def test_tools_read_and_propose_but_never_move_money():
    household = demo()
    context = ctx(household)
    tools = ALLOWANCE.build_tools(context)
    balance = json.loads(tools["allowance_balance"](member_id="kofi"))
    assert balance["account_id"] == "allow-kofi" and balance["balance"] == "42.00"
    assert "error" in json.loads(tools["allowance_balance"](member_id="ama"))
    proposal = json.loads(tools["propose_allowance"](member_id="kofi", amount="8.00", memo="book fair"))
    assert proposal["action_type"] == "allowance:transfer" and proposal["rail"] == "internal-ledger"
    assert proposal["subject_member_id"] == "kofi" and proposal["recipient"] == "hh-main" and proposal["amount_text"] == "8.00"
    assert proposal["payload"] == [{"key": "memo", "value": "book fair"}] and proposal["claimed_grant_id"] == ""
    assert "error" in json.loads(tools["propose_allowance"](member_id="kofi", amount="lots", memo="x"))
    assert household == demo()  # nothing moved, nothing recorded in the ledger
    assert [c["tool"] for c in context.log] == ["allowance_balance", "allowance_balance", "propose_allowance", "propose_allowance"]
