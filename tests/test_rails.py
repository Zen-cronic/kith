"""P3 rails: every receipt's mode is decided in code from the environment. boto3 goes through botocore's Stubber and
httpx through a MockTransport, so nothing here reaches the network; the one live SES send is gated and skipped."""

from __future__ import annotations

import base64
import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import boto3
import httpx
import pytest
from botocore.exceptions import NoCredentialsError
from botocore.stub import Stubber

from household import config
from household.config import ROOT, Settings, load_settings
from household.executor import RAILS, STUB_LABEL, execute, execute_many, forms, rails, receipts, record
from household.executor.receipts import (
    FORM_LABEL,
    LEDGER_INSUFFICIENT_LABEL,
    LEDGER_LABEL,
    REPLAY_LABEL,
    SES_LIVE_LABEL,
    SES_NO_FROM_LABEL,
    SES_NO_RECIPIENT_LABEL,
    SES_UNVERIFIED_LABEL,
    SIMULATED_LABEL,
    STRIPE_LIVE_LABEL,
    STRIPE_NO_KEY_LABEL,
    label_for,
)
from household.model import ActionProposal, ActionRecord, Household, money

DEMO = ROOT / "fixtures" / "households" / "demo.json"
CPSC_FIXTURE = ROOT / "fixtures" / "skills" / "recall" / "cpsc-26639.json"
NOW = datetime(2026, 9, 12, 16, 0, tzinfo=UTC)
SIM = Settings()
LIVE = Settings(execution_mode="live", ses_from="ops@example.test", ses_verified_identities=("ops@example.test", "ama@example.test"))


def demo() -> Household:
    return Household.model_validate(json.loads(DEMO.read_text(encoding="utf-8")))


def proposal(**overrides) -> ActionProposal:
    base = dict(id="act-1", skill_id="allowance", action_type="allowance:transfer", rail="internal-ledger",
                actor_member_id="ama", subject_member_id="kofi", recipient="allow-kofi", amount="10.00",
                evidence_refs=["photo:chores"], rationale="Kofi finished the week's chores")
    return ActionProposal(**{**base, **overrides})


def email(**overrides) -> ActionProposal:
    base = dict(id="mail-1", skill_id="recall", action_type="email:send", rail="ses-email", subject_member_id="ama",
                recipient="ama@example.test", amount=None, evidence_refs=[],
                payload={"subject": "Recall refund request", "body": "Hello"})
    return proposal(**{**base, **overrides})


def recall(**overrides) -> ActionProposal:
    base = dict(id="recall-1", skill_id="recall", action_type="recall:remedy", rail="external-api-readonly",
                subject_member_id="ama", recipient=None, amount=None, payload={"recall_number": "26639"})
    return proposal(**{**base, **overrides})


def payment(**overrides) -> ActionProposal:
    base = dict(id="pay-1", action_type="payment:transfer", rail="stripe-test", subject_member_id="daniel",
                recipient="hh-main", amount="25.00")
    return proposal(**{**base, **overrides})


def run(household: Household, action: ActionProposal, settings: Settings, **kw):
    household.actions.append(ActionRecord(proposal=action, created_at=NOW.isoformat()))
    return execute(action, household, settings, now=NOW, **kw)


def never(*_args, **_kw):
    pytest.fail("no provider call is allowed here")


# Amazon SES


def stubbed_ses(monkeypatch, expected: dict, message_id: str = "0100019-abc") -> Stubber:
    client = boto3.client("sesv2", region_name="us-east-1")
    stub = Stubber(client)
    stub.add_response("send_email", {"MessageId": message_id}, expected)
    stub.activate()
    monkeypatch.setattr(config, "sesv2_client", lambda region: client)
    return stub


def test_ses_live_send_is_complete_with_the_message_id(monkeypatch) -> None:
    household, action = demo(), email()
    expected = rails.ses_request(action, LIVE, "ama@example.test")
    assert expected == {
        "FromEmailAddress": "ops@example.test",
        "Destination": {"ToAddresses": ["ama@example.test"]},
        "Content": {"Simple": {"Subject": {"Data": "Recall refund request"}, "Body": {"Text": {"Data": "Hello"}}}},
    }
    stub = stubbed_ses(monkeypatch, expected)
    receipt = run(household, action, LIVE, grant_id="g-ama-agent-recall")
    stub.assert_no_pending_responses()
    assert receipt.mode == "COMPLETE" and receipt.provider_ref == "0100019-abc" and receipt.rail == "ses-email"
    assert receipt.label_reason == "SES sandbox: verified identities only"
    assert receipt.request_digest == receipts.digest(expected)
    assert receipt.response_digest == receipts.digest({"MessageId": "0100019-abc"})
    assert receipt.executed_under_grant == "g-ama-agent-recall" and receipt.at == NOW.isoformat()


def test_ses_unverified_recipient_is_never_sent(monkeypatch) -> None:
    monkeypatch.setattr(config, "sesv2_client", never)
    receipt = run(demo(), email(recipient="stranger@elsewhere.example"), LIVE)
    assert receipt.mode == "SIMULATED" and receipt.label_reason == "recipient not a verified SES identity (sandbox)"
    assert receipt.provider_ref is None and receipt.response_digest == "" and len(receipt.request_digest) == 64
    # a member id resolves to that member's email; a member without one cannot be mailed
    assert run(demo(), email(id="mail-3", recipient="daniel"), LIVE).label_reason == SES_NO_RECIPIENT_LABEL
    no_from = Settings(execution_mode="live", ses_verified_identities=("ama@example.test",))
    assert run(demo(), email(id="mail-4"), no_from).label_reason == SES_NO_FROM_LABEL
    # a verified domain identity covers every address at that domain
    assert rails.is_verified("Kofi@Example.test", ("example.test",))
    assert not rails.is_verified("x@other.test", ("example.test",))


def test_ses_simulated_mode_digests_the_exact_request(monkeypatch) -> None:
    monkeypatch.setattr(config, "sesv2_client", never)
    action = email()
    receipt = run(demo(), action, SIM)
    assert receipt.mode == "SIMULATED" and receipt.label_reason == SIMULATED_LABEL == STUB_LABEL
    assert receipt.request_digest == receipts.digest(rails.ses_request(action, SIM, "ama@example.test"))
    assert receipt.provider_ref is None and receipt.response_digest == ""


# Internal ledger


def test_ledger_double_entry_balances() -> None:
    household, action = demo(), proposal()
    before = sum(money(a.balance) for a in household.accounts)
    receipt = run(household, action, LIVE)
    assert receipt.mode == "COMPLETE" and receipt.label_reason == "internal household ledger (no bank rail)"
    [entry] = household.ledger
    assert entry.debit_account == "allow-kofi" and entry.credit_account == "hh-main"
    assert entry.amount == "10.00" and entry.currency == "CAD" and entry.action_id == "act-1" and entry.at == NOW.isoformat()
    assert receipt.provider_ref == entry.id and entry.id.startswith("le-")
    assert household.account("hh-main").balance == "2390.00" and household.account("allow-kofi").balance == "52.00"
    assert sum(money(a.balance) for a in household.accounts) == before == Decimal("2460.00")
    assert receipt.response_digest == receipts.digest(entry.model_dump(mode="json"))
    source, dest = rails.ledger_accounts(action, household)
    assert receipt.request_digest == receipts.digest(rails.ledger_request(action, source, dest, NOW))


def test_ledger_rejects_insufficient_balance_and_never_goes_negative() -> None:
    household = demo()
    spend = proposal(id="spend", action_type="payment:transfer", subject_member_id="kofi", recipient="hh-main", amount="99.00")
    receipt = run(household, spend, LIVE)  # Kofi's allowance holds 42.00
    assert receipt.mode == "SIMULATED" and receipt.label_reason == "insufficient balance"
    assert household.ledger == [] and household.account("allow-kofi").balance == "42.00" and household.account("hh-main").balance == "2400.00"
    assert run(demo(), spend, SIM).label_reason == "insufficient balance"  # the check runs before the mode, so a demo never pretends
    exact = proposal(id="exact", action_type="payment:transfer", subject_member_id="kofi", recipient="hh-main", amount="42.00")
    household = demo()
    assert run(household, exact, LIVE).mode == "COMPLETE" and household.account("allow-kofi").balance == "0.00"


def test_ledger_problems_are_labelled_and_post_nothing() -> None:
    household = demo()
    assert run(household, proposal(id="p1", recipient="nowhere"), LIVE).label_reason == "unknown destination account"
    assert run(household, proposal(id="p2", recipient="hh-main", payload={"from_account": "hh-main"}), LIVE).label_reason == "source and destination are the same account"
    assert run(household, proposal(id="p3", amount="0.00"), LIVE).label_reason == "amount must be positive"
    assert run(household, proposal(id="p4", currency="USD"), LIVE).label_reason.startswith("currency mismatch")
    assert run(household, proposal(id="p5", action_type="email:send"), LIVE).label_reason == "internal-ledger cannot execute email:send"
    assert household.ledger == [] and household.account("hh-main").balance == "2400.00"


def test_ledger_simulated_mode_posts_nothing() -> None:
    household, action = demo(), proposal()
    receipt = run(household, action, SIM)
    assert receipt.mode == "SIMULATED" and receipt.label_reason == SIMULATED_LABEL and receipt.provider_ref is None
    assert household.ledger == [] and household.account("hh-main").balance == "2400.00"
    source, dest = rails.ledger_accounts(action, household)
    assert receipt.request_digest == receipts.digest(rails.ledger_request(action, source, dest, NOW))


# Official forms


def form(**overrides) -> ActionProposal:
    fields = json.dumps([{"name": "Applicant name", "value": "Daniel Lim", "quote": "Name of applicant"}, {"name": "Plan number", "value": "ML-7"}])
    quotes = json.dumps([{"text": "Claims must be submitted within 12 months of the date of service.", "source": "Manulife plan booklet, p. 4"}])
    base = dict(id="form-1", skill_id="benefits", action_type="form:prepare", rail="official-form", subject_member_id="daniel",
                recipient=None, amount=None, evidence_refs=["doc:eob-1"],
                payload={"form_id": "manulife-claim", "form_title": "Manulife extended health claim",
                         "form_source": "https://example.test/claim.pdf", "fields": fields, "quotes": quotes})
    return proposal(**{**base, **overrides})


def test_form_render_is_prepare_only_and_writes_the_file(monkeypatch, tmp_path) -> None:
    assert forms.FORMS_DIR == ROOT / "runs" / "forms"
    monkeypatch.setattr(forms, "FORMS_DIR", tmp_path / "forms")
    for settings in (SIM, LIVE):
        receipt = run(demo(), form(), settings)
        assert receipt.mode == "PREPARE-ONLY" and receipt.label_reason == FORM_LABEL
        path = Path(receipt.provider_ref)
        assert path == tmp_path / "forms" / "form-1.md" and path.exists() and not path.with_suffix(".tmp").exists()
        text = path.read_text(encoding="utf-8")
        assert "PREPARED, NOT FILED" in text and "| Applicant name | Daniel Lim | Name of applicant |" in text
        assert "> Claims must be submitted within 12 months of the date of service." in text and "Manulife plan booklet, p. 4" in text
        assert "Daniel Lim (`daniel`)" in text and "`doc:eob-1`" in text and NOW.isoformat() in text
        assert receipt.response_digest == receipts.digest({"path": receipt.provider_ref, "sha256": receipts.digest(text)})
    # one key per field is accepted too; a form without fields or with broken JSON prepares nothing
    flat = form(id="form-2", payload={"form_id": "ltb-t2", "field:Tenant": "Ama Okafor-Lim", "quote:1": "Form T2, Part 1"})
    assert run(demo(), flat, SIM).mode == "PREPARE-ONLY" and "| Tenant | Ama Okafor-Lim |  |" in (tmp_path / "forms" / "form-2.md").read_text(encoding="utf-8")
    broken = run(demo(), form(id="form-3", payload={"fields": "not json"}), SIM)
    assert broken.mode == "SIMULATED" and broken.label_reason.startswith("form payload invalid")
    assert run(demo(), form(id="form-4", payload={}), SIM).label_reason == "form payload invalid: no form fields: a prepared form needs the skill's field schema"
    assert forms.safe_name("../x/y") == "x-y" and forms.safe_name("") == "form"


# External read-only lookups


def test_external_readonly_replays_the_recorded_cpsc_record(monkeypatch) -> None:
    monkeypatch.setattr(rails, "http_client", never)
    recorded = json.loads(CPSC_FIXTURE.read_text(encoding="utf-8"))
    assert recorded["recorded"] is True and recorded["response"][0]["RecallNumber"] == "26639"
    outputs: dict = {}
    receipt = run(demo(), recall(), SIM, outputs=outputs)
    assert receipt.mode == "SIMULATED-replay" and receipt.label_reason == f"replayed CPSC record fetched {recorded['fetched']}"
    assert receipt.provider_ref == recorded["response"][0]["URL"]
    assert receipt.request_digest == receipts.digest(rails.cpsc_request("26639"))
    assert receipt.response_digest == receipts.digest(recorded["response"])
    assert outputs["recall-1"]["summary"]["remedy_options"] == ["Refund"] and outputs["recall-1"]["fetched"] == recorded["fetched"]
    missing = run(demo(), recall(id="recall-2", payload={"recall_number": "00000"}), SIM)
    assert missing.mode == "SIMULATED" and missing.label_reason == "no recorded CPSC fixture at fixtures/skills/recall/cpsc-00000.json"
    assert run(demo(), recall(id="recall-3", payload={}), SIM).label_reason == "no recall_number in payload"
    flight = run(demo(), recall(id="flight-1", action_type="flight:claim", payload={"flight_ident": "AC123"}), SIM)
    assert flight.mode == "SIMULATED" and flight.label_reason == "no recorded AeroAPI fixture at fixtures/skills/flight/aeroapi-AC123.json"


def test_external_readonly_live_lookup_and_network_fallback(monkeypatch) -> None:
    recorded = json.loads(CPSC_FIXTURE.read_text(encoding="utf-8"))
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.method == "GET" and request.url.host == "www.saferproducts.gov"
        assert request.url.params["RecallNumber"] == "26639" and request.url.params["format"] == "json"
        return httpx.Response(200, json=recorded["response"])

    monkeypatch.setattr(rails, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    receipt = run(demo(), recall(), LIVE)
    assert receipt.mode == "COMPLETE" and receipt.label_reason == "live CPSC lookup (read-only)" and len(calls) == 1
    assert receipt.provider_ref == recorded["response"][0]["URL"] and receipt.response_digest == receipts.digest(recorded["response"])

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    monkeypatch.setattr(rails, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(down)))
    fallback = run(demo(), recall(), LIVE)
    assert fallback.mode == "SIMULATED-replay" and fallback.label_reason == f"replayed CPSC record fetched {recorded['fetched']}"
    monkeypatch.setattr(rails, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[]))))
    assert run(demo(), recall(), LIVE).label_reason == "CPSC has no record for this lookup"
    monkeypatch.delenv("AEROAPI_KEY", raising=False)
    no_key = run(demo(), recall(id="flight-1", action_type="flight:claim", payload={"flight_ident": "AC123"}), LIVE)
    assert no_key.mode == "SIMULATED" and no_key.label_reason.startswith("AEROAPI_KEY missing; no recorded AeroAPI fixture")


# Stripe test mode


def test_stripe_test_mode_creates_a_payment_intent(monkeypatch) -> None:
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_123")
    settings = Settings(execution_mode="live", stripe_secret_key_present=True)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.method == "POST" and str(request.url) == rails.STRIPE_URL
        assert base64.b64decode(request.headers["authorization"].split()[1]).decode() == "sk_test_123:"
        body = request.content.decode()
        assert "amount=2500" in body and "currency=cad" in body and "metadata%5Baction_id%5D=pay-1" in body
        return httpx.Response(200, json={"id": "pi_123", "object": "payment_intent", "livemode": False})

    monkeypatch.setattr(rails, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    household, action = demo(), payment()
    receipt = run(household, action, settings)
    assert receipt.mode == "COMPLETE" and receipt.provider_ref == "pi_123" and receipt.label_reason == STRIPE_LIVE_LABEL
    assert seen[0].headers["idempotency-key"] == action.idempotency_key
    assert receipt.request_digest == receipts.digest(rails.stripe_request(action, household))
    assert receipt.response_digest == receipts.digest({"id": "pi_123", "object": "payment_intent", "livemode": False})
    monkeypatch.setattr(rails, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(402, json={"error": "card_declined"}))))
    assert run(demo(), payment(id="pay-2"), settings).label_reason == "Stripe request failed: HTTPStatusError"


def test_stripe_without_a_test_key_is_simulated(monkeypatch) -> None:
    monkeypatch.setattr(rails, "http_client", never)
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    assert run(demo(), payment(), Settings(execution_mode="live")).label_reason == STRIPE_NO_KEY_LABEL
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_999")  # a live key is never used, even when present
    refused = run(demo(), payment(), Settings(execution_mode="live", stripe_secret_key_present=True))
    assert refused.mode == "SIMULATED" and refused.label_reason == STRIPE_NO_KEY_LABEL
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_123")
    assert run(demo(), payment(), Settings(stripe_secret_key_present=True)).label_reason == SIMULATED_LABEL


# Idempotency and the entry point


def test_duplicate_returns_the_prior_receipt_without_acting_twice() -> None:
    household, action = demo(), proposal()
    first = run(household, action, LIVE)
    record(household, first)
    retry = proposal(id="act-1-retry")  # same request, new proposal object, same day
    household.actions.append(ActionRecord(proposal=retry, created_at=NOW.isoformat()))
    assert execute(retry, household, LIVE, now=NOW + timedelta(hours=2)) is first
    assert len(household.ledger) == 1 and household.account("hh-main").balance == "2390.00"
    batch = [proposal(id="act-1-again"), proposal(id="act-3", amount="5.00"), proposal(id="act-3-dup", amount="5.00")]
    for item in batch:
        household.actions.append(ActionRecord(proposal=item, created_at=NOW.isoformat()))
    report = execute_many(batch, household, LIVE, now=NOW)
    assert report.skipped == [("act-1-again", "duplicate"), ("act-3-dup", "duplicate")]
    assert report.receipts[0] is first and report.receipts[1].mode == "COMPLETE" and report.receipts[2] is report.receipts[1]
    assert len(household.ledger) == 2 and household.account("allow-kofi").balance == "57.00"
    assert household.action("act-3").receipt is report.receipts[1] and household.receipts == [first, report.receipts[1]]


def test_execute_keeps_the_p1_signature_and_loads_settings_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("EXECUTION_MODE", "simulated")
    monkeypatch.setattr(config, "ses_verified_identities", never)
    household, action = demo(), proposal()
    household.actions.append(ActionRecord(proposal=action, created_at=NOW.isoformat()))
    receipt = execute(action, household, now=NOW, grant_id="g-x")
    assert receipt.mode == "SIMULATED" and receipt.executed_under_grant == "g-x" and receipt.id == f"rcpt-{action.idempotency_key[:12]}"
    assert execute(action, household, LIVE, now=NOW, execution_mode="simulated").mode == "SIMULATED"  # explicit override wins
    assert execute(action, household, SIM, now=NOW, execution_mode="live").mode == "COMPLETE"


# Settings and the SES identity cache


def test_settings_cache_ses_identities_only_in_live_mode(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(config, "ses_verified_identities", lambda region: calls.append(region) or ("ops@example.test",))
    for key, value in {"EXECUTION_MODE": "simulated", "SES_FROM": " ops@example.test ", "STRIPE_SECRET_KEY": "sk_test_1", "HOUSEHOLD_ALLOW_LIVE_SES": "1"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("AEROAPI_KEY", raising=False)
    s = load_settings(provider="fake")
    assert s.execution_mode == "simulated" and s.ses_from == "ops@example.test" and s.ses_verified_identities == () and calls == []
    assert s.stripe_secret_key_present and not s.aeroapi_key_present and s.allow_live_ses
    monkeypatch.setenv("EXECUTION_MODE", "LIVE")
    live = load_settings(provider="fake")
    assert live.execution_mode == "live" and live.ses_verified_identities == ("ops@example.test",) and calls == ["us-east-1"]
    assert load_settings(provider="fake", ses_verified_identities=("x@y.test",)).ses_verified_identities == ("x@y.test",) and calls == ["us-east-1"]
    with pytest.raises(ValueError, match="EXECUTION_MODE"):
        load_settings(provider="fake", execution_mode="prod")


def test_ses_identity_listing_pages_and_filters_through_stubber(monkeypatch) -> None:
    client = boto3.client("sesv2", region_name="us-east-1")
    with Stubber(client) as stub:
        stub.add_response("list_email_identities", {"EmailIdentities": [
            {"IdentityType": "EMAIL_ADDRESS", "IdentityName": "ops@example.test", "SendingEnabled": True, "VerificationStatus": "SUCCESS"},
            {"IdentityType": "EMAIL_ADDRESS", "IdentityName": "pending@example.test", "SendingEnabled": False, "VerificationStatus": "PENDING"},
        ], "NextToken": "t2"}, {"PageSize": 100})
        stub.add_response("list_email_identities", {"EmailIdentities": [
            {"IdentityType": "DOMAIN", "IdentityName": "example.org", "SendingEnabled": True, "VerificationStatus": "SUCCESS"},
        ]}, {"PageSize": 100, "NextToken": "t2"})
        monkeypatch.setattr(config, "sesv2_client", lambda region: client)
        assert config.ses_verified_identities("us-east-1") == ("example.org", "ops@example.test")
        stub.assert_no_pending_responses()

    def no_credentials(region: str):
        raise NoCredentialsError()

    monkeypatch.setattr(config, "sesv2_client", no_credentials)
    assert config.ses_verified_identities("us-east-1") == ()


# Labels


def test_label_reasons_are_the_exact_packet_strings() -> None:
    assert SES_LIVE_LABEL == "SES sandbox: verified identities only"
    assert SES_UNVERIFIED_LABEL == "recipient not a verified SES identity (sandbox)"
    assert LEDGER_LABEL == "internal household ledger (no bank rail)"
    assert LEDGER_INSUFFICIENT_LABEL == "insufficient balance"
    assert REPLAY_LABEL.format(source="CPSC", fetched="2026-09-12") == "replayed CPSC record fetched 2026-09-12"
    for rail, meta in RAILS.items():
        assert meta["reasons"] and all(isinstance(reason, str) and reason for reason in meta["reasons"]), rail
    assert {SES_LIVE_LABEL, SES_UNVERIFIED_LABEL, SIMULATED_LABEL} <= set(RAILS["ses-email"]["reasons"])
    assert {LEDGER_LABEL, LEDGER_INSUFFICIENT_LABEL} <= set(RAILS["internal-ledger"]["reasons"])
    assert RAILS["official-form"]["reasons"] == (FORM_LABEL,)
    assert label_for("official-form", LIVE) == label_for("official-form", SIM)
    assert label_for("official-form", SIM).mode == "PREPARE-ONLY"
    for rail in ("ses-email", "internal-ledger", "stripe-test"):
        assert label_for(rail, SIM).mode == "SIMULATED" and label_for(rail, SIM).reason == SIMULATED_LABEL
    assert label_for("ses-email", LIVE).mode == "COMPLETE" and label_for("internal-ledger", LIVE).mode == "COMPLETE"
    assert label_for("stripe-test", LIVE).reason == STRIPE_NO_KEY_LABEL
    assert label_for("external-api-readonly", SIM, {"fetched": "2026-09-12"}).mode == "SIMULATED-replay"
    assert label_for("external-api-readonly", SIM).reason == "no recorded CPSC response to replay"
    assert label_for("internal-ledger", LIVE, {"problem": "insufficient balance"}).reason == "insufficient balance"


# The one live send, only when the operator opens every gate (H1)

LIVE_GATES = (os.environ.get("EXECUTION_MODE") == "live" and bool(os.environ.get("SES_FROM")) and os.environ.get("HOUSEHOLD_ALLOW_LIVE_SES") == "1")


@pytest.mark.skipif(not LIVE_GATES, reason="live SES send needs EXECUTION_MODE=live, SES_FROM and HOUSEHOLD_ALLOW_LIVE_SES=1 (operator gate H1)")
def test_one_live_ses_send_to_the_verified_sender() -> None:
    settings = load_settings(provider="fake")
    assert settings.execution_mode == "live" and settings.allow_live_ses and settings.ses_from
    assert rails.is_verified(settings.ses_from, settings.ses_verified_identities), "SES_FROM is not a verified identity in this account"
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    action = email(id=f"live-{stamp}", recipient=settings.ses_from,
                   payload={"subject": "household P3 live SES proof", "body": "One gated live send from tests/test_rails.py."})
    household = demo()
    household.actions.append(ActionRecord(proposal=action, created_at=NOW.isoformat()))
    receipt = execute(action, household, settings, now=datetime.now(UTC))
    assert receipt.mode == "COMPLETE" and receipt.provider_ref and receipt.label_reason == SES_LIVE_LABEL
    out = ROOT / "runs" / "receipts"
    out.mkdir(parents=True, exist_ok=True)
    (out / "ses-live.json").write_text(receipt.model_dump_json(indent=2) + "\n", encoding="utf-8")
