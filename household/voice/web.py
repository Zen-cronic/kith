"""The /api/voice router. `router` is what the web app includes (`app.include_router(voice.router)`); `build_router`
takes an explicit store and settings for tests and the dev server. No credential or endpoint comes from the client."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, WebSocket

from ..config import Settings, load_settings
from ..store import JsonLedgerStore, LedgerStore
from .frames import CLIENT_TYPES, MAX_FRAME_BYTES, SERVER_TYPES
from .session import SDK_LABEL, handle_voice_session
from .tools import VOICE_TOOL_NAMES

STATIC_VOICE = Path(__file__).resolve().parent.parent / "web" / "static" / "voice"
DEFAULT_HOUSEHOLD = "demo"


def build_router(store: LedgerStore | None = None, settings: Settings | None = None, uploads_dir: Path | None = None) -> APIRouter:
    router = APIRouter()

    def resolve() -> tuple[LedgerStore, Settings]:
        return store or JsonLedgerStore(), settings or load_settings()

    @router.get("/api/voice/meta")
    def meta(household_id: str = DEFAULT_HOUSEHOLD) -> dict[str, Any]:
        ledger, resolved = resolve()
        household = ledger.load(household_id)
        return {
            "speech": resolved.speech,
            "sonic_model_id": resolved.sonic_model_id,
            "sonic_voice": resolved.sonic_voice,
            "provider": resolved.provider,
            "sdk": SDK_LABEL,
            "tools": list(VOICE_TOOL_NAMES),
            "frame_limit_bytes": MAX_FRAME_BYTES,
            "client_frame_types": sorted(CLIENT_TYPES),
            "server_frame_types": sorted(SERVER_TYPES),
            "household": {"id": household.id, "name": household.name},
            "members": [{"id": m.id, "name": m.name, "role": m.role, "language": m.language} for m in household.members],
        }

    @router.websocket("/api/voice")
    async def voice(ws: WebSocket, household_id: str = DEFAULT_HOUSEHOLD) -> None:
        ledger, resolved = resolve()
        try:
            household = ledger.load(household_id)
        except (KeyError, ValueError):
            await ws.accept()
            await ws.close(code=1008, reason="unknown household")
            return
        await handle_voice_session(ws, household, ledger, resolved, uploads_dir=uploads_dir)

    return router


router = build_router()
