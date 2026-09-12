"""FastAPI app serving the household screen and streaming sessions as server-sent events."""

from __future__ import annotations

import json
from contextlib import aclosing
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..agents.graph import describe_graph
from ..agents.roster import ROSTER
from ..config import load_settings, provider_readiness
from ..fixtures import FixtureStore
from ..languages import LANGUAGES
from ..pipeline import adhoc_document, stream_session
from ..providers.budget import ModelCallLimitExceeded
from ..rules import FIDELITY_RULE, RULES
from .runtime import RuntimeFailure, config_changed, remote_metadata, remote_session, runtime_target

STATIC = Path(__file__).parent / "static"


class RunRequest(BaseModel):
    fixture_id: str | None = None
    document_text: str | None = None
    title: str | None = None
    language: str = Field(default="es")
    consent_token: str | None = None


def create_app() -> FastAPI:
    app = FastAPI(title="Front Desk", version="0.1.0")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/demo")
    def demo() -> FileResponse:
        return FileResponse(STATIC / "demo" / "index.html")

    @app.get("/api/meta")
    async def meta() -> dict[str, Any]:
        settings = load_settings()
        try:
            target = runtime_target()
            remote = await remote_metadata(target) if target.backend != "local" else None
        except RuntimeFailure as exc:
            raise HTTPException(status_code=503, detail=exc.detail) from exc
        return {
            "provider": remote["provider"] if remote else settings.provider,
            "model_id": remote["model_id"] if remote else settings.model_id,
            "max_model_calls": remote["max_model_calls"] if remote else settings.max_model_calls,
            "providers": {} if remote else provider_readiness(),
            "backend": target.backend,
            "consent_token": target.consent_token(remote) if remote else None,
            "sdk": "Strands Agents SDK (strands-agents 1.54.0)",
            "roster": [{"id": s.id, "name": s.name, "job": s.job, "can_reject": s.can_reject, "tools": list(s.tools)} for s in ROSTER],
            "languages": [
                {"code": lang.code, "name_en": lang.name_en, "name_native": lang.name_native, "mode": lang.mode,
                 "polly": lang.polly_voice, "nova_sonic": lang.nova_sonic, "browser": lang.browser}
                for lang in LANGUAGES.values()
            ],
            "rules": [{"id": r.id, "title": r.title, "kind": r.kind, "policy": r.policy, "classes": list(r.document_classes)} for r in (*RULES, FIDELITY_RULE)],
            "graph_mermaid": describe_graph(settings),
        }

    @app.get("/api/fixtures")
    def fixtures() -> list[dict[str, Any]]:
        return [
            {"id": d.id, "title": d.title, "source": d.source, "is_real": d.is_real, "stakes": d.expected.stakes,
             "must_escalate": d.expected.must_escalate, "tags": list(d.tags), "summary_en": d.summary_en, "text": d.text}
            for d in FixtureStore().list()
        ]

    @app.post("/api/run")
    async def run(req: RunRequest) -> StreamingResponse:
        if not req.fixture_id and not (req.document_text and req.document_text.strip()):
            raise HTTPException(status_code=400, detail="fixture_id or document_text is required")
        if req.language not in LANGUAGES:
            raise HTTPException(status_code=400, detail=f"unsupported language {req.language!r}")
        settings = load_settings()
        document = adhoc_document(req.document_text, req.title or "Document brought to the desk") if req.document_text else None

        async def events():
            try:
                target = runtime_target()
                if target.backend != "local":
                    if not req.consent_token:
                        raise config_changed()
                    remote = await remote_metadata(target)
                    if req.consent_token != target.consent_token(remote):
                        raise config_changed()
                    payload = req.model_dump(exclude_none=True, exclude={"consent_token"})
                    async with aclosing(remote_session(target, payload, remote)) as stream:
                        async for event in stream:
                            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    return
                if req.consent_token:
                    # A page that consented to a remote service cannot silently fall back locally.
                    raise config_changed()
                async for event in stream_session(req.fixture_id if not document else None, req.language, settings=settings, document=document):
                    if event["event"] == "result":
                        payload = {"event": "result", "result": event["result"].model_dump(mode="json", exclude_none=True)}
                    else:
                        payload = event
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            except RuntimeFailure as exc:
                yield f"data: {json.dumps(exc.as_event())}\n\n"
            except ModelCallLimitExceeded as exc:
                yield f"data: {json.dumps(exc.as_event())}\n\n"
            except Exception as exc:  # surface the failure to the screen instead of a dead stream
                yield f"data: {json.dumps({'event': 'error', 'detail': str(exc)})}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


app = create_app()
