import json

from fastapi.testclient import TestClient

from household.web.app import create_app


def _events(body: str) -> list[dict]:
    return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]


def test_meta_names_the_sdk_and_the_roster(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_PROVIDER", "fake")
    client = TestClient(create_app())
    meta = client.get("/api/meta").json()
    assert "Strands Agents SDK" in meta["sdk"]
    assert [a["id"] for a in meta["roster"]] == ["reader", "interpreter", "drafter", "critic", "router"]
    assert meta["graph_mermaid"].startswith("flowchart")
    assert any(r["id"] == "ON-LTB-N4" for r in meta["rules"])


def test_index_and_fixtures(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_PROVIDER", "fake")
    client = TestClient(create_app())
    assert client.get("/").status_code == 200 and "Front Desk" in client.get("/").text
    docs = client.get("/api/fixtures").json()
    assert any(d["id"] == "ltb-n4" and d["is_real"] for d in docs)


def test_run_streams_events_with_fidelity_on_the_interpreter(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_PROVIDER", "fake")
    client = TestClient(create_app())
    with client.stream("POST", "/api/run", json={"fixture_id": "benefits-appointment-letter", "language": "es"}) as r:
        assert r.status_code == 200
        events = _events("".join(r.iter_text()))
    interp = next(e for e in events if e["event"] == "node_done" and e["node_id"] == "interpreter")
    assert interp["fidelity"]["band"] == "unreliable"
    assert events[-1]["event"] == "result" and events[-1]["result"]["guard"]["rule_id"] == "FIDELITY-FLOOR"


def test_run_rejects_empty_and_unknown_language(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_PROVIDER", "fake")
    client = TestClient(create_app())
    assert client.post("/api/run", json={"language": "es"}).status_code == 400
    assert client.post("/api/run", json={"fixture_id": "ltb-n4", "language": "xx"}).status_code == 400
