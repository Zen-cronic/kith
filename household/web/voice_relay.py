"""Relay the browser voice WebSocket to the Runtime `/ws`, so no AWS credential ever reaches the browser.

The browser talks only to this server's `/api/voice`. When `SESSION_BACKEND` is `agentcore` the server signs a
SigV4 `wss://` URL with `AgentCoreRuntimeClient.generate_ws_connection` and connects to the Runtime; when it is
`runtime-http` the server connects to `ws://$RUNTIME_HTTP_URL/ws`. The web tier holds the ledger and has already
identified the actor by PIN, so the relay opens the Runtime socket with a `session_open` frame carrying the household
snapshot and the actor id, then runs two pump tasks (browser -> Runtime with frame validation, Runtime -> browser)
and propagates the first close to the other side."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from ..model import Household, Member
from ..voice.frames import FrameError, parse_client_frame
from .runtime import MAX_EVENT_BYTES, RuntimeFailure, RuntimeTarget


class RuntimeConnection:
    """A thin text-frame view over a `websockets` client connection."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    async def send(self, text: str) -> None:
        await self._conn.send(text)

    async def recv(self) -> str:
        message = await self._conn.recv()
        return message.decode("utf-8") if isinstance(message, bytes | bytearray) else message

    async def close(self) -> None:
        await self._conn.close()


@asynccontextmanager
async def connect_runtime(target: RuntimeTarget, session_id: str):
    """Open the Runtime `/ws` for the selected backend and yield a `RuntimeConnection`."""
    import websockets

    if target.backend == "agentcore":
        from bedrock_agentcore.runtime.agent_core_runtime_client import AgentCoreRuntimeClient

        signer = AgentCoreRuntimeClient(region=target.region)
        url, headers = signer.generate_ws_connection(target.endpoint, session_id=session_id, endpoint_name=target.qualifier)
        conn = await websockets.connect(url, additional_headers=headers, max_size=MAX_EVENT_BYTES, open_timeout=10)
    elif target.backend == "runtime-http":
        ws_url = "ws" + target.endpoint[len("http"):] + "/ws"
        conn = await websockets.connect(ws_url, max_size=MAX_EVENT_BYTES, open_timeout=10)
    else:
        raise RuntimeFailure("runtime_configuration", "This backend does not relay voice sessions.")
    try:
        yield RuntimeConnection(conn)
    finally:
        await conn.close()


async def pump(
    recv: Callable[[], Awaitable[str]],
    send: Callable[[str], Awaitable[None]],
    *,
    validate: bool = False,
    report: Callable[[str], Awaitable[None]] | None = None,
) -> str:
    """Move text frames from `recv` to `send` until either side closes. With `validate`, each frame must pass the
    client-frame contract; an invalid frame is answered on `report` (if given) and dropped, never forwarded."""
    while True:
        try:
            raw = await recv()
        except Exception:
            return "source-closed"
        if validate:
            try:
                parse_client_frame(raw)
            except FrameError as exc:
                if report is not None:
                    await report(json.dumps(exc.as_frame()))
                continue
        try:
            await send(raw)
        except Exception:
            return "dest-closed"


async def relay_voice_session(
    ws: Any,
    target: RuntimeTarget,
    household: Household,
    member: Member,
    *,
    session_id: str | None = None,
) -> None:
    """Relay an already-accepted, already-identified browser socket to the Runtime `/ws`."""
    session_id = session_id or uuid.uuid4().hex
    async with connect_runtime(target, session_id) as runtime:
        await runtime.send(json.dumps({
            "type": "session_open",
            "household_snapshot": household.model_dump(mode="json"),
            "actor_member_id": member.id,
        }))
        browser_to_runtime = asyncio.create_task(pump(ws.receive_text, runtime.send, validate=True, report=ws.send_text))
        runtime_to_browser = asyncio.create_task(pump(runtime.recv, ws.send_text))
        tasks = {browser_to_runtime, runtime_to_browser}
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
    try:
        await ws.close()
    except Exception:
        pass
