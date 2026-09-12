"""The education-money skill: canada.ca quotes cross-checked against the recorded page, the CESG arithmetic as pure
code (carry-forward, yearly and lifetime caps, age limit, this year's ledger), tuition due dates on the session clock,
and the four routes under EXECUTION_MODE=live with the fake provider (no AWS, no network)."""

import dataclasses
import json
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

import pytest

from household import config
from household.config import ROOT, load_settings
from household.executor.receipts import LEDGER_LABEL
from household.fixtures import FixtureStore
from household.model import ActionProposal, ActionRecord, Household, Receipt, signed_money
from household.pipeline import execute_approved, run_session
from household.skills import SKILLS, match_skill, skill_for
from household.skills.base import ToolContext
from household.skills.education import EDUCATION
from household.skills.education.rules import (
    CESG_URL,
    FETCHED,
    RULES,
    cesg_room,
    due_status,
    parse_due,
    resp_account_id,
    resp_payee,
    tuition_schedule,
    year_contributed,
)
from household.skills.education.templates import RESP_CONTRIBUTE, TUITION_PAY

NOW = datetime(2026, 9, 11, 16, 0, tzinfo=UTC)
QUOTES = ROOT / "fixtures" / "skills" / "education" / "cesg-estimating-amounts.json"
SEED = {a.id: signed_money(a.balance) for a in FixtureStore().household("demo").accounts}
PINS = {"ama": "2468"}


def demo() -> Household:
    return FixtureStore().household("demo")


def deltas(household: Household) -> dict[str, Decimal]:
    changed = {a.id: signed_money(a.balance) - SEED.get(a.id, Decimal("0")) for a in household.accounts}
    return {account_id: delta for account_id, delta in changed.items() if delta != 0}


@pytest.fixture
def live(monkeypatch):
    monkeypatch.delenv("SES_FROM", raising=False)
    monkeypatch.setattr(config, "ses_verified_identities", lambda region: pytest.fail("live mode must stay offline without SES_FROM"))
    return load_settings(provider="fake", execution_mode="live")


# Contract and rules


def test_contract_is_frozen_third_in_the_order_and_both_templates_post_to_the_ledger():
    assert SKILLS[2] is EDUCATION and skill_for("education") is EDUCATION
    with pytest.raises(dataclasses.FrozenInstanceError):
        EDUCATION.id = "x"
    assert EDUCATION.action_types == ("payment:transfer",)
    assert list(EDUCATION.action_templates) == ["resp:contribute", "tuition:pay"]
    assert EDUCATION.template("resp:contribute") is RESP_CONTRIBUTE and EDUCATION.template("tuition:pay") is TUITION_PAY
    for template in EDUCATION.action_templates.values():
        assert (template.action_type, template.rail) == ("payment:transfer", "internal-ledger")
    assert RESP_CONTRIBUTE.payload_fields == ("kind", "beneficiary_member_id", "year_contributed_so_far", "grant_room_note")
    assert TUITION_PAY.payload_fields == ("kind", "payee", "due", "instalment") and "already past" in TUITION_PAY.description
    assert [t.name for t in EDUCATION.tools] == ["cesg_room", "tuition_schedule", "propose_resp_contribution", "propose_tuition_payment"]
    assert match_skill("put $50 into my RESP").id == "education" and match_skill("pay the tuition instalment").id == "education"
    assert match_skill("please respond to the teacher").id == "household"  # 'resp' inside 'respond' is not a hint
    assert match_skill("allowance for Kofi's RESP").id == "allowance"  # tie-break: the earlier skill wins
    assert "cesg_room" in EDUCATION.prompt() and "past_due" in EDUCATION.prompt() and EDUCATION.form_builders == {}
    assert set(EDUCATION.evidence_schema.model_fields) == {"beneficiary_member_id", "amount_text", "payee", "due_text", "purpose"}


def test_rules_quote_canada_ca_verbatim_from_the_recorded_page():
    recorded = json.loads(QUOTES.read_text(encoding="utf-8"))
    assert recorded["recorded"] is True and recorded["fetched"] == FETCHED == "2026-09-12" and recorded["source"] == CESG_URL
    assert [(c.label, c.quote) for c in RULES.citations] == [(q["label"], q["text"]) for q in recorded["quotes"]]
    assert all(c.url == CESG_URL for c in RULES.citations)
    assert [c.label for c in RULES.citations] == [
        "CESG, basic rate", "CESG, yearly maximum", "CESG, carry-forward", "CESG, carry-forward yearly cap",
        "CESG, lifetime maximum", "CESG, age limit", "CLB, amounts", "CLB, no contribution needed"]
    assert "20% of the first $2,500" in RULES.citations[0].quote and "$1,000" in RULES.citations[3].quote and "$7,200" in RULES.citations[4].quote
    assert "not computed" in RULES.params["CLB"] and "never proposed" in RULES.params["tuition due dates"]


# CESG arithmetic: contribution -> grant, room left


CASES = [
    # (contribution, inputs, grant, year room left, lifetime room left, carry-forward used)
    ("200.00", {}, "40.00", "460.00", "7160.00", "0.00"),
    ("2500.00", {}, "500.00", "0.00", "6700.00", "0.00"),
    ("3000.00", {}, "500.00", "0.00", "6700.00", "0.00"),  # 600.00 of basic, capped by the yearly 500.00 with no carry-forward
    ("3000.00", {"carry_forward_room": "500.00"}, "600.00", "400.00", "6600.00", "100.00"),
    ("6000.00", {"carry_forward_room": "2000.00"}, "1000.00", "0.00", "6200.00", "500.00"),  # 1,200.00 capped at 1,000.00 a year
    ("2500.00", {"lifetime_grant_received": "7000.00"}, "200.00", "300.00", "0.00", "0.00"),  # lifetime cap binds
    ("200.00", {"year_contributed_so_far": "2400.00"}, "20.00", "0.00", "7180.00", "0.00"),  # 480.00 already earned this year
    ("0.00", {}, "0.00", "500.00", "7200.00", "0.00"),
    ("33.33", {}, "6.67", "493.33", "7193.33", "0.00"),
]


@pytest.mark.parametrize(("contribution", "inputs", "grant", "year_after", "lifetime_after", "carry_used"), CASES)
def test_cesg_arithmetic_is_pure_code_from_the_cited_parameters(contribution, inputs, grant, year_after, lifetime_after, carry_used):
    household = demo()
    room = cesg_room(household, "kofi", contribution, NOW, **inputs)
    assert room is not None and room.eligible_by_age and room.age_this_year == 14 and room.beneficiary_name == "Kofi Okafor-Lim"
    assert (room.grant, room.year_room_after, room.lifetime_room_after, room.carry_forward_used) == (grant, year_after, lifetime_after, carry_used)
    assert room.basic_20pct == f"{(Decimal(contribution) * Decimal('0.20')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):.2f}"
    assert room.citations == [c.label for c in RULES.citations if c.label.startswith("CESG")]
    assert room.note().startswith(
        f"CESG on {room.contribution}: {grant} (20% basic CESG; {year_after} of this year's grant room left after this; "
        f"lifetime room {lifetime_after} of 7200.00). Source: canada.ca, Estimating amounts (fetched 2026-09-12)")
    assert household == demo()


def test_cesg_stops_after_the_year_the_child_turns_17_and_rejects_bad_input():
    household = demo()
    household.member("kofi").birth_year = 2008  # turns 18 this year
    room = cesg_room(household, "kofi", "200.00", NOW)
    assert room.age_this_year == 18 and not room.eligible_by_age and room.grant == "0.00" and room.year_room_after == "500.00"
    assert any("past the last eligible year (17)" in a for a in room.assumptions)
    household.member("kofi").birth_year = 2009  # turns 17 this year: eligible until the end of the year
    assert cesg_room(household, "kofi", "200.00", NOW).grant == "40.00"
    assert cesg_room(demo(), "zed", "200.00", NOW) is None
    with pytest.raises(ValueError):
        cesg_room(demo(), "kofi", "two hundred", NOW)
    with pytest.raises(ValueError):
        cesg_room(demo(), "kofi", "-5", NOW)


def test_this_years_resp_receipts_count_against_the_yearly_room():
    household = demo()
    kofi = household.member("kofi")
    assert resp_payee(kofi) == "RESP for Kofi" and resp_account_id(kofi) == "ext-resp-for-kofi"
    assert year_contributed(household, kofi, NOW) == Decimal("0")

    def post(action_id, amount, recipient, at):
        proposal = ActionProposal(id=action_id, skill_id="education", action_type="payment:transfer", rail="internal-ledger",
                                  actor_member_id="daniel", subject_member_id="kofi", recipient=recipient, amount=amount, idempotency_key=action_id)
        household.actions.append(ActionRecord(proposal=proposal, created_at=at))
        household.receipts.append(Receipt(id=f"r-{action_id}", action_id=action_id, rail="internal-ledger", mode="COMPLETE", request_digest="d",
                                          response_digest="", at=at, executed_under_grant="rule:parent-for-minor", label_reason="history"))

    post("resp-1", "2400.00", "RESP for Kofi", (NOW - timedelta(days=30)).isoformat())
    post("band-1", "180.00", "Toronto District School Board", (NOW - timedelta(days=10)).isoformat())  # not the RESP
    post("resp-old", "1000.00", "ext-resp-for-kofi", datetime(2025, 12, 20, tzinfo=UTC).isoformat())  # last year
    assert year_contributed(household, kofi, NOW) == Decimal("2400.00")
    room = cesg_room(household, "kofi", "200.00", NOW)
    assert (room.year_contributed_so_far, room.year_room_before, room.grant, room.year_room_after) == ("2400.00", "20.00", "20.00", "0.00")
    assert "read from the household ledger" in room.note()


# Tuition due dates


def test_due_dates_are_read_as_written_and_checked_on_the_session_clock():
    assert parse_due("September 30, 2026") == parse_due("2026-09-30") == parse_due("30 September 2026") == parse_due("Sep 30, 2026") == date(2026, 9, 30)
    assert parse_due("end of the month") is None
    status = due_status("September 30, 2026", NOW)
    assert (status["due"], status["today"], status["past_due"], status["days_until"]) == ("2026-09-30", "2026-09-11", False, 19)
    assert (due_status("August 15, 2026", NOW)["past_due"], due_status("August 15, 2026", NOW)["days_until"]) == (True, -27)
    assert due_status("September 11, 2026", NOW)["past_due"] is False  # due today is not past
    assert due_status("September 11, 2026", datetime(2026, 9, 12, 3, 0, tzinfo=UTC))["past_due"] is False  # 03:00 UTC is still the 11th in Toronto
    unknown = due_status("whenever", NOW)
    assert unknown["past_due"] is None and unknown["due"] is None and "not understood" in unknown["note"]
    household = demo()
    on_file = tuition_schedule(household, "Toronto District School Board", NOW)
    assert on_file["note"] == "" and [s["member"] for s in on_file["schedule"]] == ["kofi"]
    assert (on_file["schedule"][0]["due"], on_file["schedule"][0]["past_due"], on_file["schedule"][0]["amount"]) == ("2026-09-30", False, "180.00")
    assert tuition_schedule(household, "Bright Path Montessori", NOW) == {"payee": "Bright Path Montessori", "schedule": [], "note": "no schedule on file"}
    assert tuition_schedule(household, "", NOW)["note"] == "no schedule on file"


# Tools


def test_tools_compute_propose_and_refuse_but_never_move_money():
    household = demo()
    context = ToolContext(household=household, actor=household.member("daniel"), now=NOW)
    tools = EDUCATION.build_tools(context)
    room = json.loads(tools["cesg_room"](beneficiary_member_id="kofi", contribution="200.00"))
    assert (room["grant"], room["year_room_after"], room["lifetime_room_after"]) == ("40.00", "460.00", "7160.00")
    assert room["grant_room_note"].startswith("CESG on 200.00: 40.00") and room["citations"][0] == "CESG, basic rate"
    assert json.loads(tools["cesg_room"](beneficiary_member_id="kofi", contribution="3000.00", carry_forward_room="500.00"))["grant"] == "600.00"
    assert "error" in json.loads(tools["cesg_room"](beneficiary_member_id="zed", contribution="1.00"))
    assert "error" in json.loads(tools["cesg_room"](beneficiary_member_id="kofi", contribution="lots"))
    proposal = json.loads(tools["propose_resp_contribution"](beneficiary_member_id="kofi", amount="200.00", memo="for university"))
    assert (proposal["action_type"], proposal["rail"], proposal["subject_member_id"], proposal["recipient"], proposal["amount_text"],
            proposal["currency"], proposal["claimed_grant_id"]) == ("payment:transfer", "internal-ledger", "kofi", "RESP for Kofi", "200.00", "CAD", "")
    payload = {f["key"]: f["value"] for f in proposal["payload"]}
    assert (payload["kind"], payload["beneficiary_member_id"], payload["year_contributed_so_far"], payload["purpose"]) == ("resp:contribute", "kofi", "0.00", "for university")
    assert payload["grant_room_note"] == room["grant_room_note"]
    assert "error" in json.loads(tools["propose_resp_contribution"](beneficiary_member_id="mei", amount="x"))
    assert "error" in json.loads(tools["propose_resp_contribution"](beneficiary_member_id="zed", amount="1.00"))
    schedule = json.loads(tools["tuition_schedule"](payee="Bright Path Montessori", due_text="September 30, 2026"))
    assert schedule["note"] == "no schedule on file" and schedule["requested_due"]["past_due"] is False
    assert "requested_due" not in json.loads(tools["tuition_schedule"](payee="Toronto District School Board"))
    pay = json.loads(tools["propose_tuition_payment"](payee="Bright Path Montessori", amount="450.00", due_text="September 30, 2026",
                                                      instalment="2 of 3", subject_member_id="ama"))
    assert (pay["action_type"], pay["rail"], pay["subject_member_id"], pay["recipient"], pay["amount_text"]) == (
        "payment:transfer", "internal-ledger", "ama", "Bright Path Montessori", "450.00")
    assert {f["key"]: f["value"] for f in pay["payload"]} == {
        "kind": "tuition:pay", "payee": "Bright Path Montessori", "due": "September 30, 2026", "instalment": "2 of 3",
        "purpose": "tuition instalment (2 of 3) for Ama Okafor-Lim, due September 30, 2026"}
    assert json.loads(tools["propose_tuition_payment"](payee="X", amount="1.00", due_text="2026-10-01"))["subject_member_id"] == "daniel"
    refused = json.loads(tools["propose_tuition_payment"](payee="Bright Path Montessori", amount="450.00", due_text="August 15, 2026"))
    assert "already past" in refused["error"] and refused["past_due"] is True and refused["due"] == "2026-08-15"
    assert "not understood" in json.loads(tools["propose_tuition_payment"](payee="X", amount="1.00", due_text="soon"))["error"]
    assert "error" in json.loads(tools["propose_tuition_payment"](payee="X", amount="1.00", due_text="2026-10-01", subject_member_id="zed"))
    assert "error" in json.loads(tools["propose_tuition_payment"](payee="X", amount="nope", due_text="2026-10-01"))
    assert household == demo()
    assert [c["tool"] for c in context.log][:5] == ["cesg_room"] * 4 + ["propose_resp_contribution"]


def test_canned_plans_carry_the_tools_own_grant_arithmetic():
    store = FixtureStore()
    contribute = store.canned("daniel-resp-kofi-200", "planner")["actions"][0]
    assert {f["key"]: f["value"] for f in contribute["payload"]}["grant_room_note"] == cesg_room(demo(), "kofi", "200.00", NOW).note()
    ask = store.canned("kofi-resp-ask", "planner")["actions"][0]
    assert {f["key"]: f["value"] for f in ask["payload"]}["grant_room_note"] == cesg_room(demo(), "kofi", "50.00", NOW).note()
    assert store.canned("ama-tuition-past-due", "planner")["actions"] == [] and store.canned("ama-tuition-past-due", "planner")["needs"]


# Routes, live and offline


def test_a_parent_contributes_to_the_childs_resp_and_the_ledger_posts_it(live):
    household = demo()
    r = run_session("daniel-resp-kofi-200", settings=live, household=household, now=NOW)
    assert r.skill_id == "education" and r.outcome == "executed" and r.approvals_needed == []
    [decision] = r.plans[-1].decisions
    assert (decision.outcome, decision.rule_id, decision.grant_id) == ("allow", "parent-for-minor", "rule:parent-for-minor")
    [receipt] = r.receipts
    assert (receipt.mode, receipt.label_reason, receipt.executed_under_grant) == ("COMPLETE", LEDGER_LABEL, "rule:parent-for-minor")
    assert deltas(household) == {"hh-main": Decimal("-200.00"), "ext-resp-for-kofi": Decimal("200.00")}
    resp = household.account("ext-resp-for-kofi")
    assert resp is not None and (resp.kind, resp.owner_member_id, resp.rules) == ("external", "", {"name": "RESP for Kofi"})
    [entry] = household.ledger
    assert receipt.provider_ref == entry.id and (entry.debit_account, entry.credit_account, entry.amount) == ("ext-resp-for-kofi", "hh-main", "200.00")
    [proposal] = r.plans[-1].proposals
    assert proposal.payload["kind"] == "resp:contribute" and proposal.payload["grant_room_note"] == cesg_room(demo(), "kofi", "200.00", NOW).note()
    assert r.guard.override is False and r.briefing is not None and "$40.00" in r.briefing.headline_en and r.language == "fr"
    later = cesg_room(household, "kofi", "200.00", NOW + timedelta(days=30))  # the posted contribution now counts
    assert (later.year_contributed_so_far, later.year_room_before, later.grant) == ("200.00", "460.00", "40.00")


def test_a_minor_asking_for_resp_money_waits_for_both_guardians(live):
    household = demo()
    r = run_session("kofi-resp-ask", settings=live, household=household, now=NOW)
    assert r.outcome == "needs-approval" and r.receipts == [] and r.execution_order == ["intake", "matcher", "planner", "authority", "briefer"]
    [decision] = r.approvals_needed
    assert (decision.outcome, decision.rule_id, sorted(decision.approver_ids)) == ("needs-approval", "minor-guardian", ["ama", "daniel"])
    assert "minor and may not decide payment:transfer" in " ".join(decision.reasons)
    assert deltas(household) == {} and household.account("ext-resp-for-kofi") is None and household.ledger == []
    assert (r.plans[-1].proposals[0].recipient, r.plans[-1].proposals[0].amount) == ("RESP for Kofi", "50.00")
    assert r.briefing is not None and r.briefing.done == [] and r.briefing.waiting_on


def test_an_adult_confirms_her_own_instalment_above_the_self_limit_then_the_ledger_posts_it(live):
    household = demo()
    r = run_session("ama-tuition-instalment", settings=live, household=household, now=NOW)
    assert r.outcome == "needs-approval" and r.receipts == [] and deltas(household) == {}
    [decision] = r.approvals_needed
    assert (decision.rule_id, decision.approver_ids) == ("self-confirm", ["ama"]) and "exceeds the self-confirm limit 200.00" in " ".join(decision.reasons)
    [proposal] = r.plans[-1].proposals
    assert proposal.payload == {"kind": "tuition:pay", "payee": "Bright Path Montessori", "due": "September 30, 2026", "instalment": "2 of 3",
                                "purpose": "tuition instalment (2 of 3) for Ama Okafor-Lim, due September 30, 2026"}
    approved = execute_approved(household, decision.action_id, "ama", PINS["ama"], settings=live, now=NOW)
    assert approved.decision.outcome == "allow" and approved.decision.grant_id == "approval:ama" and approved.receipt is not None
    assert (approved.receipt.mode, approved.receipt.label_reason) == ("COMPLETE", LEDGER_LABEL)
    assert deltas(household) == {"hh-main": Decimal("-450.00"), "ext-bright-path-montessori": Decimal("450.00")}
    assert household.ledger[0].memo == "tuition instalment (2 of 3) for Ama Okafor-Lim, due September 30, 2026"


def test_a_past_due_instalment_moves_nothing_and_the_member_is_told_why(live):
    household = demo()
    r = run_session("ama-tuition-past-due", settings=live, household=household, now=NOW)
    assert r.outcome == "no-action" and r.receipts == [] and r.approvals_needed == [] and r.skill_id == "education"
    assert r.execution_order == ["intake", "matcher", "planner", "authority", "briefer"]
    assert r.plans[-1].proposals == [] and r.plans[-1].decisions == [] and r.plans[-1].verdict == "stop"
    assert any("August 15, 2026" in need and "already past" in need for need in r.plans[-1].needs)
    assert deltas(household) == {} and household.ledger == [] and household.actions == []
    assert r.briefing is not None and r.briefing.done == [] and "August 15, 2026" in r.briefing.headline_en
