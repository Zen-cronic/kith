"""Amazon Bedrock AgentCore Runtime entrypoint for the household agent.

The container runs the same six-node Strands Graph as the CLI and web server, plus the live voice session. Two
transports are served on 8080:

    POST /invocations  streaming SSE: one household session, each yielded dict becomes one `data:` line.
    /ws                WebSocket: a `session_open` frame carries the household snapshot and the already-identified
                       actor; the rest of the socket is a Nova 2 Sonic (or text-fallback) voice session.

Memory attachment is opt-in and off by default: with `AGENTCORE_MEMORY_ID` unset, no Memory session manager is
attached, so Bedrock inputs and service logs stay separate data flows.

Invocation payload (JSON):
    {"fixture_id": "kofi-allowance-8", "actor_member_id": "kofi", "language": "en"}   # replay a request fixture
    {"request_text": "...", "actor_member_id": "kofi", "language": "en"}               # a typed request
    {"document_text": "...", "actor_member_id": "kofi"}                                # a document read as the request
    {"image_b64": "...", "format": "png", "actor_member_id": "kofi"}                   # a photo or PDF shown
    optional: "household_snapshot" (a serialized Household; otherwise the bundled demo fixtures are used),
              "session_id" (the AgentCore Memory session when memory is enabled), "expected_config".

Local check without AWS: MODEL_PROVIDER=fake python agentcore/app.py  ->  POST http://localhost:8080/invocations
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import tempfile
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from household.config import Settings, load_settings
from household.executor import RAILS
from household.model import Household
from household.pipeline import stream_session
from household.providers.budget import ModelCallLimitExceeded
from household.runtime_protocol import digest, runtime_metadata
from household.skills import ALL_SKILLS

app = BedrockAgentCoreApp()

UPLOAD_SUFFIX = {"png": ".png", "jpeg": ".jpg", "jpg": ".jpg", "gif": ".gif", "webp": ".webp", "pdf": ".pdf"}


def _memory_session_manager(session_id: str, region: str) -> Any | None:
    memory_id = os.environ.get("AGENTCORE_MEMORY_ID")
    if not memory_id:
        return None
    from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig
    from bedrock_agentcore.memory.integrations.strands.session_manager import AgentCoreMemorySessionManager

    config = AgentCoreMemoryConfig(memory_id=memory_id, session_id=session_id, actor_id=os.environ.get("AGENTCORE_ACTOR_ID", "household"))
    return AgentCoreMemorySessionManager(config, region_name=region)


def runtime_meta(settings: Settings) -> dict[str, Any]:
    """The handshake metadata plus the fields that let the web tier show what will run: the execution mode, the
    action rails, and a digest of the installed skills."""
    meta = runtime_metadata(settings)
    meta["execution_mode"] = settings.execution_mode
    meta["rails"] = sorted(RAILS)
    meta["skills_digest"] = digest([{"id": s.id, "action_types": sorted(s.action_types)} for s in ALL_SKILLS])
    return meta


def _serializable(event: dict[str, Any]) -> dict[str, Any]:
    if event.get("event") == "result":
        # Preserve required nullable fields so the receiving bridge can validate the schema.
        return {"event": "result", "result": event["result"].model_dump(mode="json")}
    return event


def _household(payload: dict[str, Any]) -> Household | None:
    snapshot = payload.get("household_snapshot")
    return Household.model_validate(snapshot) if snapshot is not None else None


@app.entrypoint
async def invoke(payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    """Stream household roster events for one session. Each yielded dict becomes one SSE `data:` line."""
    settings = load_settings()
    if payload.get("operation") == "metadata":
        yield {"event": "runtime_meta", "metadata": runtime_meta(settings)}
        return
    meta = runtime_metadata(settings)
    if "expected_config" in payload and (payload["expected_config"] != meta["config_token"] or meta["memory_enabled"]):
        yield {"event": "error", "code": "runtime_configuration", "detail": "Processing configuration changed; review consent again."}
        return
    actor_member_id = payload.get("actor_member_id")
    session_id = str(payload.get("session_id") or uuid.uuid4())
    request: str | None = None
    upload: Path | None = None
    temp: Path | None = None
    if payload.get("fixture_id"):
        request = str(payload["fixture_id"])
    elif payload.get("request_text"):
        request = str(payload["request_text"])
    elif payload.get("document_text"):
        request = str(payload["document_text"])
    elif payload.get("image_b64"):
        suffix = UPLOAD_SUFFIX.get(str(payload.get("format", "")).lower())
        if suffix is None:
            yield {"event": "error", "detail": "image format must be one of png, jpeg, gif, webp, pdf"}
            return
        try:
            data = base64.b64decode(str(payload["image_b64"]), validate=True)
        except (binascii.Error, ValueError):
            yield {"event": "error", "detail": "image_b64 is not valid base64"}
            return
        handle = tempfile.NamedTemporaryFile(prefix="household-upload-", suffix=suffix, delete=False)
        handle.write(data)
        handle.close()
        upload = temp = Path(handle.name)
    if request is None and upload is None:
        yield {"event": "error", "detail": "payload needs fixture_id, request_text, document_text or image_b64"}
        return
    session_manager = _memory_session_manager(session_id, settings.aws_region)
    try:
        async for event in stream_session(request, actor_member_id, settings=settings, household=_household(payload),
                                          upload=upload, session_manager=session_manager):
            yield _serializable(event)
    except ModelCallLimitExceeded as exc:
        yield exc.as_event()
    except (KeyError, ValueError) as exc:
        yield {"event": "error", "detail": str(exc)}
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


@app.websocket
async def voice(websocket: Any, context: Any) -> None:
    """The voice transport. The web relay opens the socket with a `session_open` frame carrying the household
    snapshot and the already-identified actor (PIN checks stay on the web tier, which holds the ledger), then the
    same voice session as the local `/api/voice` path runs on this socket."""
    from household.voice.session import handle_voice_session

    await websocket.accept()
    settings = load_settings()
    try:
        raw = await websocket.receive_text()
    except Exception:
        return
    try:
        frame = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        await websocket.close(code=1003, reason="expected a session_open frame")
        return
    if not isinstance(frame, dict) or frame.get("type") != "session_open":
        await websocket.close(code=1008, reason="send session_open first")
        return
    try:
        household = Household.model_validate(frame.get("household_snapshot"))
    except Exception:
        await websocket.close(code=1008, reason="invalid household snapshot")
        return
    member = household.member(frame.get("actor_member_id")) if frame.get("actor_member_id") else None
    if member is None:
        await websocket.close(code=1008, reason="unknown actor")
        return
    await handle_voice_session(websocket, household, None, settings, accepted=True, member=member)


@app.ping
def ping() -> str:
    return "Healthy"


if __name__ == "__main__":
    app.run()
