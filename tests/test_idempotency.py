"""Idempotency keys and the executor entry point: one request, one receipt (rails are covered in test_rails.py)."""

import json
from datetime import UTC, datetime, timedelta
from typing import get_args

import pytest

from household.config import ROOT
from household.executor import (
    RAILS,
    SIMULATED_LABEL,
    execute,
    idempotency,
    rails,
    receipts,
    record,
    request_digest,
)
from household.model import ActionProposal, ActionRecord, Household, Rail, Receipt

DEMO = ROOT / "fixtures" / "households" / "demo.json"
NOW = datetime(2026, 9, 11, 16, 0, tzinfo=UTC)


def demo() -> Household:
    return Household.model_validate(json.loads(DEMO.read_text(encoding="utf-8")))


def proposal(**overrides) -> ActionProposal:
    base = dict(id="act-1", skill_id="test", action_type="payment:transfer", rail="internal-ledger",
                actor_member_id="ama", subject_member_id="daniel", recipient="allow-kofi", amount="100.00",
                evidence_refs=["doc:b", "doc:a"])
    return ActionProposal(**{**base, **overrides})


def receipt_for(action_id: str, **overrides) -> Receipt:
    base = dict(id=f"r-{action_id}", action_id=action_id, rail="internal-ledger", mode="SIMULATED",
                request_digest="d", response_digest="", at=NOW.isoformat(), label_reason="test")
    return Receipt(**{**base, **overrides})


def test_same_request_same_day_has_one_key() -> None:
    key = idempotency.key(proposal(), "demo", "2026-09-11")
    assert len(key) == 64
    same = proposal(id="act-2", rationale="worded differently", evidence_refs=["doc:a", "doc:b"], payload={"x": "y"})
    assert idempotency.key(same, "demo", "2026-09-11") == key
    assert idempotency.key(proposal(), "demo", "2026-09-12") != key
    assert idempotency.key(proposal(amount="100.01"), "demo", "2026-09-11") != key
    assert idempotency.key(proposal(recipient=None), "demo", "2026-09-11") != key
    assert idempotency.key(proposal(subject_member_id="ama"), "demo", "2026-09-11") != key
    assert idempotency.key(proposal(evidence_refs=["doc:a"]), "demo", "2026-09-11") != key
    assert idempotency.key(proposal(), "other-household", "2026-09-11") != key


def test_is_duplicate_finds_the_receipt_only_once_recorded() -> None:
    household = demo()
    assert idempotency.is_duplicate("k1", household) is None
    household.actions.append(ActionRecord(proposal=proposal(idempotency_key="k1"), created_at=NOW.isoformat()))
    assert idempotency.is_duplicate("k1", household) is None  # proposed but never executed
    receipt = receipt_for("act-1")
    record(household, receipt)
    assert idempotency.is_duplicate("k1", household) is receipt
    assert idempotency.is_duplicate("k2", household) is None
    assert idempotency.is_duplicate("", household) is None


def test_execute_in_simulated_mode_returns_a_simulated_receipt_with_the_request_digest() -> None:
    household = demo()
    action = proposal()
    household.actions.append(ActionRecord(proposal=action, created_at=NOW.isoformat()))
    receipt = execute(action, household, now=NOW, grant_id="g-daniel-ama-payments")
    assert receipt.mode == "SIMULATED" and receipt.provider_ref is None and receipt.response_digest == ""
    assert receipt.label_reason == SIMULATED_LABEL and receipt.rail == "internal-ledger" and receipt.action_id == "act-1"
    source, dest = rails.ledger_accounts(action, household)
    assert receipt.request_digest == receipts.digest(rails.ledger_request(action, source, dest, NOW))
    assert len(receipt.request_digest) == 64 and household.ledger == []  # simulated: the posting is digested, not made
    assert receipt.executed_under_grant == "g-daniel-ama-payments" and receipt.at == NOW.isoformat()
    assert action.idempotency_key == idempotency.key(action, "demo", "2026-09-11")
    assert receipt.id == f"rcpt-{action.idempotency_key[:12]}"
    assert execute(proposal(), household, now=NOW).executed_under_grant is None


def test_execute_is_idempotent_once_recorded() -> None:
    household = demo()
    action = proposal()
    household.actions.append(ActionRecord(proposal=action, created_at=NOW.isoformat()))
    first = execute(action, household, now=NOW)
    record(household, first)
    again = execute(action, household, now=NOW + timedelta(hours=3), execution_mode="live")
    assert again is first
    assert household.actions[0].receipt is first and household.receipts == [first]
    record(household, first)
    assert household.receipts == [first]
    retry = proposal(id="act-1b")  # same request, new proposal object, same day → same key → same receipt
    household.actions.append(ActionRecord(proposal=retry, created_at=NOW.isoformat()))
    assert execute(retry, household, now=NOW) is first
    tomorrow = proposal(id="act-2")
    household.actions.append(ActionRecord(proposal=tomorrow, created_at=NOW.isoformat()))
    assert execute(tomorrow, household, now=NOW + timedelta(days=1)).id != first.id


def test_record_requires_an_action_record() -> None:
    with pytest.raises(KeyError, match="ghost"):
        record(demo(), receipt_for("ghost"))


def test_request_digest_is_canonical() -> None:
    assert request_digest(proposal()) == request_digest(proposal(idempotency_key="anything"))
    assert request_digest(proposal()) != request_digest(proposal(amount="100.01"))
    assert request_digest(proposal()) != request_digest(proposal(rationale="other words"))


def test_rails_table_covers_every_rail() -> None:
    assert set(RAILS) == set(get_args(Rail))
    for meta in RAILS.values():
        assert meta["name"] and meta["label"] and meta["real"]
    assert RAILS["official-form"]["label"].startswith("PREPARE-ONLY")
    assert "no bank rail" in RAILS["internal-ledger"]["label"]
