"""The typed ledger: exact fixture round-trip, forbidden extras, PIN and consent proofs, lookups."""

import json
from decimal import Decimal

import pytest
from pydantic import ValidationError

from household.config import ROOT
from household.model import (
    ActionProposal,
    ActionRecord,
    Household,
    Member,
    Receipt,
    consent_proof,
    money,
    parse_iso,
)

DEMO = ROOT / "fixtures" / "households" / "demo.json"
PINS = {"ama": "2468", "daniel": "1357", "kofi": "1111", "mei": "2222"}


def raw_demo() -> dict:
    return json.loads(DEMO.read_text(encoding="utf-8"))


def demo() -> Household:
    return Household.model_validate(raw_demo())


def proposal(**overrides) -> ActionProposal:
    base = dict(id="act-1", skill_id="test", action_type="allowance:transfer", rail="internal-ledger",
                actor_member_id="kofi", subject_member_id="kofi", amount="8.00")
    return ActionProposal(**{**base, **overrides})


def test_demo_fixture_round_trips_exactly() -> None:
    raw = raw_demo()
    household = Household.model_validate(raw)
    assert household.model_dump(mode="json") == raw
    assert Household.model_validate(household.model_dump(mode="json")) == household
    assert (household.id, household.jurisdiction, household.currency) == ("demo", "CA-ON", "CAD")
    assert [m.id for m in household.members] == ["ama", "daniel", "kofi", "mei"]
    assert [m.role for m in household.members] == ["adult", "adult", "minor", "minor"]
    assert all(m.memory_actor_id == f"demo.{m.id}" for m in household.members)
    assert {g.id for g in household.grants} == {
        "g-daniel-ama-benefits", "g-daniel-ama-payments", "g-ama-agent-recall", "g-expired", "g-revoked",
    }
    assert household.grant("g-revoked").status == "revoked"
    assert parse_iso(household.grant("g-expired").expires_at) < parse_iso("2026-09-11T00:00:00-04:00")
    assert {a.id for a in household.accounts} == {"hh-main", "allow-kofi", "allow-mei"}
    assert [p["insurer"] for p in household.plans] == ["Sun Life", "Manulife"]
    assert household.tuition[0]["member"] == "kofi" and household.tuition[0]["amount"] == "180.00"


def test_unknown_keys_are_rejected_at_every_level() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        Household.model_validate({**raw_demo(), "bank_token": "sk_live_nope"})
    nested = raw_demo()
    nested["members"][0]["is_admin"] = True
    with pytest.raises(ValidationError, match="Extra inputs"):
        Household.model_validate(nested)
    with pytest.raises(ValidationError, match="Extra inputs"):
        proposal(approved=True)


@pytest.mark.parametrize(("member_id", "pin"), sorted(PINS.items()))
def test_verify_pin_accepts_the_demo_pin_and_rejects_others(member_id: str, pin: str) -> None:
    member = demo().member(member_id)
    assert member.verify_pin(pin)
    assert not member.verify_pin("9999")
    assert not member.verify_pin("")
    assert len(bytes.fromhex(member.pin_salt)) == 16 and len(member.pin_hash) == 64


def test_set_pin_uses_a_fresh_salt_and_no_pin_means_no_access() -> None:
    member = Member(id="x", name="X", role="adult", birth_year=1990, memory_actor_id="demo.x")
    assert not member.verify_pin("1234")
    member.set_pin("1234")
    first = (member.pin_salt, member.pin_hash)
    assert member.verify_pin("1234") and not member.verify_pin("1235")
    member.set_pin("1234")
    assert (member.pin_salt, member.pin_hash) != first and member.verify_pin("1234")


def test_consent_proofs_bind_member_target_time_and_pin() -> None:
    household = demo()
    assert len(household.consents) == 2
    for consent in household.consents:
        member = household.member(consent.member_id)
        assert consent.kind == "grant-accept" and consent.verify(member)
        assert consent.proof == consent_proof(consent.member_id, consent.target_id, consent.at, member.pin_hash)
        assert not consent.verify(household.member("ama"))  # another member's PIN hash does not prove it
        grant = household.grant(consent.target_id)
        assert grant.consent_id == consent.id and grant.grantor_id == consent.member_id
    for grant in household.grants:
        if grant.basis == "spouse-grant" and grant.status == "active" and grant.consent_id is not None:
            assert any(c.id == grant.consent_id for c in household.consents)


def test_money_fields_reject_negative_and_non_numeric() -> None:
    with pytest.raises(ValidationError, match="non-negative"):
        proposal(amount="-5.00")
    with pytest.raises(ValidationError, match="not a money amount"):
        proposal(amount="ten dollars")
    with pytest.raises(ValidationError):
        Household.model_validate({**raw_demo(), "self_confirm_limit": "NaN"})
    assert money("10") == Decimal("10.00") and proposal(amount=None).amount is None


def test_lookups_and_receipt_history() -> None:
    household = demo()
    assert household.member("nobody") is None and household.grant("g-nope") is None
    assert household.account("nope") is None and household.action("nope") is None
    assert household.allowance_account("kofi").rules == {"auto_limit": "10.00", "weekly": "15.00"}
    assert household.allowance_account("ama") is None
    assert [g.id for g in household.grants_for("daniel")] == ["g-daniel-ama-benefits", "g-daniel-ama-payments", "g-expired"]
    household.actions.append(ActionRecord(proposal=proposal(idempotency_key="k1"), created_at="2026-09-10T10:00:00-04:00"))
    receipt = Receipt(id="r1", action_id="act-1", rail="internal-ledger", mode="SIMULATED", request_digest="d",
                      response_digest="", at="2026-09-10T10:00:01-04:00", executed_under_grant="rule:minor-allowance",
                      label_reason="test")
    household.receipts.append(receipt)
    assert household.receipts_for("kofi", "allowance:transfer", "2026-09-05T00:00:00-04:00") == [receipt]
    assert household.receipts_for("kofi", "allowance:transfer", "2026-09-11T00:00:00-04:00") == []
    assert household.receipts_for("kofi", "payment:transfer", "2026-09-05T00:00:00-04:00") == []
    assert household.receipts_for("mei", "allowance:transfer", "2026-09-05T00:00:00-04:00") == []
    assert household.receipts_under("rule:minor-allowance", "2026-09-05T00:00:00-04:00") == [receipt]
    assert household.receipts_under("rule:self", "2026-09-05T00:00:00-04:00") == []
    assert household.amount_of(receipt) == Decimal("8.00")
    assert parse_iso("2026-09-10T10:00:00").tzinfo is not None  # naive strings are read as UTC
