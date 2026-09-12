"""Regress the model-facing schema, not just Pydantic's input defaults."""

import pytest
from pydantic import ValidationError
from strands.tools.structured_output.structured_output_tool import StructuredOutputTool

from household.agents.roster import ROSTER
from household.schemas import ActionPlan, AuthorityVerdict, IntakeReading, ProposedAction


def _check(model):
    schema = StructuredOutputTool(model).tool_spec["inputSchema"]["json"]
    for name, field in model.model_fields.items():
        if field.is_required():
            continue
        prop = schema["properties"][name]
        if field.default is None:
            assert "null" in prop["type"], (model.__name__, name)
            continue
        assert name in schema["required"], (model.__name__, name)
        assert "null" not in prop.get("type", []), (model.__name__, name)
    return schema


def test_strands_does_not_advertise_null_for_nonnullable_defaults():
    for spec in ROSTER:
        _check(spec.schema)


def test_nested_items_are_flat_and_non_null_too():
    # Strands inlines nested models under items; the non-null fix must reach them and they must stay flat.
    plan = _check(ActionPlan)
    item = plan["properties"]["actions"]["items"]
    assert item["title"] == "ProposedAction"
    assert "payload" in item["required"] and "evidence_refs" in item["required"] and "claimed_grant_id" in item["required"]
    assert all("anyOf" not in prop and "null" not in prop.get("type", []) for prop in item["properties"].values())
    assert item["properties"]["action_type"]["type"] == "string" and item["properties"]["rail"]["type"] == "string"
    payload = item["properties"]["payload"]["items"]
    assert payload["required"] == ["key", "value"]
    verdict = _check(AuthorityVerdict)
    decision = verdict["properties"]["decisions"]["items"]
    assert decision["properties"]["outcome"]["type"] == "string" and "approver_ids" in decision["required"]
    assert all("anyOf" not in prop for prop in decision["properties"].values())


def test_empty_lists_remain_valid_but_null_lists_do_not():
    values = dict(document_class="text-request", issuer="Kofi", subject_hint="", summary_en="A request",
                  evidence=[], confidence="high", transcribed_lines=[])
    assert IntakeReading(**values).amounts == [] and IntakeReading(**values, dates=[]).dates == []
    with pytest.raises(ValidationError):
        IntakeReading(**values, amounts=None)
    with pytest.raises(ValidationError):
        ActionPlan(actions=None)
    with pytest.raises(ValidationError):
        ProposedAction(action_type="allowance:transfer", rail="internal-ledger", subject_member_id="kofi", recipient="",
                       amount_text="8.00", currency="CAD", payload=None, evidence_refs=[], rationale="", claimed_grant_id="")
