"""Deterministic stand-ins for the voice models. Neither touches a network.

`VoiceFakeModel` is a Strands `Model` for the text fallback; `FakeSonicModel` is a `BidiModel` that behaves like Nova
Sonic (user transcript, tool use, assistant transcript, audio chunks) so the real `BidiAgent` loop, the authority hook
and the socket frames run offline. Both script the tool call from the words themselves: an amount becomes
propose_action, "status" becomes household_status, "recall" becomes lookup_recall, and so on. Anything measured with
them is a pipeline check, not a model measurement."""

from __future__ import annotations

import asyncio
import base64
import json
import math
import re
import struct
import time
import uuid
from collections.abc import AsyncGenerator, AsyncIterable
from typing import Any

from strands.experimental.bidi.types.events import (
    BidiAudioInputEvent,
    BidiAudioStreamEvent,
    BidiConnectionStartEvent,
    BidiResponseCompleteEvent,
    BidiResponseStartEvent,
    BidiTextInputEvent,
    BidiTranscriptStreamEvent,
)
from strands.models import Model
from strands.types._events import ToolResultEvent, ToolUseStreamEvent
from strands.types.content import Messages
from strands.types.streaming import StreamEvent
from strands.types.tools import ToolSpec

FAKE_LABEL = "[fake voice model: scripted from the words, not a model output]"
FAKE_AUDIO_TRANSCRIPT = "Hey, can I have eight dollars for the book fair?"
FAKE_SONIC_MODEL_ID = "fake-sonic"
FAKE_TEXT_MODEL_ID = "fake-voice-text"
MIN_AUDIO_BYTES = 16_000  # half a second of 16 kHz mono 16-bit PCM counts as one spoken turn
TURN_GAP_SECONDS = 0.5  # audio arriving after this much silence starts the next spoken turn
NO_TOOL_REPLY = ("I can help with your allowance, a payment or an email, a product recall, a grant, the last document "
                 "shown to me, or what is waiting.")
NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100,
}
_MEMBER = re.compile(r"^Member: (\S+) \((adult|minor)\)$", re.M)
_AMOUNT = re.compile(r"\$\s?(\d+(?:\.\d{1,2})?)|\b(\d+(?:\.\d{1,2})?)\s*(?:dollars?|bucks|cad)\b")
_WORDS = "|".join(NUMBER_WORDS)
_WORD_AMOUNT = re.compile(rf"\b((?:{_WORDS})(?:[ -](?:{_WORDS}))*)\s+(?:dollars?|bucks)\b")
_RECALL = re.compile(r"\b(\d{4,6})\b")
_GRANT = re.compile(r"\b(g-[a-z0-9-]+|rule:[a-z-]+|approval:[a-z0-9-]+)\b")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PURPOSE = re.compile(r"\bfor\s+(?:the\s+|my\s+|a\s+)?(.+?)[?.!]*$", re.I)


# The shared script


def member_from_prompt(system_prompt: str | None) -> tuple[str, str]:
    """(member id, role) from the `Member:` line the voice prompt carries; an adult when absent."""
    match = _MEMBER.search(system_prompt or "")
    return (match.group(1), match.group(2)) if match else ("", "adult")


def amount_in(text: str) -> str | None:
    lowered = text.lower()
    match = _AMOUNT.search(lowered)
    if match:
        return f"{float(match.group(1) or match.group(2)):.2f}"
    match = _WORD_AMOUNT.search(lowered)
    if match:
        total = 0
        for word in re.split(r"[ -]", match.group(1)):
            value = NUMBER_WORDS[word]
            total = (total or 1) * 100 if value == 100 else total + value
        return f"{total:.2f}"
    return None


def purpose_of(text: str) -> str:
    match = _PURPOSE.search(text.strip())
    return match.group(1).strip() if match else text.strip()


def plan_tool(text: str, role: str) -> tuple[str, dict[str, Any]] | None:
    """Which tool the words call for, or None for a plain reply."""
    lowered = text.lower()
    if "stop conversation" in lowered or re.search(r"\b(goodbye|bye)\b", lowered):
        return "stop_conversation", {}
    if "recall" in lowered:
        match = _RECALL.search(lowered)
        return "lookup_recall", {"recall_number": match.group(1) if match else "26639"}
    if any(word in lowered for word in ("upload", "photo", "document", "letter", "showed")):
        return "read_last_upload", {}
    if "grant" in lowered or "rule:" in lowered:
        match = _GRANT.search(lowered)
        return "explain_grant", {"grant_id": match.group(1) if match else "rule:self"}
    if any(word in lowered for word in ("status", "waiting", "balance", "what happened")):
        return "household_status", {}
    amount = amount_in(text)
    if "flight" in lowered:  # outside the voice tool surface on purpose: this is how the hook veto is exercised
        return "propose_action", {"action_type": "flight:claim", "amount": amount or "", "purpose": purpose_of(text)}
    if "email" in lowered or "e-mail" in lowered:
        match = _EMAIL.search(text)
        return "propose_action", {"action_type": "email:send", "amount": "", "purpose": text.strip(), "recipient": match.group(0) if match else ""}
    if amount is not None:
        action = "allowance:transfer" if role == "minor" or "allowance" in lowered else "payment:transfer"
        return "propose_action", {"action_type": action, "amount": amount, "purpose": purpose_of(text)}
    return None


def results_of(tool_result: dict[str, Any]) -> list[dict[str, Any]]:
    """The JSON a tool returned, or {"error": text} for a cancelled call (the hook's veto text is not JSON)."""
    found: list[dict[str, Any]] = []
    for item in tool_result.get("content", []):
        text = item.get("text")
        if not text:
            continue
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            value = {"error": text}
        if isinstance(value, dict):
            found.append(value)
    return found


def say_from(results: list[dict[str, Any]]) -> str:
    said: list[str] = []
    for result in results:
        text = str(result.get("say") or result.get("error") or "")
        if text.startswith("vetoed: "):
            text = "I can't do that by voice: " + text[len("vetoed: "):] + "."
        if text:
            said.append(text)
    return " ".join(said) or NO_TOOL_REPLY


def _tone(ms: int = 100, hz: int = 440, amplitude: int = 6000) -> bytes:
    count = 16000 * ms // 1000
    return struct.pack(f"<{count}h", *(int(amplitude * math.sin(2 * math.pi * hz * i / 16000)) for i in range(count)))


TONE_CHUNK_B64 = base64.b64encode(_tone()).decode("ascii")


def _tool_use_id() -> str:
    return f"tooluse_{uuid.uuid4().hex[:24]}"


def _last_user_text(messages: Messages) -> str:
    for message in reversed(messages):
        if message["role"] == "user":
            text = " ".join(block.get("text", "") for block in message["content"] if "text" in block).strip()
            if text:
                return text
    return ""


def _last_tool_results(messages: Messages) -> list[dict[str, Any]]:
    if not messages or messages[-1]["role"] != "user":
        return []
    found: list[dict[str, Any]] = []
    for block in messages[-1]["content"]:
        if "toolResult" in block:
            found.extend(results_of(block["toolResult"]))
    return found


# Text fallback


class VoiceFakeModel(Model):
    """A deterministic Strands Model for the voice prompt: scripts one tool call from the words, then speaks `say`."""

    def __init__(self) -> None:
        self._config: dict[str, Any] = {"model_id": FAKE_TEXT_MODEL_ID}
        self.calls: list[dict[str, Any]] = []

    def update_config(self, **model_config: Any) -> None:
        self._config.update(model_config)

    def get_config(self) -> dict[str, Any]:
        return self._config

    async def structured_output(self, output_model: type, prompt: Messages, system_prompt: str | None = None, **kwargs: Any) -> AsyncGenerator[dict[str, Any], None]:
        raise NotImplementedError("the voice fake speaks; it has no structured output")
        yield  # pragma: no cover

    async def stream(
        self,
        messages: Messages,
        tool_specs: list[ToolSpec] | None = None,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterable[StreamEvent]:
        _, role = member_from_prompt(system_prompt)
        names = {spec["name"] for spec in (tool_specs or [])}
        results = _last_tool_results(messages)
        planned = None if results else plan_tool(_last_user_text(messages), role)
        self.calls.append({"role": role, "planned": planned[0] if planned else None, "results": len(results)})
        yield {"messageStart": {"role": "assistant"}}
        if planned is not None and planned[0] in names:
            name, args = planned
            yield {"contentBlockStart": {"start": {"toolUse": {"name": name, "toolUseId": _tool_use_id()}}}}
            yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(args)}}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}
        else:
            text = say_from(results) if results else NO_TOOL_REPLY
            yield {"contentBlockStart": {"start": {}}}
            yield {"contentBlockDelta": {"delta": {"text": text}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "end_turn"}}
        yield {"metadata": {"usage": {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0}, "metrics": {"latencyMs": 0}}}


# Bidi (Sonic-shaped)


class FakeSonicModel:
    """A BidiModel shaped like Nova Sonic, offline. Half a second of audio is taken as one spoken turn and transcribed
    to `audio_transcript`; text input is its own transcript. `fail` makes start() raise, to rehearse the fallback."""

    def __init__(self, audio_transcript: str = FAKE_AUDIO_TRANSCRIPT, min_audio_bytes: int = MIN_AUDIO_BYTES, fail: Exception | None = None) -> None:
        self.model_id = FAKE_SONIC_MODEL_ID
        self.config: dict[str, Any] = {"audio": {"input_rate": 16000, "output_rate": 16000, "channels": 1, "format": "pcm", "voice": "fake"}}
        self.audio_transcript = audio_transcript
        self.min_audio_bytes = min_audio_bytes
        self.fail = fail
        self._connection_id: str | None = None
        self._queue: asyncio.Queue[Any] | None = None
        self._role = "adult"
        self._tools: set[str] = set()
        self._audio_bytes = 0
        self._turn_open = True
        self._last_audio_at = 0.0

    async def start(self, system_prompt: str | None = None, tools: list[ToolSpec] | None = None, messages: Messages | None = None, **kwargs: Any) -> None:
        if self.fail is not None:
            raise self.fail
        if self._connection_id:
            raise RuntimeError("model already started | call stop before starting again")
        self._connection_id = uuid.uuid4().hex
        self._queue = asyncio.Queue()
        _, self._role = member_from_prompt(system_prompt)
        self._tools = {spec["name"] for spec in (tools or [])}
        self._audio_bytes, self._turn_open = 0, True

    async def send(self, content: Any) -> None:
        if not self._connection_id or self._queue is None:
            raise RuntimeError("model not started | call start before sending")
        if isinstance(content, BidiTextInputEvent):
            await self._user_turn(content.text)
        elif isinstance(content, BidiAudioInputEvent):
            now = time.monotonic()
            if not self._turn_open and now - self._last_audio_at >= TURN_GAP_SECONDS:
                self._turn_open, self._audio_bytes = True, 0  # silence, then speech again: a new utterance
            self._last_audio_at = now
            if not self._turn_open:
                return  # the rest of an utterance already transcribed
            self._audio_bytes += len(base64.b64decode(content.audio))
            if self._audio_bytes >= self.min_audio_bytes:
                self._turn_open = False
                await self._user_turn(self.audio_transcript)
        elif isinstance(content, ToolResultEvent):
            await self._speak(say_from(results_of(content.get("tool_result") or {})))
        else:
            raise ValueError(f"content_type={type(content)} | content not supported")

    async def receive(self) -> AsyncGenerator[Any, None]:
        if not self._connection_id or self._queue is None:
            raise RuntimeError("model not started | call start before receiving")
        yield BidiConnectionStartEvent(connection_id=self._connection_id, model=self.model_id)
        while True:
            event = await self._queue.get()
            if event is None:
                return
            yield event

    async def stop(self) -> None:
        if self._queue is not None:
            await self._queue.put(None)
        self._connection_id = None

    async def _user_turn(self, text: str) -> None:
        assert self._queue is not None
        response_id = uuid.uuid4().hex[:8]
        await self._queue.put(BidiResponseStartEvent(response_id=response_id))
        await self._queue.put(BidiTranscriptStreamEvent(delta={"text": text}, text=text, role="user", is_final=True, current_transcript=text))
        planned = plan_tool(text, self._role)
        if planned is not None and planned[0] in self._tools:
            name, args = planned
            tool_use_id = _tool_use_id()
            await self._queue.put(ToolUseStreamEvent(
                delta={"toolUse": {"toolUseId": tool_use_id, "name": name, "input": json.dumps(args)}},
                current_tool_use={"toolUseId": tool_use_id, "name": name, "input": args},
            ))
            await self._queue.put(BidiResponseCompleteEvent(response_id=response_id, stop_reason="tool_use"))
            return
        await self._speak(NO_TOOL_REPLY, response_id, started=True)

    async def _speak(self, text: str, response_id: str | None = None, started: bool = False) -> None:
        assert self._queue is not None
        response_id = response_id or uuid.uuid4().hex[:8]
        if not started:
            await self._queue.put(BidiResponseStartEvent(response_id=response_id))
        await self._queue.put(BidiTranscriptStreamEvent(delta={"text": text}, text=text, role="assistant", is_final=True, current_transcript=text))
        for _ in range(3):
            await self._queue.put(BidiAudioStreamEvent(audio=TONE_CHUNK_B64, format="pcm", sample_rate=16000, channels=1))
        await self._queue.put(BidiResponseCompleteEvent(response_id=response_id, stop_reason="complete"))
