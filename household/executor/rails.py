"""The five action rails. They are never model tools: only `executor.execute` dispatches here, after the authority
engine allowed the action and the idempotency key was found unused.

Each rail returns a `RailResult` with the exact provider request (digested onto the receipt whether or not it was
sent), the provider response when something real happened, and the label decided by `receipts.label_for`.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from .. import config as _config
from ..config import ROOT, Settings
from ..model import Account, ActionProposal, Household, LedgerEntry, money, signed_money
from . import forms
from .receipts import LEDGER_INSUFFICIENT_LABEL, SES_NO_RECIPIENT_LABEL, Label, RailResult, digest, label_for

CPSC_URL = "https://www.saferproducts.gov/RestWebServices/Recall"
AEROAPI_URL = "https://aeroapi.flightaware.com/aeroapi"
STRIPE_URL = "https://api.stripe.com/v1/payment_intents"
SKILL_FIXTURES = ROOT / "fixtures" / "skills"
HTTP_TIMEOUT = 15.0
Outputs = dict[str, Any] | None


def http_client() -> httpx.Client:
    """One place to build the HTTP client, so tests substitute an httpx.MockTransport and never reach the network."""
    return httpx.Client(timeout=HTTP_TIMEOUT)


def _relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path)


# Amazon SES (sesv2)


def resolve_email(household: Household, recipient: str | None) -> str | None:
    """An address as given, or a member id resolved to that member's email."""
    if not recipient:
        return None
    if "@" in recipient:
        return recipient.strip()
    member = household.member(recipient)
    return member.email if member and member.email else None


def is_verified(address: str, identities: tuple[str, ...]) -> bool:
    """SES sandbox rule: the recipient must itself be a verified identity, or belong to a verified domain."""
    address = address.strip().lower()
    known = {identity.strip().lower() for identity in identities}
    return address in known or address.rsplit("@", 1)[-1] in known


def ses_request(proposal: ActionProposal, settings: Settings, recipient: str) -> dict[str, Any]:
    """The exact `sesv2.send_email` kwargs; digested onto the receipt whether or not the call is made."""
    subject = proposal.payload.get("subject") or f"Household: {proposal.action_type}"
    body = proposal.payload.get("body") or proposal.rationale
    return {
        "FromEmailAddress": settings.ses_from or "",
        "Destination": {"ToAddresses": [recipient]},
        "Content": {"Simple": {"Subject": {"Data": subject}, "Body": {"Text": {"Data": body}}}},
    }


def ses_email(proposal: ActionProposal, household: Household, settings: Settings, at: datetime, outputs: Outputs = None) -> RailResult:
    recipient = resolve_email(household, proposal.recipient)
    request = ses_request(proposal, settings, recipient or "")
    if recipient is None:
        return RailResult(Label("SIMULATED", SES_NO_RECIPIENT_LABEL), request)
    label = label_for("ses-email", settings, {"verified": is_verified(recipient, settings.ses_verified_identities)})
    if label.mode != "COMPLETE":
        return RailResult(label, request)
    reply = _config.sesv2_client(settings.aws_region).send_email(**request)
    message_id = str(reply["MessageId"])
    return RailResult(label, request, response={"MessageId": message_id}, provider_ref=message_id)


# Internal household ledger (double entry, no bank)


def _account_by_ref(household: Household, ref: str) -> Account | None:
    """An account id, or a member id resolved to that member's allowance account."""
    return household.account(ref) or household.allowance_account(ref)


def _destination(household: Household, ref: str) -> Account:
    """An account id, a member id (their allowance), or a payee outside the household, which is its counterparty
    account `ext-<slug>`. A new counterparty is attached to the ledger only when a posting actually happens."""
    return _account_by_ref(household, ref) or household.counterparty_account(ref, attach=False)


def ledger_accounts(proposal: ActionProposal, household: Household) -> tuple[Account | None, Account | None]:
    """(source, destination). An allowance transfer spends from the subject's allowance account and a payment pays
    from the household account; an explicit `from_account` overrides the source (a top-up is household -> allowance).
    The destination is `to_account` or `recipient`: an account id, a member id, or a payee name outside the household."""
    pot = next((a for a in household.accounts if a.kind == "household"), None)
    own = household.allowance_account(proposal.subject_member_id)
    src = (proposal.payload.get("from_account") or "").strip()
    dst = (proposal.payload.get("to_account") or proposal.recipient or "").strip()
    default = own if proposal.action_type == "allowance:transfer" else pot
    source = _account_by_ref(household, src) if src else default
    dest = _destination(household, dst) if dst else None
    return source, dest


def ledger_problem(proposal: ActionProposal, source: Account | None, dest: Account | None) -> str | None:
    if proposal.action_type not in ("payment:transfer", "allowance:transfer"):
        return f"internal-ledger cannot execute {proposal.action_type}"
    if proposal.amount is None or money(proposal.amount) <= 0:
        return "amount must be positive"
    if source is None:
        return "unknown source account"
    if dest is None:
        return "unknown destination account"
    if source.id == dest.id:
        return "source and destination are the same account"
    if source.currency != proposal.currency or dest.currency != proposal.currency:
        return f"currency mismatch: {source.currency}/{dest.currency} accounts, {proposal.currency} transfer"
    if source.kind != "external" and signed_money(source.balance) < money(proposal.amount):
        return LEDGER_INSUFFICIENT_LABEL  # household money never goes negative; the outside world may
    return None


def ledger_request(proposal: ActionProposal, source: Account | None, dest: Account | None, at: datetime) -> dict[str, str]:
    """The double-entry posting exactly as it would be written: debit the receiving account, credit the paying one."""
    return {
        "debit_account": dest.id if dest else "",
        "credit_account": source.id if source else "",
        "amount": proposal.amount or "0.00",
        "currency": proposal.currency,
        "memo": proposal.payload.get("memo") or proposal.payload.get("purpose") or proposal.rationale or proposal.action_type,
        "action_id": proposal.id,
        "at": at.isoformat(),
    }


def format_money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01")))


def internal_ledger(proposal: ActionProposal, household: Household, settings: Settings, at: datetime, outputs: Outputs = None) -> RailResult:
    source, dest = ledger_accounts(proposal, household)
    request = ledger_request(proposal, source, dest, at)
    label = label_for("internal-ledger", settings, {"problem": ledger_problem(proposal, source, dest)})
    if label.mode != "COMPLETE" or source is None or dest is None:
        return RailResult(label, request)
    amount = money(proposal.amount or "0")
    entry = LedgerEntry(id=f"le-{digest(request)[:12]}", **request)
    if household.account(dest.id) is None:  # a payee outside the household joins the ledger with its first posting
        household.accounts.append(dest)
    source.balance = format_money(signed_money(source.balance) - amount)
    dest.balance = format_money(signed_money(dest.balance) + amount)
    household.ledger.append(entry)
    return RailResult(label, request, response=entry.model_dump(mode="json"), provider_ref=entry.id)


# Stripe, test mode only (httpx, no SDK)


def stripe_request(proposal: ActionProposal, household: Household) -> dict[str, Any]:
    cents = int((money(proposal.amount or "0") * 100).to_integral_value())
    form = {
        "amount": str(cents),
        "currency": proposal.currency.lower(),
        "description": proposal.payload.get("memo") or proposal.rationale or proposal.action_type,
        "metadata[action_id]": proposal.id,
        "metadata[household_id]": household.id,
    }
    return {"method": "POST", "url": STRIPE_URL, "form": form, "idempotency_key": proposal.idempotency_key}


def stripe_test(proposal: ActionProposal, household: Household, settings: Settings, at: datetime, outputs: Outputs = None) -> RailResult:
    key = os.environ.get("STRIPE_SECRET_KEY", "") if settings.stripe_secret_key_present else ""
    request = stripe_request(proposal, household)
    if proposal.amount is None or money(proposal.amount) <= 0:
        return RailResult(Label("SIMULATED", "amount must be positive"), request)
    label = label_for("stripe-test", settings, {"test_key": key.startswith("sk_test_")})
    if label.mode != "COMPLETE":
        return RailResult(label, request)
    try:
        with http_client() as client:
            reply = client.post(STRIPE_URL, data=request["form"], auth=(key, ""), headers={"Idempotency-Key": proposal.idempotency_key})
            reply.raise_for_status()
            body = reply.json()
    except (httpx.HTTPError, ValueError) as exc:
        return RailResult(Label("SIMULATED", f"Stripe request failed: {type(exc).__name__}"), request)
    return RailResult(label, request, response=body, provider_ref=str(body.get("id") or "") or None)


# Official forms: prepared, never filed


def form_render(proposal: ActionProposal, household: Household, settings: Settings, at: datetime, outputs: Outputs = None) -> RailResult:
    try:
        spec = forms.spec_for(proposal)  # the proposing skill's own schema first, else the payload's
    except ValueError as exc:
        return RailResult(Label("SIMULATED", f"form payload invalid: {exc}"), {"action_id": proposal.id, "payload": proposal.payload})
    text = forms.render(proposal, household, spec, at)
    ref = _relative(forms.write(text, proposal.id))
    request = {"action_id": proposal.id, "subject_member_id": proposal.subject_member_id, "form": spec.as_dict()}
    if outputs is not None:
        outputs[proposal.id] = {"form_path": ref, "form": spec.as_dict()}
    return RailResult(label_for("official-form", settings), request, response={"path": ref, "sha256": digest(text)}, provider_ref=ref)


# External read-only lookups: CPSC recalls, AeroAPI flights


def cpsc_request(recall_number: str) -> dict[str, Any]:
    return {"method": "GET", "url": CPSC_URL, "params": {"format": "json", "RecallNumber": recall_number}}


def cpsc_fixture_path(recall_number: str) -> Path:
    return SKILL_FIXTURES / "recall" / f"cpsc-{recall_number}.json"


def aeroapi_request(ident: str) -> dict[str, Any]:
    return {"method": "GET", "url": f"{AEROAPI_URL}/flights/{ident}", "headers": {"x-apikey": "<AEROAPI_KEY>"}}


def aeroapi_fixture_path(ident: str) -> Path:
    return SKILL_FIXTURES / "flight" / f"aeroapi-{ident}.json"


def load_recorded(path: Path) -> dict[str, Any] | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def recorded_fetch_date(path: Path) -> str | None:
    recorded = load_recorded(path)
    return str(recorded.get("fetched")) if recorded and recorded.get("recorded") else None


def summarize_recall(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "recall_number": record.get("RecallNumber"),
        "title": record.get("Title"),
        "url": record.get("URL"),
        "recall_date": record.get("RecallDate"),
        "remedy_options": [o.get("Option") for o in record.get("RemedyOptions") or []],
        "remedies": [r.get("Name") for r in record.get("Remedies") or []],
        "consumer_contact": record.get("ConsumerContact"),
        "hazards": [h.get("Name") for h in record.get("Hazards") or []],
        "products": [p.get("Name") for p in record.get("Products") or []],
    }


def _first_recall(data: Any) -> dict[str, Any] | None:
    return data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else None


def _first_flight(data: Any) -> dict[str, Any] | None:
    flights = data.get("flights") if isinstance(data, dict) else None
    return flights[0] if isinstance(flights, list) and flights and isinstance(flights[0], dict) else None


def _lookup(
    proposal: ActionProposal,
    settings: Settings,
    outputs: Outputs,
    *,
    source: str,
    request: dict[str, Any],
    fixture: Path,
    fetch: Callable[[], Any] | None,
    unavailable: str | None,
    primary: Callable[[Any], dict[str, Any] | None],
    summarize: Callable[[dict[str, Any]], dict[str, Any]],
    ref: Callable[[dict[str, Any]], str],
) -> RailResult:
    """Live when EXECUTION_MODE=live and the network answers; else replay the recorded fixture; else SIMULATED."""
    problems: list[str] = []
    if settings.execution_mode == "live":
        if fetch is None:
            problems.append(unavailable or f"live {source} lookup unavailable")
        else:
            try:
                data = fetch()
            except (httpx.HTTPError, ValueError) as exc:
                problems.append(f"live {source} lookup failed: {type(exc).__name__}")
            else:
                record = primary(data)
                if record is None:
                    return RailResult(Label("SIMULATED", f"{source} has no record for this lookup"), request, response=data)
                label = label_for("external-api-readonly", settings, {"source": source, "network_ok": True})
                if outputs is not None:
                    outputs[proposal.id] = {"source": source, "mode": label.mode, "record": record, "summary": summarize(record)}
                return RailResult(label, request, response=data, provider_ref=ref(record))
    recorded = load_recorded(fixture)
    record = primary(recorded.get("response")) if recorded else None
    if recorded is None or record is None:
        problems.append(f"no recorded {source} fixture at {_relative(fixture)}")
        return RailResult(Label("SIMULATED", "; ".join(problems)), request)
    if not recorded.get("recorded"):
        return RailResult(Label("SIMULATED", f"constructed {source} fixture at {_relative(fixture)} (not a recorded response)"), request, response=recorded["response"])
    label = label_for("external-api-readonly", settings, {"source": source, "network_ok": False, "fetched": recorded.get("fetched") or "unknown date"})
    if outputs is not None:
        outputs[proposal.id] = {"source": source, "mode": label.mode, "record": record, "summary": summarize(record), "fetched": recorded.get("fetched")}
    return RailResult(label, request, response=recorded["response"], provider_ref=ref(record))


def external_readonly(proposal: ActionProposal, household: Household, settings: Settings, at: datetime, outputs: Outputs = None) -> RailResult:
    if proposal.action_type == "recall:remedy":
        number = proposal.payload.get("recall_number", "").strip()
        request = cpsc_request(number)
        if not number:
            return RailResult(Label("SIMULATED", "no recall_number in payload"), request)

        def fetch_cpsc() -> Any:
            with http_client() as client:
                reply = client.get(CPSC_URL, params=request["params"])
                reply.raise_for_status()
                return reply.json()

        return _lookup(proposal, settings, outputs, source="CPSC", request=request, fixture=cpsc_fixture_path(number),
                       fetch=fetch_cpsc, unavailable=None, primary=_first_recall, summarize=summarize_recall,
                       ref=lambda record: str(record.get("URL") or f"{CPSC_URL}?format=json&RecallNumber={number}"))
    if proposal.action_type == "flight:claim":
        ident = proposal.payload.get("flight_ident", "").strip()
        request = aeroapi_request(ident)
        if not ident:
            return RailResult(Label("SIMULATED", "no flight_ident in payload"), request)
        key = os.environ.get("AEROAPI_KEY", "") if settings.aeroapi_key_present else ""

        def fetch_flight() -> Any:
            with http_client() as client:
                reply = client.get(request["url"], headers={"x-apikey": key})
                reply.raise_for_status()
                return reply.json()

        return _lookup(proposal, settings, outputs, source="AeroAPI", request=request, fixture=aeroapi_fixture_path(ident),
                       fetch=fetch_flight if key else None, unavailable="AEROAPI_KEY missing", primary=_first_flight,
                       summarize=lambda record: {k: record.get(k) for k in ("ident", "status", "scheduled_out", "actual_out", "scheduled_in", "actual_in", "cancelled")},
                       ref=lambda record: str(record.get("fa_flight_id") or request["url"]))
    return RailResult(Label("SIMULATED", f"external-api-readonly has no lookup for {proposal.action_type}"), {"action_type": proposal.action_type})
