"""Helpers shared by the graph conditions, the pipeline and the fake provider."""

from __future__ import annotations

import json
from typing import Any, TypeVar

from pydantic import BaseModel

from ..model import Household
from ..schemas import ActionPlan, AuthorityVerdict, Briefing, CaseAssignment, ExecutionReport, IntakeReading

T = TypeVar("T", bound=BaseModel)


def structured_from_node(node_result: Any, schema: type[T]) -> T | None:
    """Pull the typed structured output out of a Strands NodeResult, or None."""
    if node_result is None:
        return None
    agent_result = getattr(node_result, "result", node_result)
    output = getattr(agent_result, "structured_output", None)
    if isinstance(output, schema):
        return output
    if isinstance(output, BaseModel):
        return schema.model_validate(output.model_dump())
    return None


def state_output(state: Any, node_id: str, schema: type[T]) -> T | None:
    return structured_from_node(state.results.get(node_id), schema)


def reading_of(state: Any) -> IntakeReading | None:
    return state_output(state, "intake", IntakeReading)


def assignment_of(state: Any) -> CaseAssignment | None:
    return state_output(state, "matcher", CaseAssignment)


def plan_of(state: Any) -> ActionPlan | None:
    return state_output(state, "planner", ActionPlan)


def verdict_of(state: Any) -> AuthorityVerdict | None:
    return state_output(state, "authority", AuthorityVerdict)


def report_of(state: Any) -> ExecutionReport | None:
    return state_output(state, "executor", ExecutionReport)


def briefing_of(state: Any) -> Briefing | None:
    return state_output(state, "briefer", Briefing)


def snapshot_for(household: Household, subject_id: str) -> dict[str, Any]:
    """What the planner may see about the subject: their grants (as grantor and grantee), accounts, plans, tuition.
    PIN material and other members' private data never leave the ledger."""
    subject = household.member(subject_id)
    return {
        "household_id": household.id,
        "currency": household.currency,
        "self_confirm_limit": household.self_confirm_limit,
        "subject": None if subject is None else {
            "id": subject.id, "name": subject.name, "role": subject.role, "guardians": list(subject.guardians),
            "email": subject.email, "language": subject.language,
        },
        "grants": [
            g.model_dump(mode="json", exclude={"consent_id"})
            for g in household.grants
            if (g.subject_id == subject_id or g.grantee_id == subject_id) and g.status == "active"
        ],
        "accounts": [a.model_dump(mode="json") for a in household.accounts if a.owner_member_id == subject_id or a.kind == "household"],
        "plans": [p for p in household.plans if p.get("member") == subject_id],
        "tuition": [t for t in household.tuition if t.get("member") == subject_id],
    }


def roster_summary(household: Household) -> list[dict[str, Any]]:
    """Members without secrets, for the matcher's input."""
    return [
        {"id": m.id, "name": m.name, "role": m.role, "guardians": list(m.guardians), "language": m.language,
         "email": m.email}
        for m in household.members
    ]


def dumps(value: Any) -> str:
    if isinstance(value, BaseModel):
        return value.model_dump_json()
    return json.dumps(value, ensure_ascii=False, default=str)
