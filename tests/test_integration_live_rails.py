"""Packet P3b acceptance, offline: the three demo routes under EXECUTION_MODE=live with the fake provider. The rails
accept the skills' proposals exactly as the planner shapes them, so the receipts are COMPLETE (allowance, on the
ledger), COMPLETE (payment to a payee outside the household) and PREPARE-ONLY (the benefits skill's own claim form);
the packet email stays SIMULATED because SES_FROM is unset. No canned fixture changes and no AWS: the SES identity
lookup is forbidden here and the form is rendered into a temporary directory."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from household import config
from household.config import load_settings
from household.executor import forms
from household.executor.receipts import FORM_LABEL, LEDGER_LABEL, SES_NO_FROM_LABEL
from household.fixtures import FixtureStore
from household.model import Household, signed_money
from household.pipeline import run_session
from household.skills.benefits.form import LABELS
from household.skills.benefits.rules import CLHIA_URL, RULES

NOW = datetime(2026, 9, 11, 16, 0, tzinfo=UTC)
SEED = {a.id: signed_money(a.balance) for a in FixtureStore().household("demo").accounts}


@pytest.fixture
def live(monkeypatch, tmp_path):
    monkeypatch.delenv("SES_FROM", raising=False)
    monkeypatch.setattr(config, "ses_verified_identities", lambda region: pytest.fail("live mode must stay offline without SES_FROM"))
    monkeypatch.setattr(forms, "FORMS_DIR", tmp_path / "forms")
    return load_settings(provider="fake", execution_mode="live")


def demo() -> Household:
    return FixtureStore().household("demo")


def deltas(household: Household) -> dict[str, Decimal]:
    """Every balance that differs from the seed, as a signed difference; a new counterparty counts from zero."""
    changed = {a.id: signed_money(a.balance) - SEED.get(a.id, Decimal("0")) for a in household.accounts}
    return {account_id: delta for account_id, delta in changed.items() if delta != 0}


def test_allowance_spend_is_complete_on_the_ledger(live) -> None:
    household = demo()
    r = run_session("kofi-allowance-8", settings=live, household=household, now=NOW)
    [receipt] = r.receipts
    assert (receipt.mode, receipt.label_reason) == ("COMPLETE", LEDGER_LABEL) and r.outcome == "executed"
    [entry] = household.ledger
    assert receipt.provider_ref == entry.id and (entry.debit_account, entry.credit_account, entry.amount) == ("hh-main", "allow-kofi", "8.00")
    assert deltas(household) == {"allow-kofi": Decimal("-8.00"), "hh-main": Decimal("8.00")}
    assert r.execution_mode == "live" and r.briefing is not None and r.guard.override is False


def test_payment_to_a_payee_is_complete_and_the_remainder_waits(live) -> None:
    household = demo()
    r = run_session("daniel-payment-450", settings=live, household=household, now=NOW)
    assert [x.mode for x in r.receipts] == ["COMPLETE"] and r.receipts[0].label_reason == LEDGER_LABEL and r.outcome == "partial"
    assert deltas(household) == {"hh-main": Decimal("-300.00"), "ext-toronto-youth-wind-orchestra": Decimal("300.00")}
    payee = household.account("ext-toronto-youth-wind-orchestra")
    assert payee is not None and (payee.kind, payee.owner_member_id, payee.rules) == ("external", "", {"name": "Toronto Youth Wind Orchestra"})
    [entry] = household.ledger
    assert r.receipts[0].provider_ref == entry.id and entry.memo == "Daniel's band trip deposit (part 1 of 2)"
    assert [d.approver_ids for d in r.approvals_needed] == [["daniel"]]


def test_benefits_claim_is_prepared_from_the_skills_form_and_the_email_is_simulated(live, tmp_path) -> None:
    household = demo()
    r = run_session("ama-dental-cob", settings=live, household=household, now=NOW)
    assert [(x.mode, x.label_reason) for x in r.receipts] == [("PREPARE-ONLY", FORM_LABEL), ("SIMULATED", SES_NO_FROM_LABEL)]
    path = Path(r.receipts[0].provider_ref)
    assert path == tmp_path / "forms" / f"{r.receipts[0].action_id}.md" and path.exists()
    text = path.read_text(encoding="utf-8")
    assert "PREPARED, NOT FILED" in text and CLHIA_URL in text
    for needle in ("Sun Life", "SL-1", "36.00", "$180.00", "$144.00", "September 3, 2026", "Bloor West Dental"):
        assert needle in text, needle
    rows = [line for line in text.splitlines() if line.startswith("| ") and not line.startswith("| Field")]
    assert [row.split(" | ")[0].removeprefix("| ") for row in rows] == list(LABELS.values())
    for citation in RULES.citations:
        assert f"> {citation.quote}\n> — {citation.label}, {citation.url}" in text
    assert household.ledger == [] and deltas(household) == {}  # a prepared form moves no money
