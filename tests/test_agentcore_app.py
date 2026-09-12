"""The AgentCore Runtime contract, exercised locally in fake mode (no AWS calls)."""

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "fake")
    monkeypatch.delenv("AGENTCORE_MEMORY_ID", raising=False)
    sys.path.insert(0, str(ROOT / "agentcore"))
    import importlib

    module = importlib.import_module("app")
    from starlette.testclient import TestClient

    return TestClient(module.app)


def test_ping(client) -> None:
    assert client.get("/ping").status_code == 200


def test_invocation_streams_roster_events_for_a_fixture(client) -> None:
    with client.stream("POST", "/invocations", json={"fixture_id": "ltb-n4", "language": "es"}) as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())
    events = [json.loads(line[len("data: "):]) for line in body.splitlines() if line.startswith("data: ")]
    kinds = [e["event"] for e in events]
    assert kinds[0] == "session_start" and kinds[-1] == "result"
    assert [e["node_id"] for e in events if e["event"] == "node_start"] == ["reader", "interpreter", "critic", "router"]
    assert events[-1]["result"]["outcome"] == "escalate"
    assert events[-1]["result"]["guard"]["rule_id"] == "ON-LTB-N4"


def test_invocation_accepts_raw_document_text(client) -> None:
    text = "Riverdale Branch Library: your requested book is ready for pickup until September 30, 2026."
    with client.stream("POST", "/invocations", json={"document_text": text, "title": "Library notice", "language": "es"}) as response:
        body = "".join(response.iter_text())
    events = [json.loads(line[len("data: "):]) for line in body.splitlines() if line.startswith("data: ")]
    result = events[-1]["result"]
    assert result["fixture_id"] == "adhoc" and result["fixture_source"].startswith("visitor-supplied")
    assert result["outcome"] == "proceed"


def test_memory_is_opt_in() -> None:
    assert os.environ.get("AGENTCORE_MEMORY_ID") is None
    sys.path.insert(0, str(ROOT / "agentcore"))
    import app as module

    assert module._memory_session_manager("s1", "us-east-1") is None


def test_runtime_exhaustion_uses_same_budget_and_error(client, monkeypatch):
    monkeypatch.setenv("MAX_MODEL_CALLS", "2")
    body = client.post("/invocations", json={"fixture_id": "ltb-n4", "max_model_calls": 1000}).text
    events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
    assert events[-1]["code"] == "model_call_limit"
    assert events[-1]["model_calls"] == {"limit": 2, "attempted": 2, "remaining": 0, "exhausted": True}
    assert not any(e["event"] == "result" for e in events)
