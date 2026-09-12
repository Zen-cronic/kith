"""The household web app: meta and the ledger view, PIN identification, the SSE session stream, approvals with a
PIN, declines, uploads, and the gated reset. Everything runs on the fake provider against a temporary ledger."""

import json

import pytest
from fastapi.testclient import TestClient

import household.web.app as web
from household.web.app import SessionSigner, create_app

PINS = {"ama": "2468", "daniel": "1357", "kofi": "1111", "mei": "2222"}
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("MODEL_PROVIDER", "fake")
    monkeypatch.delenv("EXECUTION_MODE", raising=False)
    monkeypatch.delenv("HOUSEHOLD_ALLOW_RESET", raising=False)
    monkeypatch.setenv("HOUSEHOLD_DATA_DIR", str(tmp_path / "data"))
    return TestClient(create_app())


def identify(client: TestClient, member_id: str) -> str:
    response = client.post("/api/identify", json={"member_id": member_id, "pin": PINS[member_id]})
    assert response.status_code == 200, response.text
    return response.json()["session_token"]


def events(client: TestClient, body: dict) -> list[dict]:
    with client.stream("POST", "/api/run", json=body) as response:
        assert response.status_code == 200, response.read()
        text = "".join(response.iter_text())
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def run_fixture(client: TestClient, member_id: str, fixture_id: str) -> list[dict]:
    return events(client, {"fixture_id": fixture_id, "actor_member_id": member_id, "session_token": identify(client, member_id)})


# Meta, index, ledger


def test_meta_names_the_sdk_roster_skills_and_rails(client: TestClient) -> None:
    meta = client.get("/api/meta").json()
    assert "Strands Agents SDK" in meta["sdk"] and meta["backend"] == "local" and meta["provider"] == "fake"
    assert [a["id"] for a in meta["roster"]] == ["intake", "matcher", "planner", "authority", "executor", "briefer"]
    assert [s["id"] for s in meta["skills"]] == ["allowance", "benefits", "education", "recall", "household"]
    assert meta["execution_mode"] == "simulated" and meta["reset_enabled"] is False
    rails = meta["rails"]
    assert rails["intro"].startswith("Every action ends in a receipt") and rails["execution_mode"] == "simulated"
    assert {r["id"]: r["now"]["mode"] for r in rails["items"]} == {
        "ses-email": "SIMULATED", "internal-ledger": "SIMULATED", "stripe-test": "SIMULATED",
        "official-form": "PREPARE-ONLY", "external-api-readonly": "SIMULATED-replay",
    }
    assert all(r["name"] and r["real"] and r["reasons"] and r["now"]["reason"] for r in rails["items"])
    assert meta["upload"]["kinds"] == ["gif", "jpeg", "pdf", "png", "webp"] and isinstance(meta["upload"]["available"], bool)


def test_index_and_static_screens(client: TestClient) -> None:
    page = client.get("/")
    assert page.status_code == 200 and "Kith" in page.text and "What is real" in page.text
    assert client.get("/static/app.js").status_code == 200 and client.get("/static/queue.js").status_code == 200


def test_household_view_hides_pins_and_states_grants(client: TestClient) -> None:
    response = client.get("/api/household")
    household = response.json()
    assert "pin_hash" not in response.text and "pin_salt" not in response.text and '"proof"' not in response.text
    assert [(m["id"], m["role"], m["has_pin"]) for m in household["members"]] == [
        ("ama", "adult", True), ("daniel", "adult", True), ("kofi", "minor", True), ("mei", "minor", True)]
    assert {g["id"]: g["state"] for g in household["grants"]} == {
        "g-daniel-ama-benefits": "active", "g-daniel-ama-payments": "active", "g-ama-agent-recall": "active",
        "g-expired": "expired", "g-revoked": "revoked"}
    assert [c["kind"] for c in household["consents"]] == ["grant-accept", "grant-accept"]
    assert household["actions"] == [] and household["receipts"] == []
    assert household["demo_pins"] == PINS  # each hint verified against the seed hash before it is shown


def test_fixtures_are_request_fixtures_with_their_actor(client: TestClient) -> None:
    fixtures = client.get("/api/fixtures").json()
    by_id = {f["id"]: f for f in fixtures}
    assert by_id["kofi-allowance-40"]["actor_member_id"] == "kofi" and by_id["kofi-allowance-40"]["actor_name"] == "Kofi Okafor-Lim"
    assert by_id["kofi-allowance-40"]["expected"]["approval_by"] == ["ama", "daniel"]
    assert "text" not in by_id["ama-dental-cob"] or by_id["ama-dental-cob"]["request"]


# Identification


def test_identify_requires_a_matching_pin(client: TestClient) -> None:
    assert client.post("/api/identify", json={"member_id": "ama", "pin": "0000"}).status_code == 403
    assert client.post("/api/identify", json={"member_id": "nobody", "pin": "0000"}).status_code == 404
    data = client.post("/api/identify", json={"member_id": "ama", "pin": PINS["ama"]}).json()
    assert data["member"]["id"] == "ama" and data["member"]["role"] == "adult" and "pin_hash" not in json.dumps(data["member"]) and "pin_salt" not in json.dumps(data["member"])
    assert data["session_token"].startswith("ama.") and data["expires_at"].endswith("+00:00")


def test_session_tokens_are_signed_bound_and_expire() -> None:
    signer = SessionSigner()
    token, _ = signer.issue("ama", now=1_000)
    assert signer.member_of(token, now=1_001) == "ama"
    assert signer.member_of(token, now=1_000 + web.SESSION_TTL_SECONDS) is None
    assert signer.member_of(token.replace("ama.", "kofi."), now=1_001) is None
    assert signer.member_of(token[:-1] + ("0" if token[-1] != "0" else "1"), now=1_001) is None
    assert signer.member_of("", now=1_001) is None and signer.member_of("a.b.c", now=1_001) is None
    assert SessionSigner().member_of(token, now=1_001) is None  # a different process secret


def test_run_requires_the_actor_s_own_session(client: TestClient) -> None:
    token = identify(client, "ama")
    assert client.post("/api/run", json={"fixture_id": "kofi-allowance-8", "actor_member_id": "kofi", "session_token": token}).status_code == 401
    assert client.post("/api/run", json={"fixture_id": "kofi-allowance-8", "actor_member_id": "ama", "session_token": "ama.1.bad"}).status_code == 401
    assert client.post("/api/run", json={"actor_member_id": "ama", "session_token": token}).status_code == 400
    assert client.post("/api/run", json={"fixture_id": "x", "request_text": "y", "actor_member_id": "ama", "session_token": token}).status_code == 400
    assert client.post("/api/run", json={"fixture_id": "no-such-fixture", "actor_member_id": "ama", "session_token": token}).status_code == 404


# The session stream


def test_in_scope_run_streams_the_six_nodes_action_and_receipt(client: TestClient) -> None:
    stream = run_fixture(client, "kofi", "kofi-allowance-8")
    kinds = [e["event"] for e in stream]
    assert kinds[0] == "session_start" and kinds[-1] == "result"
    assert {"node_start", "node_done", "action", "receipt"} <= set(kinds) and "approval_needed" not in kinds
    assert [e["node_id"] for e in stream if e["event"] == "node_start"] == ["intake", "matcher", "planner", "authority", "executor", "briefer"]
    start = stream[0]
    assert start["actor_member_id"] == "kofi" and start["request_id"] == "kofi-allowance-8" and len(start["roster"]) == 6
    action = next(e for e in stream if e["event"] == "action")
    assert action["decision"]["outcome"] == "allow" and action["explanation"].startswith("Allowed under rule:minor-allowance")
    receipt = next(e for e in stream if e["event"] == "receipt")["receipt"]
    assert receipt["mode"] == "SIMULATED" and receipt["action_id"] == action["proposal"]["id"]
    result = stream[-1]["result"]
    assert result["outcome"] == "executed" and result["guard"]["overrides"] == 0 and result["briefing"]["headline_target"]
    listed = client.get("/api/receipts").json()
    assert [(r["id"], r["mode"], r["action_type"], r["subject_member_id"]) for r in listed] == [(receipt["id"], "SIMULATED", "allowance:transfer", "kofi")]
    assert listed[0]["provider_ref"] is None or len(listed[0]["provider_ref"]) <= 9
    household = client.get("/api/household").json()
    assert [(a["id"], a["status"]) for a in household["actions"]] == [(action["proposal"]["id"], "executed")]


def test_typed_text_matching_a_fixture_runs_as_that_fixture_and_other_text_is_adhoc(client: TestClient) -> None:
    token = identify(client, "kofi")
    request = next(f for f in client.get("/api/fixtures").json() if f["id"] == "kofi-allowance-8")["request"]
    stream = events(client, {"request_text": "  " + request.replace("\n", " ") + " ", "actor_member_id": "kofi", "session_token": token})
    assert stream[0]["request_id"] == "kofi-allowance-8" and stream[-1]["result"]["outcome"] == "executed"
    adhoc = events(client, {"request_text": "Please water the plants on Saturday.", "actor_member_id": "kofi", "session_token": token})
    assert adhoc[0]["request_id"] == "adhoc" and adhoc[-1]["result"]["outcome"] == "no-action"
    assert not any(e["event"] in {"action", "receipt", "approval_needed"} for e in adhoc)


# Approvals with a PIN


def test_minor_over_limit_queues_an_approval_that_only_a_guardian_pin_can_release(client: TestClient) -> None:
    stream = run_fixture(client, "kofi", "kofi-allowance-40")
    assert "receipt" not in [e["event"] for e in stream] and stream[-1]["result"]["outcome"] == "needs-approval"
    approval = next(e for e in stream if e["event"] == "approval_needed")
    action_id = approval["action_id"]
    assert set(approval["approver_ids"]) == {"ama", "daniel"} and "above" in approval["explanation"]
    queued = client.get("/api/household").json()["actions"]
    assert [(a["id"], a["status"]) for a in queued] == [(action_id, "needs-approval")]
    assert client.post(f"/api/actions/{action_id}/approve", json={"approver_member_id": "ama", "pin": "0000"}).status_code == 403
    assert client.post(f"/api/actions/{action_id}/approve", json={"approver_member_id": "kofi", "pin": PINS["kofi"]}).status_code == 403
    assert client.post(f"/api/actions/{action_id}/approve", json={"approver_member_id": "mei", "pin": PINS["mei"]}).status_code == 403
    assert client.post(f"/api/actions/{action_id}/approve", json={"approver_member_id": "ghost", "pin": "1"}).status_code == 404
    assert client.post("/api/actions/act-nope/approve", json={"approver_member_id": "ama", "pin": PINS["ama"]}).status_code == 404
    assert client.get("/api/receipts").json() == []
    approved = client.post(f"/api/actions/{action_id}/approve", json={"approver_member_id": "ama", "pin": PINS["ama"]})
    assert approved.status_code == 200, approved.text
    data = approved.json()
    assert data["decision"]["outcome"] == "allow" and data["decision"]["grant_id"] == "approval:ama"
    assert data["consent"]["kind"] == "action-approve" and data["consent"]["member_id"] == "ama" and "proof" not in data["consent"]
    assert data["receipt"]["mode"] == "SIMULATED" and data["receipt"]["action_id"] == action_id
    assert data["action"]["status"] == "executed" and data["explanation"].startswith("Allowed under approval:ama")
    household = client.get("/api/household").json()
    assert [c["kind"] for c in household["consents"]][-1] == "action-approve"
    assert household["actions"][0]["status"] == "executed" and household["receipts"][0]["id"] == data["receipt"]["id"]
    assert client.post(f"/api/actions/{action_id}/approve", json={"approver_member_id": "ama", "pin": PINS["ama"]}).status_code == 409


def test_decline_records_consent_and_closes_the_action(client: TestClient) -> None:
    stream = run_fixture(client, "mei", "mei-email-teacher")
    action_id = next(e for e in stream if e["event"] == "approval_needed")["action_id"]
    assert client.post(f"/api/actions/{action_id}/decline", json={"approver_member_id": "daniel", "pin": "0000"}).status_code == 403
    assert client.post(f"/api/actions/{action_id}/decline", json={"approver_member_id": "mei", "pin": PINS["mei"]}).status_code == 403
    declined = client.post(f"/api/actions/{action_id}/decline", json={"approver_member_id": "daniel", "pin": PINS["daniel"]})
    assert declined.status_code == 200, declined.text
    data = declined.json()
    assert data["decision"]["outcome"] == "block" and data["decision"]["grant_id"] == "declined:daniel" and data["receipt"] is None
    assert data["consent"]["kind"] == "action-decline" and data["action"]["status"] == "declined"
    assert data["explanation"].startswith("Blocked:") and "declined by Daniel Lim" in data["explanation"]
    household = client.get("/api/household").json()
    assert household["actions"][0]["status"] == "declined" and household["receipts"] == []
    assert household["consents"][-1]["kind"] == "action-decline"
    assert client.post(f"/api/actions/{action_id}/approve", json={"approver_member_id": "ama", "pin": PINS["ama"]}).status_code == 409
    assert client.post(f"/api/actions/{action_id}/decline", json={"approver_member_id": "ama", "pin": PINS["ama"]}).status_code == 409


def test_revise_loop_streams_two_revisions_and_a_partial_outcome(client: TestClient) -> None:
    stream = run_fixture(client, "ama", "daniel-payment-450")
    revisions = sorted({e["revision"] for e in stream if e["event"] == "action"})
    assert revisions == [1, 2] and stream[-1]["result"]["outcome"] == "partial"
    approval = next(e for e in stream if e["event"] == "approval_needed")
    assert approval["approver_ids"] == ["daniel"] and "exceeded" in " ".join(approval["reasons"])


# Uploads


def test_intake_stores_an_upload_for_the_session_only(client: TestClient, monkeypatch) -> None:
    token = identify(client, "ama")
    assert client.post("/api/intake", files={"file": ("x.png", PNG, "image/png")}, data={"session_token": "ama.1.bad"}).status_code == 401
    assert client.post("/api/intake", files={"file": ("x.txt", b"hello", "text/plain")}, data={"session_token": token}).status_code == 415
    monkeypatch.setattr(web, "UPLOAD_MAX_BYTES", 32)
    assert client.post("/api/intake", files={"file": ("x.png", PNG, "image/png")}, data={"session_token": token}).status_code == 413
    monkeypatch.setattr(web, "UPLOAD_MAX_BYTES", 5 * 1024 * 1024)
    uploaded = client.post("/api/intake", files={"file": ("statement.png", PNG, "image/png")}, data={"session_token": token})
    assert uploaded.status_code == 200, uploaded.text
    data = uploaded.json()
    assert data["kind"] == "png" and data["filename"] == "statement.png" and data["size"] == len(PNG) and data["upload_id"].isalnum()
    pdf = client.post("/api/intake", files={"file": ("letter.pdf", b"%PDF-1.4 minimal", "application/pdf")}, data={"session_token": token}).json()
    assert pdf["kind"] == "pdf"
    other = identify(client, "daniel")
    assert client.post("/api/run", json={"upload_id": data["upload_id"], "actor_member_id": "daniel", "session_token": other}).status_code in {404, 501}


def test_run_with_an_upload_passes_the_path_only_when_the_pipeline_accepts_it(client: TestClient, monkeypatch) -> None:
    token = identify(client, "ama")
    upload_id = client.post("/api/intake", files={"file": ("statement.png", PNG, "image/png")}, data={"session_token": token}).json()["upload_id"]
    monkeypatch.setattr(web, "UPLOAD_SUPPORTED", False)
    assert client.post("/api/run", json={"upload_id": upload_id, "actor_member_id": "ama", "session_token": token}).status_code == 501
    seen: dict = {}

    class Result:
        def model_dump(self, mode="json", exclude_none=True):
            return {"outcome": "no-action"}

    async def fake_stream(fixture, actor_id, **kwargs):
        seen.update(kwargs, fixture=fixture, actor=actor_id)
        yield {"event": "session_start", "request_id": fixture.id}
        yield {"event": "result", "result": Result()}

    monkeypatch.setattr(web, "UPLOAD_SUPPORTED", True)
    monkeypatch.setattr(web, "stream_session", fake_stream)
    stream = events(client, {"upload_id": upload_id, "actor_member_id": "ama", "session_token": token})
    assert [e["event"] for e in stream] == ["session_start", "result"]
    assert seen["upload"].name.startswith(upload_id) and seen["upload"].suffix == ".png" and seen["actor"] == "ama"
    assert seen["fixture"].id == "adhoc" and seen["upload"].read_bytes() == PNG
    assert client.post("/api/run", json={"upload_id": "nope", "actor_member_id": "ama", "session_token": token}).status_code == 404


# Reset


def test_reset_is_gated_and_reseeds_the_ledger(client: TestClient, monkeypatch) -> None:
    run_fixture(client, "kofi", "kofi-allowance-8")
    assert client.get("/api/household").json()["actions"]
    assert client.post("/api/reset").status_code == 403
    monkeypatch.setenv("HOUSEHOLD_ALLOW_RESET", "1")
    assert client.get("/api/meta").json()["reset_enabled"] is True
    assert client.post("/api/reset").json()["reset"] is True
    household = client.get("/api/household").json()
    assert household["actions"] == [] and household["receipts"] == [] and len(household["consents"]) == 2


# Voice router mounted on the web app (P7 wired by the coordinator)


def test_voice_router_is_mounted_and_refuses_a_wrong_pin(client) -> None:
    meta = client.get("/api/voice/meta")
    assert meta.status_code == 200 and "tools" in meta.json()
    with client.websocket_connect("/api/voice") as ws:
        ws.send_json({"type": "identify", "member_id": "kofi", "pin": "0000"})
        frame = ws.receive_json()
        assert frame["type"] == "identify_failed" and frame["attempts_left"] == 2
