"""Server-selected Runtime transports. No credentials or endpoint comes from the browser."""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import aclosing
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import boto3
import httpx
from botocore.config import Config

from ..agents.roster import ROSTER
from ..config import PROVIDERS
from ..pipeline import SessionResult
from ..runtime_protocol import PROTOCOL_VERSION, digest, fixture_digest

MAX_EVENT_BYTES = 1_048_576
# Household session events the bridge relays untouched once the session has started (decided and issued in code upstream).
PASSTHROUGH_EVENTS = frozenset({"action", "receipt", "approval_needed", "ledger_post"})


class RuntimeFailure(Exception):
    def __init__(self, code: str = "runtime_unavailable", detail: str = "The reading service is unavailable. Ask staff for help."):
        self.code, self.detail = code, detail
        super().__init__(detail)

    def as_event(self) -> dict[str, str]:
        return {"event": "error", "code": self.code, "detail": self.detail}


def protocol_failure() -> RuntimeFailure:
    return RuntimeFailure("runtime_protocol", "The reading service returned an incomplete or invalid response. Ask staff for help.")


def config_changed() -> RuntimeFailure:
    return RuntimeFailure("runtime_configuration", "The processing configuration changed. Reload Front Desk and review consent before trying again.")


@dataclass(frozen=True)
class RuntimeTarget:
    backend: str
    endpoint: str = ""
    region: str = "us-east-1"
    qualifier: str = "DEFAULT"

    def consent_token(self, metadata: dict[str, Any]) -> str:
        return digest([self.backend, self.endpoint, self.region, self.qualifier, metadata["config_token"]])


def runtime_target() -> RuntimeTarget:
    backend = os.environ.get("SESSION_BACKEND", "local").strip().lower()
    if backend == "local":
        return RuntimeTarget(backend)
    if backend == "runtime-http":
        endpoint = os.environ.get("RUNTIME_HTTP_URL", "").rstrip("/")
        url = urlsplit(endpoint)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise RuntimeFailure("runtime_configuration", "Staff must configure the reading service address.")
        return RuntimeTarget(backend, endpoint)
    if backend == "agentcore":
        arn = os.environ.get("HOUSEHOLD_RUNTIME_ARN", "")
        if not arn.startswith("arn:") or ":bedrock-agentcore:" not in arn or ":runtime/" not in arn:
            raise RuntimeFailure("runtime_configuration", "Staff must configure the AgentCore Runtime address.")
        return RuntimeTarget(backend, arn, os.environ.get("AWS_REGION", "us-east-1"), os.environ.get("HOUSEHOLD_RUNTIME_QUALIFIER", "DEFAULT"))
    raise RuntimeFailure("runtime_configuration", "Staff must configure a supported reading service.")


async def decode_sse(chunks: AsyncIterator[bytes]) -> AsyncIterator[dict[str, Any]]:
    """Bounded SSE JSON decoder; accepts split UTF-8, CR/LF, comments and multiline data."""
    line = bytearray()
    data: list[bytes] = []
    size = 0
    after_cr = False

    def consume_line() -> dict[str, Any] | None:
        nonlocal size
        raw = bytes(line)
        line.clear()
        if not raw:
            if not data:
                return None
            try:
                value = json.loads(b"\n".join(data))
            except (ValueError, UnicodeError) as exc:
                raise protocol_failure() from exc
            data.clear()
            size = 0
            if not isinstance(value, dict) or not isinstance(value.get("event"), str):
                raise protocol_failure()
            return value
        if raw.startswith(b"data:"):
            value = raw[5:]
            if value.startswith(b" "):
                value = value[1:]
            size += len(value) + 1
            if size > MAX_EVENT_BYTES:
                raise protocol_failure()
            data.append(value)
        return None

    async for chunk in chunks:
        for byte in chunk:
            if after_cr and byte == 10:
                after_cr = False
                continue
            after_cr = byte == 13
            if byte in (10, 13):
                value = consume_line()
                if value is not None:
                    yield value
            else:
                line.append(byte)
                if len(line) > MAX_EVENT_BYTES:
                    raise protocol_failure()
    # An unterminated frame is a broken stream, even if its JSON happens to parse.
    if line or data:
        raise protocol_failure()


def aws_client(target: RuntimeTarget):
    return boto3.client("bedrock-agentcore", region_name=target.region,
                        config=Config(connect_timeout=10, read_timeout=180, retries={"total_max_attempts": 1, "mode": "standard"}))


async def _aws_chunks(target: RuntimeTarget, payload: dict[str, Any]) -> AsyncIterator[bytes]:
    client = aws_client(target)
    body = None
    pending = asyncio.create_task(asyncio.to_thread(
        client.invoke_agent_runtime, agentRuntimeArn=target.endpoint, qualifier=target.qualifier,
        runtimeSessionId=str(uuid.uuid4()), contentType="application/json", accept="text/event-stream",
        payload=json.dumps(payload, ensure_ascii=False).encode(),
    ))
    try:
        try:
            response = await asyncio.shield(pending)
        except asyncio.CancelledError:
            # A blocking SDK request can finish after the browser leaves; close its late body.
            def close_late(task):
                try:
                    task.result()["response"].close()
                except Exception:
                    pass
                finally:
                    client.close()
            pending.add_done_callback(close_late)
            raise
        body = response["response"]
        if response.get("statusCode") != 200 or not response.get("contentType", "").startswith("text/event-stream"):
            raise protocol_failure()
        iterator = body.iter_chunks(chunk_size=1024)
        while (chunk := await asyncio.to_thread(next, iterator, None)) is not None:
            yield chunk
    finally:
        if body is not None:
            body.close()
        if pending.done() and not pending.cancelled():
            client.close()


async def _http_chunks(target: RuntimeTarget, payload: dict[str, Any]) -> AsyncIterator[bytes]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=10), follow_redirects=False) as client:
        async with client.stream("POST", target.endpoint + "/invocations", json=payload) as response:
            if response.status_code != 200:
                raise RuntimeFailure()
            if not response.headers.get("content-type", "").startswith("text/event-stream"):
                raise protocol_failure()
            async for chunk in response.aiter_bytes():
                yield chunk


async def invoke_events(target: RuntimeTarget, payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    try:
        chunks = _aws_chunks(target, payload) if target.backend == "agentcore" else _http_chunks(target, payload)
        async with aclosing(chunks), aclosing(decode_sse(chunks)) as events:
            async for event in events:
                yield event
    except RuntimeFailure:
        raise
    except Exception as exc:
        # Do not return provider error bodies, ARNs or credential diagnostics to a visitor.
        raise RuntimeFailure() from exc


async def remote_metadata(target: RuntimeTarget) -> dict[str, Any]:
    values = []
    async with aclosing(invoke_events(target, {"operation": "metadata"})) as events:
        async for event in events:
            values.append(event)
            if len(values) > 1:
                raise protocol_failure()
    if len(values) != 1 or values[0].get("event") != "runtime_meta":
        raise protocol_failure()
    meta = values[0].get("metadata", {})
    if (not isinstance(meta, dict) or meta.get("protocol_version") != PROTOCOL_VERSION or meta.get("provider") not in PROVIDERS
            or not isinstance(meta.get("model_id"), str) or not isinstance(meta.get("config_token"), str)
            or len(meta["config_token"]) != 64 or type(meta.get("max_model_calls")) is not int
            or meta["max_model_calls"] <= 0 or meta.get("fixture_digest") != fixture_digest()):
        raise protocol_failure()
    if meta.get("memory_enabled") is not False:
        raise RuntimeFailure("runtime_configuration", "Staff must disable Runtime Memory before this reading service can be used.")
    return meta


async def remote_session(target: RuntimeTarget, payload: dict[str, Any], meta: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    started = None
    terminal = None
    node_ids = {step.id for step in ROSTER}
    async with aclosing(invoke_events(target, {**payload, "expected_config": meta["config_token"]})) as events:
        async for event in events:
            kind = event["event"]
            if terminal is not None:
                raise protocol_failure()
            if kind == "error":
                if event.get("code") == "runtime_configuration":
                    raise config_changed()
                if event.get("code") == "model_call_limit":
                    terminal = {"event": "error", "code": "model_call_limit", "detail": f"This reading reached its {meta['max_model_calls']}-call model limit. Ask staff for help before trying again.", "model_calls": event.get("model_calls", {})}
                else:
                    raise RuntimeFailure()
            elif kind == "session_start":
                if started is not None or any(event.get(key) != value for key, value in {
                    "language": payload["language"], "provider": meta["provider"], "model_id": meta["model_id"],
                }.items()) or (payload.get("fixture_id") and event.get("fixture_id") != payload["fixture_id"]):
                    raise protocol_failure()
                started = event
                yield event
            elif kind == "result":
                if started is None:
                    raise protocol_failure()
                try:
                    result = SessionResult.model_validate(event.get("result"))
                except ValueError as exc:
                    raise protocol_failure() from exc
                if any(getattr(result, key) != started[key] for key in ("fixture_id", "language", "provider", "model_id")):
                    raise protocol_failure()
                terminal = {"event": "result", "result": result.model_dump(mode="json", exclude_none=True)}
            elif kind in {"node_start", "node_done"} and started is not None and event.get("node_id") in node_ids:
                yield event
            elif kind in PASSTHROUGH_EVENTS and started is not None:
                yield event
            else:
                raise protocol_failure()
    if terminal is None:
        raise protocol_failure()
    # A completed result becomes printable only after the upstream ends cleanly.
    yield terminal
