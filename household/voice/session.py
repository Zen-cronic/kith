"""One voice session over one WebSocket.

identify (member id + PIN) -> `identified` frame -> a Nova 2 Sonic `BidiAgent` fed by `WebSocketBidiInput` and
drained by `WebSocketBidiOutput` -> on any Sonic failure, a `fallback` frame and a text `Agent` on the same socket.
Both agents get the same tools and the same `AuthorityHook`. Every outgoing frame goes through one queue and one
sender task, so tools running in worker threads can emit safely (`VoiceSession.emit`)."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from starlette.websockets import WebSocket, WebSocketDisconnect
from strands import Agent
from strands.experimental.bidi import BidiAgent
from strands.experimental.bidi.types.events import (
    BidiAudioInputEvent,
    BidiAudioStreamEvent,
    BidiConnectionCloseEvent,
    BidiConnectionRestartEvent,
    BidiConnectionStartEvent,
    BidiErrorEvent,
    BidiInterruptionEvent,
    BidiResponseCompleteEvent,
    BidiResponseStartEvent,
    BidiTextInputEvent,
    BidiTranscriptStreamEvent,
)
from strands.types._events import ToolUseStreamEvent

from ..agents.hooks import AuthorityHook, ToolVeto
from ..config import Settings
from ..model import Household, Member
from ..providers import build_model
from ..store import LedgerStore
from .frames import (
    AUDIO_CHANNELS,
    AUDIO_FORMAT,
    AUDIO_SAMPLE_RATE,
    FrameError,
    parse_client_frame,
    split_audio,
)
from .identify import IdentificationFailed, identify
from .prompts import voice_prompt
from .tools import VOICE_TOOL_NAMES, VoiceRecord, make_voice_tools, voice_guard

log = logging.getLogger(__name__)

MAX_SESSION_SECONDS = 600  # a Sonic connection is billed while open; no session outlives this
MAX_TEXT_TURNS = 40
CLOSE_NORMAL = 1000
SDK_LABEL = "Strands Agents SDK 1.54.0 (strands.experimental.bidi.BidiAgent)"
OFF_BANNER = "Voice is off on this server (SPEECH=off). Type your request; the same rules and receipts apply."
FALLBACK_BANNER = ("Live voice is unavailable right now, so this is the text agent on the same rules. "
                   "Type your request; the same approvals and receipts apply.")
VOICE_UNAVAILABLE = "Voice is unavailable in this session; type your request instead."


class ClientStop(Exception):
    """The client sent a stop frame."""


@dataclass
class VoiceSession:
    ws: WebSocket
    household: Household
    ledger: LedgerStore | None
    settings: Settings
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    member: Member | None = None
    mode: str = "text"
    client_gone: bool = False
    loop: asyncio.AbstractEventLoop = field(default_factory=asyncio.get_running_loop)
    queue: asyncio.Queue[dict[str, Any] | None] = field(default_factory=asyncio.Queue)
    sender: asyncio.Task[None] | None = None

    # Outgoing frames: one queue, one writer

    def start_sender(self) -> None:
        self.sender = asyncio.create_task(self._drain())

    async def _drain(self) -> None:
        while True:
            frame = await self.queue.get()
            if frame is None:
                return
            try:
                await self.ws.send_json(frame)
            except Exception:  # the socket is gone; the reader notices on its next receive
                self.client_gone = True
                return

    def emit(self, frame: dict[str, Any]) -> None:
        """Queue a frame from any thread (tools run in worker threads)."""
        self.loop.call_soon_threadsafe(self.queue.put_nowait, frame)

    async def send(self, frame: dict[str, Any]) -> None:
        await self.queue.put(frame)

    async def stop_sender(self) -> None:
        await self.queue.put(None)
        if self.sender is not None:
            await self.sender

    async def close(self, code: int, reason: str) -> None:
        await self.stop_sender()
        if self.client_gone:
            return
        try:
            await self.ws.close(code=code, reason=reason)
        except Exception:
            self.client_gone = True

    # Incoming frames

    async def receive_frame(self) -> dict[str, Any]:
        """The next valid client frame; invalid ones get an error frame and are skipped."""
        while True:
            try:
                raw = await self.ws.receive_text()
            except WebSocketDisconnect:
                self.client_gone = True
                raise
            try:
                return parse_client_frame(raw)
            except FrameError as exc:
                await self.send(exc.as_frame())


# BidiAgent IO over the socket


class WebSocketBidiInput:
    """BidiInput: client frames become BidiAgent input events. stop raises ClientStop; the rest are answered inline."""

    def __init__(self, session: VoiceSession) -> None:
        self.session = session

    async def start(self, agent: BidiAgent) -> None:
        return

    async def stop(self) -> None:
        return

    async def __call__(self) -> BidiAudioInputEvent | BidiTextInputEvent:
        while True:
            frame = await self.session.receive_frame()
            kind = frame["type"]
            if kind == "stop":
                raise ClientStop()
            if kind == "identify":
                await self.session.send({"type": "error", "code": "already_identified", "detail": "This session is already identified."})
                continue
            if kind == "bidi_audio_input":
                return BidiAudioInputEvent(audio=frame["audio"], format=AUDIO_FORMAT, sample_rate=AUDIO_SAMPLE_RATE, channels=AUDIO_CHANNELS)
            return BidiTextInputEvent(text=frame["text"], role="user")


def output_frame(event: Any) -> dict[str, Any] | None:
    """The JSON frame for one BidiAgent output event, or None for events the client does not need."""
    if isinstance(event, BidiAudioStreamEvent):
        return {"type": "bidi_audio_stream", "audio": event.audio, "format": event.format, "sample_rate": event.sample_rate, "channels": event.channels}
    if isinstance(event, BidiTranscriptStreamEvent):
        return {"type": "bidi_transcript_stream", "role": event.role, "text": event.text, "is_final": event.is_final}
    if isinstance(event, ToolUseStreamEvent):
        tool_use = event.get("current_tool_use") or {}
        return {"type": "tool", "name": tool_use.get("name"), "status": "started", "tool_use_id": tool_use.get("toolUseId")}
    if isinstance(event, BidiInterruptionEvent):
        return {"type": "bidi_interruption", "reason": event.reason}
    if isinstance(event, BidiResponseStartEvent):
        return {"type": "bidi_response_start", "response_id": event.response_id}
    if isinstance(event, BidiResponseCompleteEvent):
        return {"type": "bidi_response_complete", "response_id": event.response_id, "stop_reason": event.stop_reason}
    if isinstance(event, BidiConnectionStartEvent):
        return {"type": "bidi_connection_start", "model": event.model}
    if isinstance(event, BidiConnectionCloseEvent):
        return {"type": "bidi_connection_close", "reason": event.reason}
    if isinstance(event, BidiConnectionRestartEvent):
        return {"type": "bidi_connection_restart", "detail": str(event.timeout_error)}
    if isinstance(event, BidiErrorEvent):
        return {"type": "bidi_error", "code": event.code, "message": event.message}
    return None


class WebSocketBidiOutput:
    """BidiOutput: BidiAgent events become frames; audio is split so no frame exceeds the cap."""

    def __init__(self, session: VoiceSession) -> None:
        self.session = session
        self.audio_frames = 0

    async def start(self, agent: BidiAgent) -> None:
        return

    async def stop(self) -> None:
        return

    async def __call__(self, event: Any) -> None:
        frame = output_frame(event)
        if frame is None:
            return
        if frame["type"] == "bidi_audio_stream":
            for piece in split_audio(frame):
                self.audio_frames += 1
                await self.session.send(piece)
            return
        await self.session.send(frame)


# Model selection


def build_bidi_model(settings: Settings) -> Any:
    """Nova 2 Sonic for every live provider (it is always Bedrock); the offline fake for MODEL_PROVIDER=fake."""
    if settings.provider == "fake":
        from .fake import FakeSonicModel

        return FakeSonicModel()
    from strands.experimental.bidi.models.nova_sonic import BidiNovaSonicModel

    return BidiNovaSonicModel(
        model_id=settings.sonic_model_id,
        provider_config={"audio": {"voice": settings.sonic_voice}},
        client_config={"region": settings.aws_region},
    )


def build_text_model(settings: Settings) -> Any:
    if settings.provider == "fake":
        from .fake import VoiceFakeModel

        return VoiceFakeModel()
    return build_model(settings)


def model_label(settings: Settings, mode: str) -> str:
    if mode == "sonic":
        return "fake-sonic" if settings.provider == "fake" else settings.sonic_model_id
    return "fake-voice-text" if settings.provider == "fake" else settings.model_id


def veto_frame(veto: ToolVeto) -> dict[str, Any]:
    return {"type": "tool", "name": veto.tool, "status": "vetoed", "channel": veto.channel, "reason": veto.reason,
            "say": f"I can't do that by voice: {veto.reason}."}


# The session


async def run_sonic(session: VoiceSession, tools: dict[str, Any], hook: AuthorityHook, prompt: str) -> None:
    assert session.member is not None
    agent = BidiAgent(model=build_bidi_model(session.settings), tools=list(tools.values()), system_prompt=prompt,
                      hooks=[hook], name="Household voice", agent_id="voice")
    state = {"member_id": session.member.id, "household_id": session.household.id, "channel": "voice", "session_id": session.session_id}
    await agent.run(inputs=[WebSocketBidiInput(session)], outputs=[WebSocketBidiOutput(session)], invocation_state=state)


async def run_text(session: VoiceSession, tools: dict[str, Any], hook: AuthorityHook, prompt: str, record: VoiceRecord) -> None:
    assert session.member is not None
    agent = Agent(model=build_text_model(session.settings), tools=list(tools.values()), system_prompt=prompt, hooks=[hook],
                  callback_handler=None, name="Household voice (text)", agent_id="voice-text")
    state: dict[str, Any] = {"request_state": {}, "member_id": session.member.id, "household_id": session.household.id,
                             "channel": "voice-text", "session_id": session.session_id}
    turns = 0
    warned_audio = False
    while not record.stop_requested and turns < MAX_TEXT_TURNS:
        frame = await session.receive_frame()
        kind = frame["type"]
        if kind == "stop":
            raise ClientStop()
        if kind == "identify":
            await session.send({"type": "error", "code": "already_identified", "detail": "This session is already identified."})
            continue
        if kind == "bidi_audio_input":
            if not warned_audio:
                warned_audio = True
                await session.send({"type": "error", "code": "voice_unavailable", "detail": VOICE_UNAVAILABLE})
            continue
        turns += 1
        response_id = f"text-{turns}"
        text = frame["text"]
        await session.send({"type": "bidi_transcript_stream", "role": "user", "text": text, "is_final": True})
        await session.send({"type": "bidi_response_start", "response_id": response_id})
        spoken: list[str] = []
        started: set[str] = set()
        async for event in agent.stream_async(text, invocation_state=state):
            if not isinstance(event, dict):
                continue
            data = event.get("data")
            if isinstance(data, str) and data:
                spoken.append(data)
                await session.send({"type": "bidi_transcript_stream", "role": "assistant", "text": data, "is_final": False})
            tool_use = event.get("current_tool_use")
            if isinstance(tool_use, dict) and tool_use.get("name") and tool_use.get("toolUseId") not in started:
                started.add(str(tool_use.get("toolUseId")))
                await session.send({"type": "tool", "name": tool_use["name"], "status": "started", "tool_use_id": tool_use.get("toolUseId")})
        if not spoken and record.stop_requested and record.tool_calls:
            spoken.append(str(record.tool_calls[-1].get("say", "")))  # the stop tool ends the loop before a model turn
        await session.send({"type": "bidi_transcript_stream", "role": "assistant", "text": "".join(spoken), "is_final": True})
        await session.send({"type": "bidi_response_complete", "response_id": response_id, "stop_reason": "complete"})
    await session.send({"type": "bidi_connection_close", "reason": "user_request" if record.stop_requested else "complete"})


async def handle_voice_session(
    ws: WebSocket,
    household: Household,
    store: LedgerStore | None,
    settings: Settings,
    *,
    uploads_dir: Path | None = None,
    session_seconds: float = MAX_SESSION_SECONDS,
    now: datetime | None = None,
    accepted: bool = False,
    member: Member | None = None,
) -> None:
    """Accept the socket, identify the member, run Sonic (or the text fallback) until stop, disconnect or timeout.

    `accepted` skips `ws.accept()` when the caller already accepted the socket (the AgentCore `/ws` handler reads a
    `session_open` frame first). `member`, when given, is an actor the caller already authenticated (PIN checks stay
    on the web tier that holds the ledger), so the on-socket PIN identify step is skipped."""
    if not accepted:
        await ws.accept()
    session = VoiceSession(ws=ws, household=household, ledger=store, settings=settings)
    session.start_sender()
    closed = False
    try:
        if member is None:
            try:
                member = await identify(ws.receive_text, session.send, session.close, household)
            except IdentificationFailed:
                closed = True
                return
        session.member = member
        record = VoiceRecord(session_id=session.session_id)
        hook = AuthorityHook(VOICE_TOOL_NAMES, guard=voice_guard(member), on_veto=lambda veto: session.emit(veto_frame(veto)))
        tools = make_voice_tools(household, member, settings, store, record, session.emit, uploads_dir=uploads_dir, now=now)
        prompt = voice_prompt(member, household, now)
        session.mode = "sonic" if settings.speech == "on" else "text"
        await session.send({
            "type": "identified", "session_id": session.session_id, "mode": session.mode,
            "banner": None if session.mode == "sonic" else OFF_BANNER,
            "member": {"id": member.id, "name": member.name, "role": member.role, "language": member.language},
            "model": model_label(settings, session.mode), "sdk": SDK_LABEL, "tools": list(VOICE_TOOL_NAMES),
        })
        if session.mode == "sonic":
            try:
                await asyncio.wait_for(run_sonic(session, tools, hook, prompt), timeout=session_seconds)
            except (WebSocketDisconnect, ClientStop):
                raise
            except TimeoutError:
                await session.send({"type": "bidi_connection_close", "reason": "timeout"})
                return
            except Exception as exc:  # Sonic unavailable: same socket, text agent, visible banner
                if session.client_gone:
                    closed = True
                    return
                reason = f"{type(exc).__name__}: {exc}"
                log.warning("voice session %s: Sonic unavailable (%s); falling back to text", session.session_id, reason)
                session.mode = "text"
                await session.send({"type": "fallback", "mode": "text", "banner": FALLBACK_BANNER, "reason": reason,
                                    "model": model_label(settings, "text")})
        if session.mode == "text" and not record.stop_requested:
            await run_text(session, tools, hook, prompt, record)
    except WebSocketDisconnect:
        closed = True
    except ClientStop:
        pass
    finally:
        if closed:
            await session.stop_sender()
        else:
            await session.close(CLOSE_NORMAL, "session ended")
