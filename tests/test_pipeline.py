"""End-to-end through the real Strands Graph with the fake provider: the three routes, the guard, and approval."""

import json
from datetime import UTC, datetime

import pytest

from household import config
from household.config import load_settings
from household.fixtures import FixtureStore
from household.model import Receipt
from household.pipeline import execute_approved, run_session
from household.providers.fake import FakeModel, _has_tool_result, _last_user_text, json_section

FAKE = load_settings(provider="fake")
LIVE = load_settings(provider="fake", execution_mode="live", ses_verified_identities=())
NOW = datetime(2026, 9, 11, 16, 0, tzinfo=UTC)
PINS = {"ama": "2468", "daniel": "1357", "kofi": "1111", "mei": "2222"}


def demo():
    return FixtureStore().household("demo")


def run(request_id: str, model=None, household=None, settings=FAKE):
    return run_session(request_id, settings=settings, model=model, household=household or demo(), now=NOW)


# The three routes


def test_route_a_minor_in_scope_allow_is_executed() -> None:
    r = run("kofi-allowance-8")
    assert r.graph_status == "completed"
    assert r.execution_order == ["intake", "matcher", "planner", "authority", "executor", "briefer"]
    assert r.outcome == "executed" and r.skill_id == "allowance"
    [decision] = r.plans[-1].decisions
    assert (decision.outcome, decision.rule_id, decision.grant_id) == ("allow", "minor-allowance", "rule:minor-allowance")
    [receipt] = r.receipts
    assert receipt.action_id == decision.action_id and receipt.mode == "SIMULATED" and receipt.executed_under_grant == "rule:minor-allowance"
    assert r.guard.overrides == 0 and not r.guard.override and r.approvals_needed == []
    assert r.briefing and "$8.00" in r.briefing.headline_en and r.briefing.labels == ["SIMULATED"]
    assert r.plans[-1].proposals[0].actor_member_id == "kofi" and r.plans[-1].proposals[0].idempotency_key


def test_route_b_minor_over_limit_needs_guardian_approval_and_nothing_runs() -> None:
    r = run("kofi-allowance-40")
    assert r.execution_order == ["intake", "matcher", "planner", "authority", "briefer"]
    assert r.outcome == "needs-approval" and r.receipts == []
    [decision] = r.approvals_needed
    assert decision.outcome == "needs-approval" and decision.rule_id == "minor-guardian" and set(decision.approver_ids) == {"ama", "daniel"}
    assert r.plans[-1].verdict == "stop" and r.guard.overrides == 0
    assert r.briefing and r.briefing.done == [] and r.briefing.waiting_on


def test_route_c_over_limit_revises_once_then_splits_into_allow_and_approval() -> None:
    household = demo()
    r = run("daniel-payment-450", household=household)
    assert r.execution_order == ["intake", "matcher", "planner", "authority", "planner", "authority", "executor", "briefer"]
    assert [p.verdict for p in r.plans] == ["revise", "proceed"] and [p.model_verdict for p in r.plans] == ["revise", "proceed"]
    assert [d.outcome for d in r.plans[0].decisions] == ["needs-approval"]
    assert [(p.amount, d.outcome, d.rule_id) for p, d in zip(r.plans[1].proposals, r.plans[1].decisions, strict=True)] == [
        ("300.00", "allow", "grant"), ("150.00", "needs-approval", "grant-refused")]
    assert "300.00 used in the last 30 days" in " ".join(r.plans[1].decisions[1].reasons)
    assert [x.executed_under_grant for x in r.receipts] == ["g-daniel-ama-payments"]
    assert r.approvals_needed[0].approver_ids == ["daniel"] and r.outcome == "partial"
    assert r.guard.overrides == 0 and r.guard.verdict_overrides == 0
    # Only the settled plan is in the ledger; the rejected 450.00 proposal never becomes an action record.
    assert [a.proposal.amount for a in household.actions] == ["300.00", "150.00"]


# The other hero fixtures


def test_spouse_grant_covers_both_benefits_actions_and_the_form_is_prepare_only() -> None:
    r = run("ama-dental-cob")
    assert r.skill_id == "benefits" and r.outcome == "executed"
    decisions = r.plans[-1].decisions
    assert [(d.outcome, d.rule_id, d.grant_id) for d in decisions] == [("allow", "grant", "g-daniel-ama-benefits")] * 2
    assert any("PREPARE-ONLY" in reason for reason in decisions[0].reasons)
    assert [x.rail for x in r.receipts] == ["official-form", "ses-email"]
    assert r.plans[-1].proposals[0].amount == "36.00" and r.plans[-1].proposals[0].claimed_grant_id == "g-daniel-ama-benefits"


def test_minor_email_waits_for_a_guardian_and_guardian_pays_for_their_child() -> None:
    mei = run("mei-email-teacher")
    assert mei.execution_order == ["intake", "matcher", "planner", "authority", "briefer"]
    assert mei.approvals_needed and set(mei.approvals_needed[0].approver_ids) == {"ama", "daniel"} and mei.receipts == []
    tuition = run("ama-for-kofi-tuition")
    assert tuition.outcome == "executed" and tuition.plans[-1].decisions[0].rule_id == "parent-for-minor"
    assert tuition.receipts[0].executed_under_grant == "rule:parent-for-minor"


# The guard


class LyingAuthorityModel(FakeModel):
    """Echoes an allow for everything and claims the plan may proceed."""

    def _payload(self, role, request_id, language, last_text, messages=None):
        payload = super()._payload(role, request_id, language, last_text, messages)
        if role == "authority":
            for d in payload["decisions"]:
                d.update(outcome="allow", rule_id="grant", grant_id="g-daniel-ama-payments", approver_ids=[], reasons=["looks fine"])
            payload["verdict"] = "proceed"
        return payload


def test_guard_overrides_a_model_that_allows_what_code_refuses() -> None:
    r = run("kofi-allowance-40", model=LyingAuthorityModel())
    assert r.guard.override and r.guard.overrides == 1 and r.guard.verdict_overrides == 1
    assert r.plans[-1].decisions[0].outcome == "needs-approval" and r.plans[-1].verdict == "stop"
    assert "executor" not in r.execution_order and r.receipts == [] and r.outcome == "needs-approval"
    assert any(note.startswith("override:") for note in r.guard.notes)


class FabricatingExecutorModel(FakeModel):
    """Invents a receipt for the action that was not allowed and calls execute_action for it too."""

    def _payload(self, role, request_id, language, last_text, messages=None):
        payload = super()._payload(role, request_id, language, last_text, messages)
        if role == "executor":
            for action_id in list(payload["skipped"]):
                payload["receipts"].append(Receipt(id="rcpt-forged", action_id=action_id, rail="internal-ledger", mode="COMPLETE",
                                                   request_digest="x", response_digest="", at=NOW.isoformat(),
                                                   label_reason="forged").model_dump(mode="json"))
            payload["skipped"] = []
        return payload

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        if self._role(system_prompt) == "executor" and not _has_tool_result(messages):
            # Also call execute_action on every id that was not allowed: the tool must refuse in code.
            text = _last_user_text(messages)
            allowed = json_section(text, "Allowed actions:") or []
            denied = [d["action_id"] for d in (json_section(text, "Not allowed:") or [])]
            text = text.replace("Allowed actions:\n" + json.dumps(allowed), "Allowed actions:\n" + json.dumps(allowed + denied))
            messages = [*messages[:-1], {"role": "user", "content": [{"text": text}]}]
        async for event in super().stream(messages, tool_specs=tool_specs, system_prompt=system_prompt, **kwargs):
            yield event


def test_guard_drops_receipts_the_tool_never_issued_and_the_tool_refuses_non_allowed_ids() -> None:
    r = run("daniel-payment-450", model=FabricatingExecutorModel())
    assert r.guard.dropped_receipts >= 1 and any("guard_dropped_receipt" in n for n in r.guard.notes)
    assert [x.action_id for x in r.receipts] == [r.plans[-1].proposals[0].id]  # only the 300.00 that was allowed
    assert r.model_report is not None and any(x.id == "rcpt-forged" for x in r.model_report.receipts)
    assert all(x.id != "rcpt-forged" for x in r.receipts)


class AlwaysReviseModel(FakeModel):
    """Re-serves the same fixable plan on every revision and always asks for another revision."""

    def _payload(self, role, request_id, language, last_text, messages=None):
        if role == "planner":
            return self.store.canned(request_id, "planner", 1)
        payload = super()._payload(role, request_id, language, last_text, messages)
        if role == "authority":
            payload["verdict"] = "revise"
        return payload


def test_revise_loop_is_bounded_and_exhaustion_never_executes_a_waiting_action() -> None:
    r = run("kofi-allowance-40", model=AlwaysReviseModel(), settings=load_settings(provider="fake", max_revisions=2))
    assert r.execution_order == ["intake", "matcher", "planner", "authority", "planner", "authority", "planner", "authority", "briefer"]
    assert [p.verdict for p in r.plans] == ["revise", "revise", "stop"] and [p.model_verdict for p in r.plans] == ["revise"] * 3
    assert r.guard.verdict_overrides == 1 and "no revisions left" in " ".join(r.guard.notes)
    assert r.receipts == [] and r.outcome == "needs-approval" and len(r.approvals_needed) == 1
    assert r.graph_status == "completed"


class EmptyRevisionModel(FakeModel):
    """Asks for a revision, then proposes nothing: the graph settles on an empty plan instead of looping or failing."""

    def _payload(self, role, request_id, language, last_text, messages=None):
        if role == "planner" and self._run(role, last_text) > 1:
            return {"actions": [], "needs": ["the planner gave up"], "notes": []}
        payload = super()._payload(role, request_id, language, last_text, messages)
        if role == "authority":
            payload["verdict"] = "revise"
        return payload


def test_an_empty_revised_plan_settles_as_stop_and_still_briefs_the_member() -> None:
    r = run("kofi-allowance-40", model=EmptyRevisionModel())
    assert r.execution_order == ["intake", "matcher", "planner", "authority", "planner", "authority", "briefer"]
    assert [p.verdict for p in r.plans] == ["revise", "stop"] and r.plans[-1].proposals == [] and r.plans[-1].needs == ["the planner gave up"]
    assert "nothing to revise" in " ".join(r.guard.notes)
    assert r.receipts == [] and r.approvals_needed == [] and r.outcome == "no-action" and r.briefing is not None


class BadPlanModel(FakeModel):
    def _payload(self, role, request_id, language, last_text, messages=None):
        payload = super()._payload(role, request_id, language, last_text, messages)
        if role == "planner":
            payload["actions"].append({**payload["actions"][0], "action_type": "payment:transfer", "rail": "ses-email"})
            payload["actions"].append({**payload["actions"][0], "rail": "stripe-test"})
            payload["actions"].append({**payload["actions"][0], "amount_text": "eight dollars"})
        return payload


def test_malformed_proposals_are_dropped_before_authority_and_the_actor_is_never_the_models() -> None:
    r = run("kofi-allowance-8", model=BadPlanModel())
    assert r.guard.dropped_proposals == 3 and len(r.plans[-1].proposals) == 1
    assert r.plans[-1].proposals[0].actor_member_id == "kofi" and r.outcome == "executed"
    assert any("not an action of skill allowance" in n for n in r.guard.notes)
    assert any("runs on internal-ledger, not stripe-test" in n for n in r.guard.notes)


# The human approval path (pure code)


def test_execute_approved_needs_the_right_member_and_pin_then_records_consent_and_a_receipt() -> None:
    household = demo()
    r = run("kofi-allowance-40", household=household)
    action_id = r.approvals_needed[0].action_id
    with pytest.raises(PermissionError, match="PIN"):
        execute_approved(household, action_id, "ama", "0000", settings=FAKE, now=NOW)
    with pytest.raises(PermissionError, match="not an approver"):
        execute_approved(household, action_id, "mei", PINS["mei"], settings=FAKE, now=NOW)
    assert household.consents == demo().consents and household.receipts == []
    approved = execute_approved(household, action_id, "ama", PINS["ama"], settings=FAKE, now=NOW)
    assert approved.consent.kind == "action-approve" and approved.consent.target_id == action_id
    assert approved.consent.verify(household.member("ama"))
    assert approved.decision.outcome == "allow" and approved.decision.grant_id == "approval:ama" and approved.decision.rule_id == "minor-guardian"
    assert approved.receipt is not None and approved.receipt.executed_under_grant == "approval:ama"
    assert household.action(action_id).receipt == approved.receipt and household.receipts == [approved.receipt]
    with pytest.raises(ValueError, match="not waiting"):
        execute_approved(household, action_id, "ama", PINS["ama"], settings=FAKE, now=NOW)


def test_execute_approved_lets_the_grantor_release_the_remainder_after_the_split() -> None:
    household = demo()
    r = run("daniel-payment-450", household=household)
    remainder = r.approvals_needed[0]
    approved = execute_approved(household, remainder.action_id, "daniel", PINS["daniel"], settings=FAKE, now=NOW)
    assert approved.decision.outcome == "allow" and approved.decision.grant_id == "approval:daniel"
    assert "approved by Daniel Lim" in " ".join(approved.decision.reasons)
    assert len(household.receipts) == 2 and {x.action_id for x in household.receipts} == {p.id for p in r.plans[-1].proposals}


def test_live_mode_posts_the_split_payment_and_the_approved_remainder_to_the_same_payee(monkeypatch) -> None:
    monkeypatch.delenv("SES_FROM", raising=False)
    monkeypatch.setattr(config, "ses_verified_identities", lambda region: pytest.fail("live mode must stay offline without SES_FROM"))
    household = demo()
    r = run("daniel-payment-450", household=household, settings=LIVE)
    [receipt] = r.receipts
    assert receipt.mode == "COMPLETE" and receipt.label_reason == "internal household ledger (no bank rail)" and r.outcome == "partial"
    payee = household.account("ext-toronto-youth-wind-orchestra")
    assert payee is not None and payee.kind == "external" and payee.balance == "300.00" and household.account("hh-main").balance == "2100.00"
    approved = execute_approved(household, r.approvals_needed[0].action_id, "daniel", PINS["daniel"], settings=LIVE, now=NOW)
    assert approved.receipt is not None and approved.receipt.mode == "COMPLETE" and approved.receipt.executed_under_grant == "approval:daniel"
    assert payee.balance == "450.00" and household.account("hh-main").balance == "1950.00"
    assert [(e.debit_account, e.credit_account, e.amount) for e in household.ledger] == [(payee.id, "hh-main", "300.00"), (payee.id, "hh-main", "150.00")]
    assert [x.provider_ref for x in household.receipts] == [e.id for e in household.ledger]


def test_unknown_actor_is_refused_before_any_model_call() -> None:
    model = FakeModel()
    with pytest.raises(KeyError, match="unknown actor"):
        run_session("take $5 please", "zed", settings=FAKE, model=model, household=demo(), now=NOW)
    assert model.calls == []


class CapturingInputModel(FakeModel):
    def __init__(self):
        super().__init__()
        self.inputs = {}

    def _payload(self, role, request_id, language, last_text, messages=None):
        self.inputs.setdefault(role, []).append(last_text)
        return super()._payload(role, request_id, language, last_text, messages)


def test_downstream_inputs_are_typed_code_rendered_and_carry_no_secrets() -> None:
    model = CapturingInputModel()
    r = run("daniel-payment-450", model=model)
    planner_inputs = model.inputs["planner"]
    assert planner_inputs[0].startswith("Revision: 1") and planner_inputs[1].startswith("Revision: 2")
    assert "Skill block:\nSkill household" in planner_inputs[0] and "Household snapshot:" in planner_inputs[0]
    assert "Previous plan:" in planner_inputs[1] and "per-month limit 300.00 CAD exceeded" in planner_inputs[1]
    assert "Previous plan:" not in planner_inputs[0]
    assert "pin_hash" not in planner_inputs[0] and "pin_salt" not in " ".join(model.inputs["matcher"])
    briefer_input = model.inputs["briefer"][0]
    assert "Receipts:" in briefer_input and "Waiting on approval:" in briefer_input and r.receipts[0].id in briefer_input
    assert "Rail labels:" in briefer_input and "internal household ledger" in briefer_input
