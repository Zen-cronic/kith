"""The product-recall skill: verbatim quotes from the recorded CPSC record, a fixture-only search, a claim email that
quotes the remedy word for word, and the route offline under EXECUTION_MODE=live (replayed record, simulated email)."""

import dataclasses
import json
from datetime import UTC, datetime

import httpx
import pytest

from household import config
from household.config import load_settings
from household.executor import rails
from household.executor.receipts import REPLAY_LABEL, SES_NO_FROM_LABEL, SIMULATED_LABEL
from household.fixtures import FixtureStore
from household.pipeline import run_session
from household.skills import SKILLS, match_skill, skill_for
from household.skills.base import ToolContext
from household.skills.recall import RECALL, rules
from household.skills.recall.rules import (
    RECALL_26639_URL,
    RULES,
    claim_body,
    contact_email,
    find_recall,
    remedy_text,
)

NOW = datetime(2026, 9, 11, 16, 0, tzinfo=UTC)
FIXTURE = rules.RECALL_FIXTURES / "cpsc-26639.json"


def demo():
    return FixtureStore().household("demo")


def record():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["response"][0]


@pytest.fixture
def live(monkeypatch):
    monkeypatch.delenv("SES_FROM", raising=False)
    monkeypatch.setattr(config, "ses_verified_identities", lambda region: pytest.fail("live mode must stay offline without SES_FROM"))

    def down(request):
        raise httpx.ConnectError("no route to host", request=request)

    monkeypatch.setattr(rails, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(down)))
    return load_settings(provider="fake", execution_mode="live")


def test_contract_is_frozen_and_last_in_the_order():
    assert SKILLS[3] is RECALL and skill_for("recall") is RECALL
    with pytest.raises(dataclasses.FrozenInstanceError):
        RECALL.name = "x"
    assert RECALL.action_types == ("recall:remedy", "email:send")
    remedy, email = RECALL.template("recall:remedy"), RECALL.template("email:send")
    assert (remedy.rail, remedy.payload_fields) == ("external-api-readonly", ("recall_number", "product")) and "read-only" in remedy.description
    assert (email.rail, email.payload_fields) == ("ses-email", ("subject", "body")) and "verbatim" in email.description
    assert [t.name for t in RECALL.tools] == ["lookup_recall", "propose_recall_claim"]
    assert RECALL.matcher_hints == ("recall", "recalled", "CPSC", "safety notice", "product safety")
    assert match_skill("our baby bibs were recalled by the CPSC").id == "recall"
    assert match_skill("a recall notice about the dental claim").id == "benefits"  # tie-break: the earlier skill wins
    assert RECALL.form_builders == {} and "lookup_recall" in RECALL.prompt() and "word for word" in RECALL.prompt()
    assert set(RECALL.evidence_schema.model_fields) == {"member_id", "product", "recall_number_text", "purchase_text"}


def test_rules_quote_the_recorded_record_verbatim():
    rec = record()
    quotes = {c.label: c for c in RULES.citations}
    assert quotes["CPSC recall 26639, remedy"].quote == rec["Remedies"][0]["Name"] == remedy_text(rec)
    assert quotes["CPSC recall 26639, consumer contact"].quote == rec["ConsumerContact"]
    assert quotes["CPSC recall 26639, hazard"].quote == rec["Hazards"][0]["Name"]
    assert all(c.url == rec["URL"] == RECALL_26639_URL for c in RULES.citations)
    assert contact_email(rec) == "peonydesigncoshoppe@gmail.com" and contact_email({"ConsumerContact": "call 800-555-0100"}) is None
    assert RULES.params["lookup"].startswith("recall:remedy is read-only") and "never paraphrased" in RULES.params["remedy"]


def test_find_recall_by_number_or_by_title_words_from_the_fixture_dir_only():
    for query in ("26639", "#26639", " 26639 ", "Peony Design bibs", "stroller bags peony", "personalized baby bibs choking"):
        found = find_recall(query)
        assert found is not None and found["record"]["RecallNumber"] == "26639", query
        assert found["fetched"] == "2026-09-12" and found["recorded"] and found["path"] == "fixtures/skills/recall/cpsc-26639.json"
    for query in ("", "99999", "toaster oven fire", "a", "recalled lawn mower blade"):
        assert find_recall(query) is None, query
    assert find_recall("26639", directory=FIXTURE.parent.parent) is None  # no cpsc-*.json there


def test_claim_body_quotes_the_remedy_and_the_url_word_for_word():
    rec = record()
    body = claim_body(rec, "Peony Design personalized baby bibs", "Ama Okafor-Lim", "bought on Etsy in December 2025")
    assert f'"{rec["Remedies"][0]["Name"]}"' in body and rec["URL"] in body and rec["Title"] in body
    assert body.startswith("Hello Peony Design Co.,") and body.endswith("Thank you,\nAma Okafor-Lim")
    assert "We own Peony Design personalized baby bibs, bought on Etsy in December 2025, and" in body
    assert "bought" not in claim_body(rec, "bibs", "Ama") and rules.claim_subject(rec, "bibs") == "Recall 26639: refund request for bibs"


def test_tools_search_the_recorded_records_and_propose_but_never_send():
    household = demo()
    context = ToolContext(household=household, actor=household.member("ama"), now=NOW)
    tools = RECALL.build_tools(context)
    found = json.loads(tools["lookup_recall"](query="Peony bibs"))
    assert found["recall_number"] == "26639" and found["remedy"] == remedy_text(record()) and found["contact_email"] == "peonydesigncoshoppe@gmail.com"
    assert found["remedy_options"] == ["Refund"] and found["fetched"] == "2026-09-12" and found["source"] == "recorded CPSC response"
    assert found["url"] == RECALL_26639_URL and found["units"] == ["About 52"] and found["recall_date"] == "2026-07-23"
    missing = json.loads(tools["lookup_recall"](query="toaster"))
    assert "error" in missing and missing["recorded_recall_numbers"] == ["26639"]
    plan = json.loads(tools["propose_recall_claim"](member_id="ama", recall_number="26639", product="the bibs", purchase_text="on Etsy"))
    lookup, claim = plan["actions"]
    assert plan["needs"] == []
    assert (lookup["action_type"], lookup["rail"], lookup["subject_member_id"], lookup["recipient"], lookup["amount_text"]) == (
        "recall:remedy", "external-api-readonly", "ama", "", "")
    assert lookup["payload"] == [{"key": "recall_number", "value": "26639"}, {"key": "product", "value": "the bibs"}]
    assert (claim["action_type"], claim["rail"], claim["recipient"], claim["amount_text"], claim["claimed_grant_id"]) == (
        "email:send", "ses-email", "peonydesigncoshoppe@gmail.com", "", "")
    payload = {f["key"]: f["value"] for f in claim["payload"]}
    assert payload["subject"] == "Recall 26639: refund request for the bibs"
    assert payload["body"] == claim_body(record(), "the bibs", "Ama Okafor-Lim", "on Etsy")
    assert "error" in json.loads(tools["propose_recall_claim"](member_id="zed", recall_number="26639", product="x"))
    assert "error" in json.loads(tools["propose_recall_claim"](member_id="ama", recall_number="1", product="x"))
    assert household == demo()
    assert [c["tool"] for c in context.log] == ["lookup_recall", "lookup_recall", "propose_recall_claim", "propose_recall_claim", "propose_recall_claim"]


def test_claim_falls_back_to_the_members_own_email_when_the_record_has_no_contact(monkeypatch):
    stripped = record()
    stripped["ConsumerContact"] = "Peony Design toll-free at 800-555-0100."
    entry = {"record": stripped, "fetched": "2026-09-12", "recorded": True, "path": "x"}
    monkeypatch.setattr(rules, "recorded_recalls", lambda directory=None: [entry])
    household = demo()
    tools = RECALL.build_tools(ToolContext(household=household, actor=household.member("ama"), now=NOW))
    plan = json.loads(tools["propose_recall_claim"](member_id="ama", recall_number="26639", product="bibs"))
    assert plan["actions"][1]["recipient"] == "ama@example.test" and plan["needs"][0].startswith("manufacturer contact email not in record")
    plan = json.loads(tools["propose_recall_claim"](member_id="daniel", recall_number="26639", product="bibs"))
    assert plan["actions"][1]["recipient"] == "" and len(plan["needs"]) == 2 and "no email on file" in plan["needs"][1]


def test_canned_plan_carries_the_tools_own_claim_email():
    canned = FixtureStore().canned("ama-recall-bibs", "planner")
    body = {f["key"]: f["value"] for f in canned["actions"][1]["payload"]}["body"]
    assert body == claim_body(record(), "Peony Design personalized baby bibs", "Ama Okafor-Lim", "bought on Etsy in December 2025")
    assert canned["actions"][1]["recipient"] == "peonydesigncoshoppe@gmail.com" and canned["actions"][0]["payload"][0]["value"] == "26639"


def test_route_replays_the_recorded_record_offline_and_prepares_the_claim_email(live):
    household = demo()
    r = run_session("ama-recall-bibs", settings=live, household=household, now=NOW)
    assert r.skill_id == "recall" and r.outcome == "executed" and r.approvals_needed == []
    assert [(d.outcome, d.rule_id, d.grant_id) for d in r.plans[-1].decisions] == [("allow", "self", "rule:self")] * 2
    assert [(x.rail, x.mode, x.label_reason) for x in r.receipts] == [
        ("external-api-readonly", "SIMULATED-replay", REPLAY_LABEL.format(source="CPSC", fetched="2026-09-12")),
        ("ses-email", "SIMULATED", SES_NO_FROM_LABEL),
    ]
    assert r.receipts[0].provider_ref == RECALL_26639_URL and r.receipts[0].response_digest and r.receipts[1].response_digest == ""
    lookup, claim = r.plans[-1].proposals
    assert lookup.payload == {"recall_number": "26639", "product": "Peony Design personalized baby bibs"} and lookup.amount is None
    assert claim.recipient == "peonydesigncoshoppe@gmail.com" and claim.amount is None
    assert remedy_text(record()) in claim.payload["body"] and RECALL_26639_URL in claim.payload["body"]
    assert household.ledger == [] and [a.balance for a in household.accounts] == [a.balance for a in demo().accounts]
    assert r.guard.override is False and r.briefing is not None and r.briefing.labels == ["SIMULATED", "SIMULATED-replay"]


def test_route_in_simulated_mode_still_replays_the_record_and_sends_nothing(monkeypatch):
    monkeypatch.setattr(rails, "http_client", lambda: pytest.fail("simulated mode never opens an HTTP client"))
    r = run_session("ama-recall-bibs", settings=load_settings(provider="fake"), household=demo(), now=NOW)
    assert [(x.mode, x.label_reason) for x in r.receipts] == [
        ("SIMULATED-replay", REPLAY_LABEL.format(source="CPSC", fetched="2026-09-12")), ("SIMULATED", SIMULATED_LABEL)]
