"""The AgentCore Runtime contract, exercised locally in fake mode (no AWS calls)."""

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def events_of(body: str) -> list[dict]:
    return [json.loads(line[len("data: "):]) for line in body.splitlines() if line.startswith("data: ")]


@pytest.fixture()
def module(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "fake")
    monkeypatch.delenv("AGENTCORE_MEMORY_ID", raising=False)
    monkeypatch.delenv("EXECUTION_MODE", raising=False)
    sys.path.insert(0, str(ROOT / "agentcore"))
    import importlib

    return importlib.import_module("app")


@pytest.fixture()
def client(module):
    from starlette.testclient import TestClient

    return TestClient(module.app)


def test_ping(client) -> None:
    assert client.get("/ping").status_code == 200


def test_ws_route_is_registered(module) -> None:
    paths = {getattr(route, "path", None) for route in module.app.routes}
    assert "/ws" in paths and "/invocations" in paths


def test_metadata_operation_reports_execution_mode_rails_and_skills(client) -> None:
    body = client.post("/invocations", json={"operation": "metadata"}).text
    events = events_of(body)
    assert len(events) == 1 and events[0]["event"] == "runtime_meta"
    meta = events[0]["metadata"]
    assert meta["provider"] == "fake" and meta["memory_enabled"] is False
    assert meta["execution_mode"] == "simulated"
    assert meta["rails"] == ["external-api-readonly", "internal-ledger", "official-form", "ses-email", "stripe-test"]
    assert isinstance(meta["skills_digest"], str) and len(meta["skills_digest"]) == 64


def test_invocation_streams_household_roster_for_a_fixture(client) -> None:
    with client.stream("POST", "/invocations", json={"fixture_id": "kofi-allowance-8", "actor_member_id": "kofi", "language": "en"}) as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())
    events = events_of(body)
    kinds = [e["event"] for e in events]
    assert kinds[0] == "session_start" and kinds[-1] == "result"
    assert [e["node_id"] for e in events if e["event"] == "node_start"] == ["intake", "matcher", "planner", "authority", "executor", "briefer"]
    action = next(e for e in events if e["event"] == "action")
    assert action["decision"]["outcome"] == "allow" and action["decision"]["rule_id"] == "minor-allowance"
    assert any(e["event"] == "receipt" for e in events)
    assert events[-1]["result"]["outcome"] == "executed" and events[-1]["result"]["actor_member_id"] == "kofi"


def test_invocation_accepts_raw_request_text(client) -> None:
    with client.stream("POST", "/invocations", json={"request_text": "Can I have eight dollars from my allowance for the book fair?", "actor_member_id": "kofi", "language": "en"}) as response:
        body = "".join(response.iter_text())
    events = events_of(body)
    assert events[0]["event"] == "session_start" and events[-1]["event"] == "result"
    assert events[-1]["result"]["actor_member_id"] == "kofi"


def test_needs_approval_fixture_never_starts_the_executor(client) -> None:
    body = client.post("/invocations", json={"fixture_id": "kofi-allowance-40", "actor_member_id": "kofi", "language": "en"}).text
    events = events_of(body)
    assert "executor" not in [e.get("node_id") for e in events]
    assert any(e["event"] == "approval_needed" for e in events)
    assert events[-1]["result"]["outcome"] == "needs-approval"


def test_missing_request_returns_an_explicit_error(client) -> None:
    body = client.post("/invocations", json={}).text
    events = events_of(body)
    assert events[-1]["event"] == "error" and "needs" in events[-1]["detail"]


def test_memory_is_opt_in(module) -> None:
    assert os.environ.get("AGENTCORE_MEMORY_ID") is None
    assert module._memory_session_manager("s1", "us-east-1") is None


def test_runtime_exhaustion_uses_the_env_budget_and_payload_cannot_raise_it(client, monkeypatch) -> None:
    monkeypatch.setenv("MAX_MODEL_CALLS", "2")
    body = client.post("/invocations", json={"fixture_id": "kofi-allowance-8", "actor_member_id": "kofi", "max_model_calls": 1000}).text
    events = events_of(body)
    assert events[-1]["code"] == "model_call_limit"
    assert events[-1]["model_calls"]["limit"] == 2 and events[-1]["model_calls"]["exhausted"] is True
    assert not any(e["event"] == "result" for e in events)
