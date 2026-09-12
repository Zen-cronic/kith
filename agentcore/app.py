"""Amazon Bedrock AgentCore Runtime entrypoint for Front Desk.

The deployment target runs the same Strands Graph as the local CLI and web server. It streams
roster events over SSE. Memory attachment is opt-in, but AWS persistence, isolation and session
resumption have not been verified. With Memory unset, no Memory session manager is attached;
Bedrock inputs and service logging remain separate data flows.

Invocation payload (JSON):
    {"fixture_id": "ltb-n4", "language": "es"}                       # replay a fixture
    {"document_text": "...", "title": "...", "language": "es"}         # a document brought to the desk
    optional: "session_id" (string) - used as the AgentCore Memory session when memory is enabled

Local check without AWS: MODEL_PROVIDER=fake poetry run python agentcore/app.py  ->  POST http://localhost:8080/invocations
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from household.config import load_settings
from household.pipeline import adhoc_document, stream_session
from household.providers.budget import ModelCallLimitExceeded
from household.runtime_protocol import runtime_metadata

app = BedrockAgentCoreApp()


def _memory_session_manager(session_id: str, region: str) -> Any | None:
    memory_id = os.environ.get("AGENTCORE_MEMORY_ID")
    if not memory_id:
        return None
    from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig
    from bedrock_agentcore.memory.integrations.strands.session_manager import AgentCoreMemorySessionManager

    config = AgentCoreMemoryConfig(memory_id=memory_id, session_id=session_id, actor_id=os.environ.get("AGENTCORE_ACTOR_ID", "household"))
    return AgentCoreMemorySessionManager(config, region_name=region)


def _serializable(event: dict[str, Any]) -> dict[str, Any]:
    if event.get("event") == "result":
        # Preserve required nullable fields so the receiving bridge can validate the schema.
        return {"event": "result", "result": event["result"].model_dump(mode="json")}
    return event


@app.entrypoint
async def invoke(payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    """Stream Front Desk roster events for one session. Each yielded dict becomes one SSE `data:` line."""
    settings = load_settings()
    metadata = runtime_metadata(settings)
    if payload.get("operation") == "metadata":
        yield {"event": "runtime_meta", "metadata": metadata}
        return
    if "expected_config" in payload and (payload["expected_config"] != metadata["config_token"] or metadata["memory_enabled"]):
        yield {"event": "error", "code": "runtime_configuration", "detail": "Processing configuration changed; review consent again."}
        return
    language = str(payload.get("language", "es"))
    session_id = str(payload.get("session_id") or uuid.uuid4())
    document = None
    fixture_id = payload.get("fixture_id")
    if payload.get("document_text"):
        document = adhoc_document(str(payload["document_text"]), str(payload.get("title") or "Document brought to the desk"))
        fixture_id = None
    if not fixture_id and document is None:
        yield {"event": "error", "detail": "payload needs fixture_id or document_text"}
        return
    session_manager = _memory_session_manager(session_id, settings.aws_region)
    try:
        async for event in stream_session(fixture_id, language, settings=settings, document=document, session_manager=session_manager):
            yield _serializable(event)
    except ModelCallLimitExceeded as exc:
        yield exc.as_event()


@app.ping
def ping() -> str:
    return "Healthy"


if __name__ == "__main__":
    app.run()
