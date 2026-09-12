"""P7 voice: identify by PIN over the socket, the text fallback with a visible banner, the Sonic-shaped path through
the real BidiAgent loop with the offline fake, the shared AuthorityHook veto on a voice-originated tool call, and the
frame contract (allowed types, 32 KB cap). Every websocket test is synchronous through TestClient.websocket_connect."""

from __future__ import annotations

import base64
import json
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from strands.experimental.hooks.events import BidiBeforeToolCallEvent
from strands.hooks import BeforeToolCallEvent, HookRegistry

from household.agents.hooks import AuthorityHook
from household.config import ROOT, Settings, load_settings
from household.store import SEED_DIR, JsonLedgerStore
from household.voice import fake as voice_fake
from household.voice import session as voice_session
from household.voice.devserver import create_app
from household.voice.frames import (
    AUDIO_B64_PER_FRAME,
    MAX_FRAME_BYTES,
    SERVER_TYPES,
    FrameError,
    frame_bytes,
    parse_client_frame,
    split_audio,
)
from household.voice.tools import VOICE_TOOL_NAMES, upload_summary_path

WAV = ROOT / "fixtures" / "audio" / "kofi-allowance.wav"
KOFI_PIN = "1111"
AMA_PIN = "2468"
EIGHT = "Can I have eight dollars from my allowance for the book fair?"
FORTY = "Can I have forty dollars for the science kit?"


@pytest.fixture
def store(tmp_path: Path) -> JsonLedgerStore:
    return JsonLedgerStore(root=tmp_path / "data", seed_dir=SEED_DIR)


def client_for(store: JsonLedgerStore, tmp_path: Path, speech: str = "off") -> TestClient:
    settings = Settings(provider="fake", speech=speech)
    return TestClient(create_app(store, settings, uploads_dir=tmp_path / "uploads"))


def identify(ws: Any, member_id: str, pin: str) -> dict[str, Any]:
    ws.send_json({"type": "identify", "member_id": member_id, "pin": pin})
    frame = ws.receive_json()
    assert frame["type"] == "identified", frame
    return frame


def until(ws: Any, stop: Callable[[dict[str, Any]], bool], limit: int = 300) -> list[dict[str, Any]]:
    """Frames up to and including the first one `stop` accepts. Every frame must honour the server contract."""
    frames: list[dict[str, Any]] = []
    for _ in range(limit):
        frame = ws.receive_json()
        assert frame["type"] in SERVER_TYPES, frame
        assert frame_bytes(frame) <= MAX_FRAME_BYTES, frame["type"]
        frames.append(frame)
        if stop(frame):
            return frames
    raise AssertionError(f"no frame matched after {limit}: {[f['type'] for f in frames][-10:]}")


def turn(ws: Any, text: str) -> list[dict[str, Any]]:
    ws.send_json({"type": "bidi_text_input", "text": text})
    return until(ws, lambda f: (f["type"] == "bidi_response_complete" and f["stop_reason"] == "complete") or f["type"] == "bidi_connection_close")


def tool_done(frames: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return next(f for f in frames if f["type"] == "tool" and f["name"] == name and f["status"] == "done")


def final(frames: list[dict[str, Any]], role: str) -> str:
    return next(f["text"] for f in frames if f["type"] == "bidi_transcript_stream" and f["role"] == role and f["is_final"])


def wav_frames() -> list[dict[str, Any]]:
    with wave.open(str(WAV)) as handle:
        assert (handle.getframerate(), handle.getnchannels(), handle.getsampwidth()) == (16000, 1, 2)
        data = handle.readframes(handle.getnframes())
    return [
        {"type": "bidi_audio_input", "audio": base64.b64encode(data[i:i + 3200]).decode("ascii"), "format": "pcm", "sample_rate": 16000, "channels": 1}
        for i in range(0, len(data), 3200)
    ]


# Identification


def test_three_pin_failures_close_the_socket_with_1008(store: JsonLedgerStore, tmp_path: Path) -> None:
    client = client_for(store, tmp_path)
    with client.websocket_connect("/api/voice") as ws:
        ws.send_json({"type": "bidi_text_input", "text": "hello"})  # before identifying: refused, not counted
        assert ws.receive_json()["code"] == "identify_first"
        ws.send_json({"type": "identify", "member_id": "kofi", "pin": "9999"})
        assert ws.receive_json() == {"type": "identify_failed", "attempts_left": 2, "detail": "That member id and PIN do not match."}
        ws.send_json({"type": "identify", "member_id": "nobody", "pin": KOFI_PIN})  # unknown member counts the same
        assert ws.receive_json()["attempts_left"] == 1
        ws.send_json({"type": "identify", "member_id": "kofi", "pin": "1112"})
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == 1008 and closed.value.reason == "identification failed"


def test_identified_frame_names_the_member_mode_and_tools(store: JsonLedgerStore, tmp_path: Path) -> None:
    client = client_for(store, tmp_path)
    with client.websocket_connect("/api/voice") as ws:
        frame = identify(ws, "kofi", KOFI_PIN)
        assert frame["member"] == {"id": "kofi", "name": "Kofi Okafor-Lim", "role": "minor", "language": "en"}
        assert frame["mode"] == "text" and "SPEECH=off" in frame["banner"]
        assert frame["tools"] == list(VOICE_TOOL_NAMES) and "BidiAgent" in frame["sdk"]
        assert "pin" not in json.dumps(frame)
        ws.send_json({"type": "stop"})
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == 1000
    meta = client.get("/api/voice/meta").json()
    assert meta["speech"] == "off" and [m["id"] for m in meta["members"]] == ["ama", "daniel", "kofi", "mei"]
    assert "pin" not in json.dumps(meta)


def test_unknown_household_closes_with_1008(store: JsonLedgerStore, tmp_path: Path) -> None:
    client = client_for(store, tmp_path)
    with client.websocket_connect("/api/voice?household_id=nobody") as ws:
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == 1008


# Text fallback (SPEECH=off): same tools, same authority, visible banner


def test_text_fallback_minor_within_allowance_executes_with_a_receipt_and_persists(store: JsonLedgerStore, tmp_path: Path) -> None:
    client = client_for(store, tmp_path)
    with client.websocket_connect("/api/voice") as ws:
        identify(ws, "kofi", KOFI_PIN)
        frames = turn(ws, EIGHT)
        assert final(frames, "user") == EIGHT
        started = next(f for f in frames if f["type"] == "tool" and f["status"] == "started")
        assert started["name"] == "propose_action"
        done = tool_done(frames, "propose_action")
        assert done["outcome"] == "allow" and done["rule_id"] == "minor-allowance" and done["grant_id"] == "rule:minor-allowance"
        assert done["receipt"]["mode"] == "SIMULATED" and done["proposal"]["actor_member_id"] == "kofi"
        assert done["proposal"]["amount"] == "8.00" and done["proposal"]["action_type"] == "allowance:transfer"
        assert "Receipt SIMULATED" in final(frames, "assistant") and "{" not in final(frames, "assistant")
    household = store.load("demo")
    record = household.action(done["action_id"])
    assert record is not None and record.receipt is not None and record.receipt.id == done["receipt"]["id"]
    assert household.receipts[-1].action_id == done["action_id"]


def test_text_fallback_minor_above_limit_needs_guardian_approval_and_nothing_moves(store: JsonLedgerStore, tmp_path: Path) -> None:
    client = client_for(store, tmp_path)
    with client.websocket_connect("/api/voice") as ws:
        identify(ws, "kofi", KOFI_PIN)
        frames = turn(ws, FORTY)
        done = tool_done(frames, "propose_action")
        assert done["outcome"] == "needs-approval" and done["approver_ids"] == ["ama", "daniel"]
        assert done["rule_id"] == "minor-guardian" and done["receipt"] is None
        assert "I've asked Ama or Daniel to approve" in final(frames, "assistant")
    household = store.load("demo")
    record = household.action(done["action_id"])
    assert record is not None and record.receipt is None and record.decisions[-1].outcome == "needs-approval"
    assert household.receipts == []


def test_text_fallback_adult_in_scope_request_executes_under_rule_self(store: JsonLedgerStore, tmp_path: Path) -> None:
    client = client_for(store, tmp_path)
    with client.websocket_connect("/api/voice") as ws:
        identify(ws, "ama", AMA_PIN)
        frames = turn(ws, "Pay $45.00 to the school for the field trip")
        done = tool_done(frames, "propose_action")
        assert done["proposal"]["action_type"] == "payment:transfer" and done["proposal"]["rail"] == "internal-ledger"
        assert done["outcome"] == "allow" and done["grant_id"] == "rule:self" and done["receipt"]["mode"] == "SIMULATED"


def test_text_fallback_read_only_tools_and_goodbye(store: JsonLedgerStore, tmp_path: Path) -> None:
    uploads = tmp_path / "uploads"
    upload_summary_path(uploads, "demo").parent.mkdir(parents=True)
    upload_summary_path(uploads, "demo").write_text(json.dumps({
        "title": "a dental statement", "summary_en": "Sun Life paid 120.00 of a 180.00 claim.",
        "amounts": [{"amount_text": "$180.00"}], "dates": [], "at": "2026-09-12T10:00:00-04:00",
    }), encoding="utf-8")
    client = client_for(store, tmp_path)
    with client.websocket_connect("/api/voice") as ws:
        identify(ws, "ama", AMA_PIN)
        status = tool_done(turn(ws, "What is waiting?"), "household_status")
        assert status["waiting_for_me"] == [] and "say" in status
        recall = tool_done(turn(ws, "Look up recall 26639"), "lookup_recall")
        assert recall["found"] is True and recall["summary"]["recall_number"] == "26639" and "nothing was filed" in recall["say"]
        grant = tool_done(turn(ws, "Explain grant g-daniel-ama-benefits"), "explain_grant")
        assert grant["grant_status"] == "active" and grant["status"] == "done" and "Daniel lets Ama" in grant["say"]
        upload = tool_done(turn(ws, "What did the last document say?"), "read_last_upload")
        assert upload["found"] is True and "$180.00" in upload["say"]
        frames = turn(ws, "Okay, goodbye")
        assert tool_done(frames, "stop_conversation")["say"].startswith("Bye Ama")
        assert final(frames, "assistant").startswith("Bye Ama")  # the stop tool ends the loop before a model turn
        assert ws.receive_json() == {"type": "bidi_connection_close", "reason": "user_request"}
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == 1000


# The Sonic-shaped path: the real BidiAgent loop over the offline fake


def test_sonic_path_streams_audio_transcribes_and_runs_the_same_authority(store: JsonLedgerStore, tmp_path: Path) -> None:
    client = client_for(store, tmp_path, speech="on")
    with client.websocket_connect("/api/voice") as ws:
        frame = identify(ws, "kofi", KOFI_PIN)
        assert frame["mode"] == "sonic" and frame["banner"] is None and frame["model"] == "fake-sonic"
        for chunk in wav_frames():
            ws.send_json(chunk)
        frames = until(ws, lambda f: f["type"] == "bidi_response_complete" and f["stop_reason"] == "complete")
        assert frames[0]["type"] == "bidi_connection_start"
        assert final(frames, "user") == voice_fake.FAKE_AUDIO_TRANSCRIPT
        done = tool_done(frames, "propose_action")
        assert done["outcome"] == "allow" and done["receipt"]["mode"] == "SIMULATED"
        audio = [f for f in frames if f["type"] == "bidi_audio_stream"]
        assert audio and all(f["sample_rate"] == 16000 and f["channels"] == 1 and f["format"] == "pcm" for f in audio)
        assert all(frame_bytes(f) <= MAX_FRAME_BYTES for f in audio)
        frames = turn(ws, FORTY)
        done = tool_done(frames, "propose_action")
        assert done["outcome"] == "needs-approval" and done["approver_ids"] == ["ama", "daniel"]
        assert "I've asked Ama or Daniel to approve" in final(frames, "assistant")
        frames = turn(ws, "goodbye")
        assert frames[-1]["type"] == "bidi_connection_close" and frames[-1]["reason"] == "user_request"
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == 1000
    assert [a.decisions[-1].outcome for a in store.load("demo").actions] == ["allow", "needs-approval"]


def test_hook_vetoes_a_voice_originated_out_of_scope_tool_call(store: JsonLedgerStore, tmp_path: Path) -> None:
    client = client_for(store, tmp_path, speech="on")
    with client.websocket_connect("/api/voice") as ws:
        identify(ws, "kofi", KOFI_PIN)
        frames = turn(ws, "Claim my flight for two hundred dollars")
        veto = next(f for f in frames if f["type"] == "tool" and f["status"] == "vetoed")
        assert veto["name"] == "propose_action" and veto["channel"] == "voice" and "flight:claim" in veto["reason"]
        assert not [f for f in frames if f["type"] == "tool" and f["status"] == "done"]
        assert "can't do that by voice" in final(frames, "assistant")
    assert store.load("demo").actions == []


def test_sonic_failure_falls_back_to_text_on_the_same_socket_with_a_banner(store: JsonLedgerStore, tmp_path: Path, monkeypatch) -> None:
    error = ValueError("no AWS credentials found. configure credentials via environment variables, credential files, IAM roles, or SSO.")
    monkeypatch.setattr(voice_session, "build_bidi_model", lambda settings: voice_fake.FakeSonicModel(fail=error))
    client = client_for(store, tmp_path, speech="on")
    with client.websocket_connect("/api/voice") as ws:
        assert identify(ws, "kofi", KOFI_PIN)["mode"] == "sonic"
        fallback = ws.receive_json()
        assert fallback["type"] == "fallback" and fallback["mode"] == "text"
        assert fallback["banner"] == voice_session.FALLBACK_BANNER and fallback["reason"].startswith("ValueError: no AWS credentials")
        frames = turn(ws, EIGHT)
        assert tool_done(frames, "propose_action")["outcome"] == "allow"
        ws.send_json(wav_frames()[0])
        assert ws.receive_json()["code"] == "voice_unavailable"


# The frame contract


def test_client_frames_are_validated_and_capped() -> None:
    with pytest.raises(FrameError, match="at most"):
        parse_client_frame("x" * (MAX_FRAME_BYTES + 1))
    with pytest.raises(FrameError, match="unknown frame type"):
        parse_client_frame(json.dumps({"type": "bidi_image_input", "image": "", "mime_type": "image/png"}))
    with pytest.raises(FrameError, match="base64"):
        parse_client_frame(json.dumps({"type": "bidi_audio_input", "audio": "not base64!"}))
    with pytest.raises(FrameError, match="sample_rate"):
        parse_client_frame(json.dumps({"type": "bidi_audio_input", "audio": "AAAA", "sample_rate": 48000}))
    with pytest.raises(FrameError, match="non-empty"):
        parse_client_frame(json.dumps({"type": "bidi_text_input", "text": "  "}))
    assert parse_client_frame(json.dumps({"type": "identify", "member_id": " Kofi ", "pin": "1111"}))["member_id"] == "kofi"
    assert parse_client_frame('{"type": "stop", "extra": 1}') == {"type": "stop"}


def test_outgoing_audio_is_split_under_the_cap() -> None:
    big = {"type": "bidi_audio_stream", "audio": "A" * (AUDIO_B64_PER_FRAME * 3 + 4), "format": "pcm", "sample_rate": 16000, "channels": 1}
    pieces = split_audio(big)
    assert len(pieces) == 4 and "".join(p["audio"] for p in pieces) == big["audio"]
    assert all(frame_bytes(p) <= MAX_FRAME_BYTES and len(p["audio"]) % 4 == 0 for p in pieces)


# The hook and the settings


def test_authority_hook_registers_for_text_and_voice_and_vetoes_the_same_way() -> None:
    hook = AuthorityHook(("a", "b"), guard=lambda name, inputs: "too big" if inputs.get("amount") == "999" else None, max_calls=3)
    registry = HookRegistry()
    registry.add_hook(hook)
    assert registry.has_callbacks() and BeforeToolCallEvent in registry._registered_callbacks
    assert BidiBeforeToolCallEvent in registry._registered_callbacks
    assert hook.reason_for("a", {}) is None
    assert hook.reason_for("zzz", {}) == "zzz is not a tool this conversation may call"
    assert hook.reason_for("b", {"amount": "999"}) == "too big"
    assert hook.reason_for("a", {}) is not None and "budget" in str(hook.reason_for("a", {}))


def test_voice_settings_come_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_PROVIDER", "fake")
    monkeypatch.setenv("SPEECH", "off")
    monkeypatch.setenv("SONIC_VOICE", "tiffany")
    settings = load_settings()
    assert settings.speech == "off" and settings.sonic_voice == "tiffany" and settings.sonic_model_id == "amazon.nova-2-sonic-v1:0"
    with pytest.raises(ValueError, match="SPEECH"):
        Settings(speech="maybe")
