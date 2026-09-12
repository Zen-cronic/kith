"""Authority is decided in code: a parametrized matrix over the demo ledger, every rule id covered.

Each case pins outcome, rule id, grant id, approvers and a reason fragment, and also checks that `decide`
is deterministic and leaves the household untouched.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from household.authority import ALL_RULE_IDS, decide, explain
from household.config import ROOT
from household.model import ActionProposal, ActionRecord, Household, Receipt

DEMO = ROOT / "fixtures" / "households" / "demo.json"
NOW = datetime(2026, 9, 11, 16, 0, tzinfo=UTC)
GUARDIANS = ("ama", "daniel")


def demo() -> Household:
    return Household.model_validate(json.loads(DEMO.read_text(encoding="utf-8")))


def proposal(**overrides) -> ActionProposal:
    base = dict(id="act-1", skill_id="test", action_type="email:send", rail="ses-email",
                actor_member_id="ama", subject_member_id="ama", recipient="school@example.test")
    return ActionProposal(**{**base, **overrides})


# Ledger variants


def with_history(*, subject: str, action_type: str, amount: str, grant_id: str, days_ago: int) -> Callable:
    """A prior executed action so limit windows have something to add up."""

    def setup(household: Household) -> Household:
        at = (NOW - timedelta(days=days_ago)).isoformat()
        action_id = f"hist-{grant_id}-{days_ago}"
        past = ActionProposal(id=action_id, skill_id="test", action_type=action_type, rail="internal-ledger",
                              actor_member_id=subject, subject_member_id=subject, amount=amount, idempotency_key=action_id)
        household.actions.append(ActionRecord(proposal=past, created_at=at))
        household.receipts.append(Receipt(id=f"r-{action_id}", action_id=action_id, rail="internal-ledger",
                                          mode="SIMULATED", request_digest="d", response_digest="", at=at,
                                          executed_under_grant=grant_id, label_reason="history"))
        return household

    return setup


def only_grants(*ids: str) -> Callable:
    def setup(household: Household) -> Household:
        household.grants = [g for g in household.grants if g.id in ids]
        return household

    return setup


def no_guardians(member_id: str) -> Callable:
    def setup(household: Household) -> Household:
        household.member(member_id).guardians = []
        return household

    return setup


@dataclass(frozen=True)
class Case:
    id: str
    rule: str
    outcome: str
    proposal: dict
    grant_id: str | None = None
    approvers: tuple[str, ...] = ()
    reason: str = ""
    setup: Callable[[Household], Household] | None = None


KOFI_ALLOWANCE = dict(action_type="allowance:transfer", rail="internal-ledger", actor_member_id="kofi",
                      subject_member_id="kofi", recipient=None)
AMA_FOR_DANIEL_PAYMENT = dict(action_type="payment:transfer", rail="internal-ledger", actor_member_id="ama",
                              subject_member_id="daniel", recipient="hh-main", amount="100.00")
KOFI_HISTORY = dict(subject="kofi", action_type="allowance:transfer", amount="10.00", grant_id="rule:minor-allowance")
PAYMENT_HISTORY = dict(subject="daniel", action_type="payment:transfer", grant_id="g-daniel-ama-payments")

CASES = [
    # 1. unknown members
    Case("unknown-actor", "unknown-member", "block", dict(actor_member_id="zed"), reason="unknown member"),
    Case("unknown-subject", "unknown-member", "block", dict(subject_member_id="zed"), reason="unknown member"),
    # 2. forged grants
    Case("forged-grant", "forged-grant", "block",
         dict(action_type="benefits:claim", rail="official-form", subject_member_id="daniel", claimed_grant_id="g-forged-999"),
         reason="forged grant"),
    Case("forged-grant-by-minor", "forged-grant", "block",
         dict(actor_member_id="kofi", subject_member_id="kofi", claimed_grant_id="g-made-up"), reason="forged grant"),
    # 3. rail policy
    Case("rail-payment-on-email", "rail-policy", "block",
         dict(action_type="payment:transfer", rail="ses-email", amount="50.00"), reason="no consumer bank rail"),
    Case("rail-payment-on-official-form", "rail-policy", "block",
         {**AMA_FOR_DANIEL_PAYMENT, "rail": "official-form"}, reason="no consumer bank rail"),
    Case("rail-email-no-recipient", "rail-policy", "block", dict(recipient=None), reason="no recipient"),
    Case("rail-email-blank-recipient", "rail-policy", "block", dict(recipient="   "), reason="no recipient"),
    # 4. minors
    Case("minor-allowance-under-limit", "minor-allowance", "allow", {**KOFI_ALLOWANCE, "amount": "8.00"},
         grant_id="rule:minor-allowance", reason="without asking"),
    Case("minor-allowance-at-limit", "minor-allowance", "allow",
         {**KOFI_ALLOWANCE, "actor_member_id": "mei", "subject_member_id": "mei", "amount": "5.00"},
         grant_id="rule:minor-allowance", reason="without asking"),
    Case("minor-allowance-over-limit", "minor-guardian", "needs-approval", {**KOFI_ALLOWANCE, "amount": "12.00"},
         approvers=GUARDIANS, reason="above"),
    Case("minor-allowance-weekly-cap", "minor-guardian", "needs-approval", {**KOFI_ALLOWANCE, "amount": "8.00"},
         approvers=GUARDIANS, reason="weekly allowance cap", setup=with_history(**KOFI_HISTORY, days_ago=2)),
    Case("minor-allowance-weekly-window-reset", "minor-allowance", "allow", {**KOFI_ALLOWANCE, "amount": "8.00"},
         grant_id="rule:minor-allowance", reason="0 of 15.00 used", setup=with_history(**KOFI_HISTORY, days_ago=9)),
    Case("minor-allowance-no-amount", "minor-guardian", "needs-approval", {**KOFI_ALLOWANCE, "amount": None},
         approvers=GUARDIANS, reason="needs an amount"),
    Case("minor-allowance-for-sibling", "minor-guardian", "needs-approval",
         {**KOFI_ALLOWANCE, "subject_member_id": "mei", "amount": "3.00"}, approvers=GUARDIANS, reason="is a minor"),
    Case("minor-email", "minor-guardian", "needs-approval",
         dict(actor_member_id="kofi", subject_member_id="kofi", recipient="teacher@example.test"),
         approvers=GUARDIANS, reason="minor requires guardian approval"),
    Case("minor-acts-for-parent", "minor-guardian", "needs-approval",
         dict(action_type="payment:transfer", rail="internal-ledger", actor_member_id="kofi", subject_member_id="ama",
              recipient="hh-main", amount="20.00"),
         approvers=GUARDIANS, reason="minor requires guardian approval"),
    Case("minor-no-guardians", "minor-no-guardian", "block",
         dict(actor_member_id="kofi", subject_member_id="kofi"), reason="no guardian", setup=no_guardians("kofi")),
    # 5. adults for themselves
    Case("self-no-amount", "self", "allow", {}, grant_id="rule:self", reason="within the self-confirm limit"),
    Case("self-under-limit", "self", "allow",
         dict(action_type="payment:transfer", rail="internal-ledger", recipient="hh-main", amount="150.00"),
         grant_id="rule:self", reason="within the self-confirm limit"),
    Case("self-at-limit", "self", "allow",
         dict(action_type="payment:transfer", rail="stripe-test", recipient="acct_test", amount="200.00"),
         grant_id="rule:self", reason="within the self-confirm limit"),
    Case("self-over-limit", "self-confirm", "needs-approval",
         dict(action_type="payment:transfer", rail="internal-ledger", recipient="hh-main", amount="250.00"),
         approvers=("ama",), reason="self-confirm above limit"),
    Case("self-official-form-prepare-only", "self", "allow",
         dict(action_type="form:prepare", rail="official-form", actor_member_id="daniel", subject_member_id="daniel",
              recipient=None),
         grant_id="rule:self", reason="PREPARE-ONLY rail"),
    # 6. guardians for their minors
    Case("parent-for-minor", "parent-for-minor", "allow", dict(subject_member_id="kofi"),
         grant_id="rule:parent-for-minor", reason="guardian of"),
    Case("parent-for-minor-large-amount", "parent-for-minor", "allow",
         dict(action_type="payment:transfer", rail="internal-ledger", actor_member_id="daniel", subject_member_id="mei",
              recipient="hh-main", amount="500.00"),
         grant_id="rule:parent-for-minor", reason="guardian of"),
    Case("guardian-form-prepare-only", "parent-for-minor", "allow",
         dict(action_type="form:prepare", rail="official-form", actor_member_id="daniel", subject_member_id="mei",
              recipient=None),
         grant_id="rule:parent-for-minor", reason="PREPARE-ONLY rail"),
    # 7. grants
    Case("spouse-grant-within-limit", "grant", "allow",
         dict(action_type="benefits:claim", rail="official-form", subject_member_id="daniel", amount="400.00"),
         grant_id="g-daniel-ama-benefits", reason="spouse-grant"),
    Case("spouse-grant-no-amount", "grant", "allow", dict(subject_member_id="daniel", recipient="claims@example.test"),
         grant_id="g-daniel-ama-benefits", reason="covers"),
    Case("spouse-grant-over-per-action", "grant-refused", "needs-approval",
         dict(action_type="benefits:claim", rail="official-form", subject_member_id="daniel", amount="600.00"),
         approvers=("daniel",), reason="per-action limit"),
    Case("spouse-payment-within-month", "grant", "allow", AMA_FOR_DANIEL_PAYMENT,
         grant_id="g-daniel-ama-payments", reason="per-month"),
    Case("spouse-payment-month-cap", "grant-refused", "needs-approval", AMA_FOR_DANIEL_PAYMENT,
         approvers=("daniel",), reason="per-month limit",
         setup=with_history(**PAYMENT_HISTORY, amount="250.00", days_ago=5)),
    Case("spouse-payment-exactly-cap", "grant", "allow", AMA_FOR_DANIEL_PAYMENT,
         grant_id="g-daniel-ama-payments", setup=with_history(**PAYMENT_HISTORY, amount="200.00", days_ago=5)),
    Case("spouse-payment-month-window-reset", "grant", "allow", AMA_FOR_DANIEL_PAYMENT,
         grant_id="g-daniel-ama-payments", setup=with_history(**PAYMENT_HISTORY, amount="250.00", days_ago=31)),
    Case("expired-grant-claimed", "grant-refused", "needs-approval",
         {**AMA_FOR_DANIEL_PAYMENT, "claimed_grant_id": "g-expired"}, approvers=("daniel",), reason="expired"),
    Case("expired-grant-only", "grant-refused", "needs-approval", AMA_FOR_DANIEL_PAYMENT,
         approvers=("daniel",), reason="expired", setup=only_grants("g-expired")),
    Case("revoked-grant-claimed", "grant-refused", "needs-approval",
         dict(action_type="benefits:claim", rail="official-form", actor_member_id="daniel", subject_member_id="ama",
              claimed_grant_id="g-revoked"),
         approvers=("ama",), reason="revoked"),
    Case("revoked-grant-only", "grant-refused", "needs-approval",
         dict(action_type="benefits:claim", rail="official-form", actor_member_id="daniel", subject_member_id="ama"),
         approvers=("ama",), reason="revoked"),
    Case("scope-mismatch", "no-grant", "needs-approval",
         dict(action_type="flight:claim", rail="external-api-readonly", subject_member_id="daniel"),
         approvers=("daniel",), reason="no grant covers flight:claim"),
    Case("claimed-grant-wrong-parties", "grant-refused", "needs-approval",
         dict(action_type="benefits:claim", rail="official-form", subject_member_id="daniel",
              claimed_grant_id="g-ama-agent-recall"),
         approvers=("daniel",), reason="does not cover"),
    Case("claimed-valid-grant", "grant", "allow",
         {**AMA_FOR_DANIEL_PAYMENT, "claimed_grant_id": "g-daniel-ama-payments"}, grant_id="g-daniel-ama-payments"),
    Case("currency-mismatch", "grant-refused", "needs-approval",
         dict(action_type="benefits:claim", rail="official-form", subject_member_id="daniel", amount="100.00",
              currency="USD"),
         approvers=("daniel",), reason="USD"),
    Case("agent-under-agent-grant", "grant", "allow",
         dict(action_type="recall:remedy", rail="external-api-readonly", actor_member_id="agent", subject_member_id="ama",
              amount="50.00"),
         grant_id="g-ama-agent-recall", reason="the agent"),
    Case("agent-over-grant-limit", "grant-refused", "needs-approval",
         dict(action_type="recall:remedy", rail="external-api-readonly", actor_member_id="agent", subject_member_id="ama",
              amount="150.00"),
         approvers=("ama",), reason="per-action limit"),
    Case("agent-no-grant", "no-grant", "needs-approval",
         dict(action_type="benefits:claim", rail="official-form", actor_member_id="agent", subject_member_id="daniel"),
         approvers=("daniel",), reason="no grant covers benefits:claim"),
    Case("agent-grant-covers-member-request", "grant", "allow",
         dict(actor_member_id="daniel", subject_member_id="ama", recipient="recalls@example.test"),
         grant_id="g-ama-agent-recall", reason="covers"),
    Case("no-grant-between-spouses", "no-grant", "needs-approval",
         dict(action_type="payment:transfer", rail="internal-ledger", actor_member_id="daniel", subject_member_id="ama",
              recipient="hh-main", amount="50.00"),
         approvers=("ama",), reason="no grant covers payment:transfer"),
]


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_decision(case: Case) -> None:
    household = case.setup(demo()) if case.setup else demo()
    before = household.model_dump(mode="json")
    action = proposal(**case.proposal)
    decision = decide(action, household, NOW)
    assert decision.action_id == action.id
    assert (decision.outcome, decision.rule_id) == (case.outcome, case.rule)
    assert decision.grant_id == case.grant_id
    assert decision.approver_ids == list(case.approvers)
    assert decision.reasons and case.reason.lower() in " ".join(decision.reasons).lower()
    assert decision == decide(action, household, NOW)  # deterministic
    assert household.model_dump(mode="json") == before  # pure: the ledger is never mutated by a decision
    sentence = explain(decision)
    prefix = {"allow": "Allowed under ", "block": "Blocked: ", "needs-approval": "Needs approval from "}[case.outcome]
    assert sentence.startswith(prefix) and sentence.endswith(".") and "\n" not in sentence
    assert ". " not in sentence  # one sentence


def test_every_rule_id_is_covered_by_at_least_one_case() -> None:
    assert len(CASES) >= 25
    assert len({c.id for c in CASES}) == len(CASES)
    assert {c.rule for c in CASES} == set(ALL_RULE_IDS)


def test_decisions_are_the_same_across_the_day() -> None:
    action = proposal(**AMA_FOR_DANIEL_PAYMENT)
    morning = decide(action, demo(), datetime(2026, 9, 11, 6, 0, tzinfo=UTC))
    evening = decide(action, demo(), datetime(2026, 9, 11, 23, 59, tzinfo=UTC))
    naive = decide(action, demo(), datetime(2026, 9, 11, 12, 0))
    assert morning == evening == naive


def test_expiry_is_judged_against_the_clock_passed_in() -> None:
    action = proposal(**AMA_FOR_DANIEL_PAYMENT)
    household = only_grants("g-expired")(demo())
    before_expiry = decide(action, household, datetime(2026, 7, 31, 12, 0, tzinfo=UTC))
    after_expiry = decide(action, household, datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    assert before_expiry.outcome == "allow" and before_expiry.grant_id == "g-expired"
    assert after_expiry.outcome == "needs-approval" and "expired" in " ".join(after_expiry.reasons)
