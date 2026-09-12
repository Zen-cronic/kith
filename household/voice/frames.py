"""The WebSocket frame contract for /api/voice. Every frame is one JSON text message of at most 32 KB.

Client -> server: identify {member_id, pin} · bidi_audio_input {audio (base64 PCM), format "pcm", sample_rate 16000,
channels 1} · bidi_text_input {text} · stop {}.
Server -> client: identified · identify_failed · error · fallback · tool · bidi_connection_start ·
bidi_connection_close · bidi_connection_restart · bidi_response_start · bidi_response_complete · bidi_audio_stream ·
bidi_transcript_stream · bidi_interruption · bidi_error. Outgoing audio is split so no frame exceeds the cap."""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any

MAX_FRAME_BYTES = 32 * 1024
AUDIO_FORMAT = "pcm"
AUDIO_SAMPLE_RATE = 16000
AUDIO_CHANNELS = 1
MAX_TEXT_CHARS = 2000
MAX_ID_CHARS = 64
MAX_PIN_CHARS = 32
# base64 characters of audio per outgoing frame: a multiple of 4 that keeps the JSON envelope under MAX_FRAME_BYTES.
AUDIO_B64_PER_FRAME = 24_000

CLIENT_TYPES: frozenset[str] = frozenset({"identify", "bidi_audio_input", "bidi_text_input", "stop"})
SERVER_TYPES: frozenset[str] = frozenset({
    "identified", "identify_failed", "error", "fallback", "tool",
    "bidi_connection_start", "bidi_connection_close", "bidi_connection_restart",
    "bidi_response_start", "bidi_response_complete", "bidi_audio_stream", "bidi_transcript_stream",
    "bidi_interruption", "bidi_error",
})


class FrameError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        self.code, self.detail = code, detail
        super().__init__(detail)

    def as_frame(self) -> dict[str, str]:
        return {"type": "error", "code": self.code, "detail": self.detail}


def frame_bytes(frame: dict[str, Any]) -> int:
    return len(json.dumps(frame, ensure_ascii=False).encode("utf-8"))


def parse_client_frame(raw: str | bytes) -> dict[str, Any]:
    """Validate one incoming frame: size, JSON object, allowed type, and the fields that type carries."""
    data = raw.encode("utf-8") if isinstance(raw, str) else raw
    if len(data) > MAX_FRAME_BYTES:
        raise FrameError("frame_too_large", f"frames must be at most {MAX_FRAME_BYTES} bytes, got {len(data)}")
    try:
        frame = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise FrameError("bad_json", "frames must be JSON text") from exc
    if not isinstance(frame, dict) or not isinstance(frame.get("type"), str):
        raise FrameError("bad_frame", 'frames are JSON objects with a string "type"')
    kind = frame["type"]
    if kind not in CLIENT_TYPES:
        raise FrameError("unknown_type", f"unknown frame type {kind!r}; allowed: {', '.join(sorted(CLIENT_TYPES))}")
    if kind == "identify":
        member_id, pin = frame.get("member_id"), frame.get("pin")
        if not isinstance(member_id, str) or not isinstance(pin, str):
            raise FrameError("bad_identify", "identify needs string member_id and pin")
        if len(member_id) > MAX_ID_CHARS or len(pin) > MAX_PIN_CHARS:
            raise FrameError("bad_identify", "member_id or pin is too long")
        return {"type": kind, "member_id": member_id.strip().lower(), "pin": pin}
    if kind == "bidi_text_input":
        text = frame.get("text")
        if not isinstance(text, str) or not text.strip():
            raise FrameError("bad_text", "bidi_text_input needs a non-empty text")
        if len(text) > MAX_TEXT_CHARS:
            raise FrameError("bad_text", f"text must be at most {MAX_TEXT_CHARS} characters")
        return {"type": kind, "text": text.strip(), "role": "user"}
    if kind == "bidi_audio_input":
        audio = frame.get("audio")
        if not isinstance(audio, str) or not audio:
            raise FrameError("bad_audio", "bidi_audio_input needs base64 audio")
        try:
            base64.b64decode(audio, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise FrameError("bad_audio", "audio is not valid base64") from exc
        if frame.get("format", AUDIO_FORMAT) != AUDIO_FORMAT:
            raise FrameError("bad_audio", f"audio format must be {AUDIO_FORMAT!r}")
        if frame.get("sample_rate", AUDIO_SAMPLE_RATE) != AUDIO_SAMPLE_RATE:
            raise FrameError("bad_audio", f"sample_rate must be {AUDIO_SAMPLE_RATE}")
        if frame.get("channels", AUDIO_CHANNELS) != AUDIO_CHANNELS:
            raise FrameError("bad_audio", f"channels must be {AUDIO_CHANNELS}")
        return {"type": kind, "audio": audio, "format": AUDIO_FORMAT, "sample_rate": AUDIO_SAMPLE_RATE, "channels": AUDIO_CHANNELS}
    return {"type": "stop"}


def split_audio(frame: dict[str, Any]) -> list[dict[str, Any]]:
    """Split an outgoing bidi_audio_stream frame on base64 boundaries so each piece stays under the frame cap."""
    audio = str(frame.get("audio", ""))
    if len(audio) <= AUDIO_B64_PER_FRAME:
        return [frame]
    pieces: list[dict[str, Any]] = []
    for start in range(0, len(audio), AUDIO_B64_PER_FRAME):
        pieces.append({**frame, "audio": audio[start:start + AUDIO_B64_PER_FRAME]})
    return pieces
