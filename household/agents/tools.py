"""Deterministic tools the agents call. Authority and execution live here as closures over one session's ledger, so a
model can only ask; it can never decide or act on its own."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from strands import tool

from .. import authority, executor
from ..config import Settings
from ..model import ActionProposal, AuthorityDecision, Household, Member, Receipt
from ..skills import ALL_SKILLS, ToolContext

RULE_TEXT = {
    "rule:self": "an adult deciding for themself within the household self-confirm limit",
    "rule:minor-allowance": "a child taking from their own allowance within the rule their parents set",
    "rule:parent-for-minor": "a guardian deciding for their own child",
}


@dataclass
class SessionRecord:
    """What code observed during one session. The guard checks the model's claims against this, never the reverse."""

    proposals: dict[str, ActionProposal] = field(default_factory=dict)
    plans: list[list[str]] = field(default_factory=list)  # proposal ids per plan revision, in plan order
    decisions: dict[str, list[AuthorityDecision]] = field(default_factory=dict)
    receipts: dict[str, Receipt] = field(default_factory=dict)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    lookups: list[str] = field(default_factory=list)
    refused_executions: list[dict[str, Any]] = field(default_factory=list)

    @property
    def revision(self) -> int:
        return len(self.plans)

    def current_plan(self) -> list[ActionProposal]:
        return [self.proposals[i] for i in self.plans[-1]] if self.plans else []

    def latest_decision(self, action_id: str) -> AuthorityDecision | None:
        found = self.decisions.get(action_id)
        return found[-1] if found else None

    def add_decision(self, decision: AuthorityDecision) -> None:
        self.decisions.setdefault(decision.action_id, []).append(decision)


def make_tools(
    household: Household,
    actor: Member,
    settings: Settings,
    record: SessionRecord,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    ctx = ToolContext(household=household, actor=actor, now=now, log=record.tool_calls)

    @tool
    def household_lookup(query: str) -> str:
        """Look up members, grants, accounts, plans and tuition entries in this household by id, name or word.
        Returns matching records without any secrets. An empty query returns the roster.

        Args:
            query: an id ("kofi"), a name ("Daniel"), or a word ("dental", "allowance").
        """
        record.lookups.append(query)
        needle = query.strip().lower()

        def hit(*parts: Any) -> bool:
            return not needle or any(needle in str(p).lower() for p in parts)

        result = {
            "members": [
                {"id": m.id, "name": m.name, "role": m.role, "guardians": list(m.guardians), "email": m.email, "language": m.language}
                for m in household.members if hit(m.id, m.name, m.role)
            ],
            "grants": [
                g.model_dump(mode="json", exclude={"consent_id"})
                for g in household.grants if hit(g.id, g.grantor_id, g.grantee_id, g.subject_id, " ".join(g.scope), g.basis)
            ],
            "accounts": [a.model_dump(mode="json") for a in household.accounts if hit(a.id, a.owner_member_id, a.kind)],
            "plans": [p for p in household.plans if hit(*p.values())],
            "tuition": [t for t in household.tuition if hit(*t.values())],
        }
        return json.dumps(result, ensure_ascii=False)

    @tool
    def check_authority(action_json: str) -> str:
        """Ask the code whether one planned action may run. Pass the action's id as JSON, for example {"id": "act-1"}.
        Returns the decision (allow, block or needs-approval) with its rule, grant, approvers and reasons. Echo it
        exactly; it is decided on the plan as recorded, in plan order, so earlier allowed amounts count against limits.

        Args:
            action_json: JSON carrying the action id under "id" (or "action_id"), or the bare id.
        """
        action_id = _action_id(action_json)
        plan = record.current_plan()
        index = next((i for i, p in enumerate(plan) if p.id == action_id), None)
        if index is None:
            decision = AuthorityDecision(action_id=action_id or "?", outcome="block", rule_id="unknown-action",
                                         reasons=[f"unknown action id {action_id!r}: it is not in the current plan"])
            return decision.model_dump_json()
        decision = authority.decide_plan(plan[: index + 1], household, now)[-1]
        record.add_decision(decision)
        return decision.model_dump_json()

    @tool
    def execute_action(action_id: str) -> str:
        """Execute one allowed action through its rail and return the receipt. Refuses, with an error, unless the
        latest decision for that id is allow and no receipt exists yet.

        Args:
            action_id: the id of a planned action the authority allowed.
        """
        proposal = record.proposals.get(action_id)
        decision = record.latest_decision(action_id)
        error: str | None = None
        if proposal is None:
            error = f"unknown action id {action_id!r}"
        elif decision is None or decision.outcome != "allow":
            error = f"not allowed: latest decision for {action_id} is {decision.outcome if decision else 'missing'}"
        elif action_id in record.receipts or (household.action(action_id) or _NO_RECORD).receipt is not None:
            error = f"already executed: {action_id} has a receipt"
        elif household.action(action_id) is None:
            error = f"no action record for {action_id}: the plan was not accepted"
        if error is not None:
            record.refused_executions.append({"action_id": action_id, "error": error})
            return json.dumps({"error": error, "action_id": action_id})
        assert proposal is not None and decision is not None
        receipt = executor.execute(proposal, household, execution_mode=settings.execution_mode, now=now, grant_id=decision.grant_id)
        executor.record(household, receipt)
        recorded = household.action(action_id)
        if recorded is not None:
            recorded.proposal.idempotency_key = proposal.idempotency_key
        record.receipts[action_id] = receipt
        return receipt.model_dump_json()

    @tool
    def grant_text(grant_id: str) -> str:
        """Plain words for a grant id or rule id the member can read, e.g. "g-daniel-ama-benefits" or "rule:self".

        Args:
            grant_id: the grant_id from a decision or receipt.
        """
        if grant_id in RULE_TEXT:
            return json.dumps({"id": grant_id, "text": RULE_TEXT[grant_id]})
        if grant_id.startswith("approval:"):
            member = household.member(grant_id.split(":", 1)[1])
            return json.dumps({"id": grant_id, "text": f"approved by {member.name if member else grant_id[9:]}"})
        grant = household.grant(grant_id)
        if grant is None:
            return json.dumps({"id": grant_id, "error": "unknown grant id"})
        grantor = household.member(grant.grantor_id)
        grantee = household.member(grant.grantee_id)
        limit = f" up to {grant.limit_amount} {grant.limit_currency} {grant.limit_period}" if grant.limit_amount else ""
        text = (
            f"{grantor.name if grantor else grant.grantor_id} lets {grantee.name if grantee else grant.grantee_id} "
            f"handle {', '.join(grant.scope)} for them{limit}, until {grant.expires_at[:10]}"
        )
        return json.dumps({"id": grant_id, "text": text, "status": grant.status, "basis": grant.basis})

    tools: dict[str, Any] = {
        "household_lookup": household_lookup,
        "check_authority": check_authority,
        "execute_action": execute_action,
        "grant_text": grant_text,
    }
    for skill in ALL_SKILLS:
        tools.update(skill.build_tools(ctx))
    return tools


class _NoRecord:
    receipt = None


_NO_RECORD = _NoRecord()


def _action_id(action_json: str) -> str:
    text = (action_json or "").strip()
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return text
        return str(data.get("id") or data.get("action_id") or "")
    return text.strip('"')
