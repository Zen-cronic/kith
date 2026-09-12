"""Remote consent, actual Runtime ASGI streams and offline IAM SDK contract checks."""
import asyncio
import importlib
import io
import json
import sys
from pathlib import Path

import boto3
import httpx
import pytest
from botocore.response import StreamingBody
from botocore.stub import ANY, Stubber
from fastapi.testclient import TestClient

from household.config import Settings
from household.runtime_protocol import runtime_metadata
from household.web import runtime
from household.web.app import create_app


def run(coro):
    return asyncio.run(coro)


async def collect(stream):
    return [event async for event in stream]


async def chunks(*values):
    for value in values:
        yield value


def frame(event):
    return ("data: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode()


def events(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


@pytest.fixture
def bridge(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "fake")
    monkeypatch.setenv("SESSION_BACKEND", "runtime-http")
    monkeypatch.setenv("RUNTIME_HTTP_URL", "http://runtime.test")
    monkeypatch.delenv("AGENTCORE_MEMORY_ID", raising=False)
    monkeypatch.setenv("MAX_MODEL_CALLS", "20")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agentcore"))
    module = importlib.import_module("app")
    original = httpx.AsyncClient
    monkeypatch.setattr(runtime.httpx, "AsyncClient", lambda **kw: original(transport=httpx.ASGITransport(app=module.app), **kw))
    return TestClient(create_app()), module


def test_sse_fragmentation_multiline_comments_and_crlf():
    wire = ': hello\r\ndata: {"event":\r\ndata: "á"}\r\n\r\n'.encode()
    assert run(collect(runtime.decode_sse(chunks(*(bytes([b]) for b in wire))))) == [{"event": "á"}]


@pytest.mark.parametrize("wire", [b'data: {}\n\n', b'data: []\n\n', b'data: oops\n\n', b'data: {"event":"result"}\n', b'data: \xff\n\n', b'x' * (runtime.MAX_EVENT_BYTES + 1)])
def test_sse_rejects_invalid_unterminated_or_oversized_frames(wire):
    with pytest.raises(runtime.RuntimeFailure):
        run(collect(runtime.decode_sse(chunks(wire))))


def test_actual_runtime_metadata_and_refusal(bridge):
    client, _ = bridge
    meta = client.get("/api/meta").json()
    assert meta["backend"] == "runtime-http" and meta["provider"] == "fake"
    assert meta["max_model_calls"] == 20 and len(meta["consent_token"]) == 64
    assert meta["providers"] == {} and "runtime.test" not in json.dumps(meta)
    output = events(client.post("/api/run", json={"fixture_id": "ltb-n4", "language": "es", "consent_token": meta["consent_token"]}))
    assert output[0]["event"] == "session_start"
    assert output[-1]["result"]["guard"]["rule_id"] == "ON-LTB-N4"
    assert "drafter" not in output[-1]["result"]["execution_order"]


@pytest.mark.parametrize("run_input", [
    {"fixture_id": "school-trip-letter"},
    {"document_text": "Community picnic on Saturday. Bring a snack.", "title": "Picnic"},
])
def test_actual_runtime_assistance_preserves_nullable_result_fields(bridge, run_input):
    client, _ = bridge
    meta = client.get("/api/meta").json()
    output = events(client.post("/api/run", json={**run_input, "language": "es", "consent_token": meta["consent_token"]}))
    assert output[-1]["event"] == "result", output[-1]
    assert output[-1]["result"]["outcome"] == "proceed"
    assert output[-1]["result"]["guard"].get("rule_id") is None


def test_budget_and_config_change_before_document_dispatch(bridge, monkeypatch):
    client, module = bridge
    old = client.get("/api/meta").json()["consent_token"]
    monkeypatch.setenv("MAX_MODEL_CALLS", "3")
    current = client.get("/api/meta").json()["consent_token"]
    assert current != old
    request = {"fixture_id": "school-trip-letter", "language": "es"}
    denied = events(client.post("/api/run", json={**request, "consent_token": old}))
    assert [e["event"] for e in denied] == ["error"]
    assert denied[0]["code"] == "runtime_configuration"
    limited = events(client.post("/api/run", json={**request, "consent_token": current, "max_model_calls": 1000}))
    assert limited[-1]["code"] == "model_call_limit" and not any(e["event"] == "result" for e in limited)
    assert limited[-1]["model_calls"]["attempted"] == 3
    # A change after the bridge's metadata lookup is independently stopped inside Runtime.
    def forbidden(*args, **kwargs):
        raise AssertionError("No graph or Memory work is allowed after drift")
    monkeypatch.setattr(module, "stream_session", forbidden)
    monkeypatch.setattr(module, "_memory_session_manager", forbidden)
    bad = run(collect(module.invoke({**request, "expected_config": "stale"})))
    assert bad[0]["code"] == "runtime_configuration"


def test_memory_is_rejected_and_never_attached(bridge, monkeypatch):
    client, module = bridge
    monkeypatch.setenv("AGENTCORE_MEMORY_ID", "test-memory-not-a-real-id")
    def forbidden(*args, **kwargs):
        raise AssertionError("Memory must never attach")
    monkeypatch.setattr(module, "_memory_session_manager", forbidden)
    response = client.get("/api/meta")
    assert response.status_code == 503 and "disable Runtime Memory" in response.text
    meta = run(collect(module.invoke({"operation": "metadata"})))[0]["metadata"]
    assert meta["memory_enabled"] is True
    denied = run(collect(module.invoke({"fixture_id": "ltb-n4", "expected_config": meta["config_token"]})))
    assert denied[0]["code"] == "runtime_configuration"


def test_no_consent_or_changed_target_never_runs_local_graph(bridge, monkeypatch):
    client, _ = bridge
    response = client.post("/api/run", json={"fixture_id": "ltb-n4"})
    assert events(response)[0]["code"] == "runtime_configuration"
    token = client.get("/api/meta").json()["consent_token"]
    monkeypatch.setenv("SESSION_BACKEND", "local")
    assert events(client.post("/api/run", json={"fixture_id": "ltb-n4", "consent_token": token}))[0]["code"] == "runtime_configuration"
    monkeypatch.setenv("SESSION_BACKEND", "runtime-http")
    monkeypatch.setenv("RUNTIME_HTTP_URL", "ftp://wrong")
    assert client.get("/api/meta").status_code == 503
    assert events(client.post("/api/run", json={"fixture_id": "ltb-n4", "consent_token": token}))[0]["event"] == "error"


@pytest.mark.parametrize("wire", [[{"event": "session_start", "fixture_id": "wrong"}], [], [{"event": "runtime_meta"}], [{"event": "node_start", "node_id": "unknown"}]])
def test_remote_session_rejects_incomplete_wrong_or_unexpected_events(monkeypatch, wire):
    async def fake(*args):
        for event in wire:
            yield event
    monkeypatch.setattr(runtime, "invoke_events", fake)
    with pytest.raises(runtime.RuntimeFailure):
        run(collect(runtime.remote_session(runtime.RuntimeTarget("runtime-http"), {"fixture_id": "ltb-n4", "language": "es"}, runtime_metadata(Settings()))))


def test_terminal_result_not_published_until_clean_end(bridge, monkeypatch):
    client, _ = bridge
    meta = client.get("/api/meta").json()
    output = events(client.post("/api/run", json={"fixture_id": "ltb-n4", "consent_token": meta["consent_token"]}))
    async def malformed(*args):
        for event in output:
            yield event
        yield {"event": "node_start", "node_id": "reader"}
    monkeypatch.setattr(runtime, "invoke_events", malformed)
    seen = []
    async def check():
        async for event in runtime.remote_session(runtime.RuntimeTarget("runtime-http"), {"fixture_id": "ltb-n4", "language": "es"}, runtime_metadata(Settings())):
            seen.append(event)
    with pytest.raises(runtime.RuntimeFailure):
        run(check())
    assert not any(e["event"] == "result" for e in seen)


def test_aws_sdk_request_stream_cleanup_and_distinct_session_ids(monkeypatch):
    target = runtime.RuntimeTarget("agentcore", "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/household-test")
    client = boto3.client("bedrock-agentcore", region_name="us-east-1", aws_access_key_id="test", aws_secret_access_key="test")
    monkeypatch.setattr(runtime, "aws_client", lambda _: client)
    raw = frame({"event": "runtime_meta", "metadata": {"language": "español"}})
    sessions = []
    original = client.invoke_agent_runtime
    def recording(**kw):
        sessions.append(kw["runtimeSessionId"])
        return original(**kw)
    monkeypatch.setattr(client, "invoke_agent_runtime", recording)
    bodies = []
    with Stubber(client) as stub:
        for _ in range(2):
            stream = io.BytesIO(raw)
            bodies.append(stream)
            stub.add_response("invoke_agent_runtime", {"statusCode": 200, "contentType": "text/event-stream", "response": StreamingBody(stream, len(raw))},
                              {"agentRuntimeArn": target.endpoint, "qualifier": "DEFAULT", "runtimeSessionId": ANY,
                               "contentType": "application/json", "accept": "text/event-stream", "payload": b'{"operation": "metadata"}'})
            assert run(collect(runtime.invoke_events(target, {"operation": "metadata"})))[0]["metadata"]["language"] == "español"
        stub.assert_no_pending_responses()
    assert all(body.closed for body in bodies)
    assert len(set(sessions)) == 2 and all(len(s) == 36 for s in sessions)


def test_aws_retry_configuration(monkeypatch):
    captured = {}
    def client(*args, **kw):
        captured.update(kw)
    monkeypatch.setattr(runtime.boto3, "client", client)
    runtime.aws_client(runtime.RuntimeTarget("agentcore"))
    assert captured["config"].retries["total_max_attempts"] == 1


def test_aws_body_closed_when_consumer_stops(monkeypatch):
    raw = frame({"event": "session_start"}) + frame({"event": "result"})
    stream = io.BytesIO(raw)
    class Client:
        def invoke_agent_runtime(self, **kwargs):
            return {"statusCode": 200, "contentType": "text/event-stream", "response": StreamingBody(stream, len(raw))}
        def close(self):
            pass
    monkeypatch.setattr(runtime, "aws_client", lambda _: Client())
    async def check():
        iterator = runtime.invoke_events(runtime.RuntimeTarget("agentcore"), {})
        assert (await anext(iterator))["event"] == "session_start"
        await iterator.aclose()
    run(check())
    assert stream.closed


def test_http_failure_is_sanitized_and_stream_is_closed(monkeypatch):
    original = httpx.AsyncClient
    closed = []
    class FailingStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield frame({"event": "session_start"})
            raise httpx.ReadError("secret upstream diagnostic")
        async def aclose(self):
            closed.append(True)
    monkeypatch.setattr(runtime.httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(lambda request: httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=FailingStream())), **kw))
    with pytest.raises(runtime.RuntimeFailure, match="unavailable") as error:
        run(collect(runtime.invoke_events(runtime.RuntimeTarget("runtime-http", "http://runtime.test"), {})))
    assert "secret" not in str(error.value) and closed == [True]


def test_late_aws_response_closed_after_cancellation(monkeypatch):
    import threading
    requested, release = threading.Event(), threading.Event()
    stream = io.BytesIO(frame({"event": "result"}))
    class Client:
        def invoke_agent_runtime(self, **kwargs):
            requested.set()
            release.wait(2)
            return {"statusCode": 200, "contentType": "text/event-stream", "response": StreamingBody(stream, len(stream.getvalue()))}
        def close(self):
            pass
    monkeypatch.setattr(runtime, "aws_client", lambda _: Client())
    async def check():
        task = asyncio.create_task(collect(runtime.invoke_events(runtime.RuntimeTarget("agentcore"), {})))
        await asyncio.to_thread(requested.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        for _ in range(100):
            if stream.closed:
                break
            await asyncio.sleep(.01)
        assert stream.closed
    run(check())
