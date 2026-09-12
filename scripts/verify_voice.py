"""Prove the voice slice end to end against a running voice server, and write a receipt.

Connects to /api/voice, identifies as a member by id + PIN, and holds a two-turn conversation:
  turn 1: streams fixtures/audio/kofi-allowance.wav ("eight dollars for the book fair") as 100 ms PCM frames, or
          types the same words with --text-only. Expects a user transcript, a propose_action tool event that code
          allowed under the child's allowance rule with a receipt, and (voice mode) bidi_audio_stream frames.
  turn 2: types "forty dollars for the science kit". Expects a propose_action tool event that is needs-approval by
          ama and daniel, and an assistant line that says so ("I've asked Ama ... to approve").
Then sends stop and expects a clean close. A fallback frame (Sonic unavailable) is recorded with its reason, the
banner is checked, and the turns continue as text so the fallback path is proven in the same run.

Speaks only the frame contract; it never imports the household package, so it runs against any checkout or deployment.

  python -m household.voice.devserver --port 8020            # in another shell
  python scripts/verify_voice.py --url ws://127.0.0.1:8020/api/voice [--text-only] [--docs-copy docs/receipts/voice-<date>.json]
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import threading
import time
import wave
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parents[1]
WAV = ROOT / "fixtures" / "audio" / "kofi-allowance.wav"
RECEIPT = ROOT / "runs" / "voice" / "receipt.json"
MAX_FRAME_BYTES = 32 * 1024
FRAME_MS = 100
SILENCE_FRAME = bytes(16000 * 2 * FRAME_MS // 1000)  # an open microphone keeps sending silence so turn detection can fire
TURN_ONE_TEXT = "Can I have eight dollars from my allowance for the book fair?"
TURN_TWO_TEXT = "Can I have forty dollars from my allowance for the science kit?"
SERVER_TYPES = {
    "identified", "identify_failed", "error", "fallback", "tool", "bidi_connection_start", "bidi_connection_close",
    "bidi_connection_restart", "bidi_response_start", "bidi_response_complete", "bidi_audio_stream",
    "bidi_transcript_stream", "bidi_interruption", "bidi_error",
}


def wav_frames(path: Path) -> list[bytes]:
    with wave.open(str(path)) as handle:
        if (handle.getframerate(), handle.getnchannels(), handle.getsampwidth()) != (16000, 1, 2):
            raise SystemExit(f"{path} must be 16 kHz mono 16-bit PCM")
        data = handle.readframes(handle.getnframes())
    step = 16000 * 2 * FRAME_MS // 1000
    return [data[i:i + step] for i in range(0, len(data), step)]


class Run:
    def __init__(self, ws: Any) -> None:
        self.ws = ws
        self.frames: list[dict[str, Any]] = []
        self.checks: list[dict[str, Any]] = []
        self.max_frame_bytes = 0
        self.closed: tuple[int, str] | None = None
        self.send_lock = threading.Lock()
        self.mic_open = threading.Event()
        self.mic_thread: threading.Thread | None = None

    def open_mic(self) -> None:
        """Stream silence frames at real-time pace until close_mic, like a browser microphone between words."""
        self.mic_open.set()

        def pump() -> None:
            while self.mic_open.is_set() and self.closed is None:
                try:
                    self.send_audio(SILENCE_FRAME)
                except ConnectionClosed:
                    return
                time.sleep(FRAME_MS / 1000)

        self.mic_thread = threading.Thread(target=pump, daemon=True)
        self.mic_thread.start()

    def close_mic(self) -> None:
        self.mic_open.clear()
        if self.mic_thread is not None:
            self.mic_thread.join(timeout=2)

    def send_audio(self, chunk: bytes) -> None:
        self.send({"type": "bidi_audio_input", "audio": base64.b64encode(chunk).decode("ascii"), "format": "pcm", "sample_rate": 16000, "channels": 1})

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append({"check": name, "ok": bool(ok), "detail": detail})
        print(f"[{'ok' if ok else 'FAIL'}] {name}{': ' + detail if detail else ''}")
        return bool(ok)

    def send(self, frame: dict[str, Any]) -> None:
        with self.send_lock:
            self.ws.send(json.dumps(frame))

    def recv(self, timeout: float) -> dict[str, Any] | None:
        try:
            raw = self.ws.recv(timeout=timeout)
        except TimeoutError:
            return None
        except ConnectionClosed as exc:
            self.closed = (exc.code if hasattr(exc, "code") else exc.rcvd.code if exc.rcvd else 1006, str(exc))
            return None
        size = len(raw.encode("utf-8") if isinstance(raw, str) else raw)
        self.max_frame_bytes = max(self.max_frame_bytes, size)
        frame = json.loads(raw)
        self.frames.append(frame)
        return frame

    def collect(self, done: Callable[[list[dict[str, Any]]], bool], timeout: float, grace: float = 1.0) -> list[dict[str, Any]]:
        """Frames until `done` holds over what arrived, then a short grace period for trailing audio."""
        start = len(self.frames)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            frame = self.recv(timeout=min(2.0, max(0.1, deadline - time.monotonic())))
            if frame is None and self.closed is not None:
                break
            if done(self.frames[start:]):
                until = time.monotonic() + grace
                while time.monotonic() < until and self.recv(timeout=until - time.monotonic()) is not None:
                    pass
                break
        return self.frames[start:]


def tool_done(frames: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    return next((f for f in frames if f.get("type") == "tool" and f.get("name") == name and f.get("status") == "done"), None)


def finals(frames: list[dict[str, Any]], role: str) -> list[str]:
    return [f["text"] for f in frames if f.get("type") == "bidi_transcript_stream" and f.get("role") == role and f.get("is_final")]


def heard(frames: list[dict[str, Any]]) -> list[str]:
    """Every user transcript, final or not: Nova Sonic reports its own speech recognition without a final flag."""
    return [f["text"] for f in frames if f.get("type") == "bidi_transcript_stream" and f.get("role") == "user" and f.get("text")]


def summarize_turn(label: str, sent: str, frames: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "turn": label,
        "input": sent,
        "user_transcripts": heard(frames),
        "assistant_transcripts": finals(frames, "assistant"),
        "tools": [f for f in frames if f.get("type") == "tool"],
        "audio_frames": sum(1 for f in frames if f.get("type") == "bidi_audio_stream"),
        "audio_bytes": sum(len(base64.b64decode(f["audio"])) for f in frames if f.get("type") == "bidi_audio_stream"),
        "frame_types": sorted({f.get("type", "?") for f in frames}),
    }


def turn_settled(voice: bool) -> Callable[[list[dict[str, Any]]], bool]:
    def done(frames: list[dict[str, Any]]) -> bool:
        if tool_done(frames, "propose_action") is None or not finals(frames, "assistant"):
            return False
        if voice and not any(f.get("type") == "bidi_audio_stream" for f in frames):
            return False
        return frames[-1].get("type") in ("bidi_response_complete", "bidi_connection_close")
    return done


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="ws://127.0.0.1:8020/api/voice")
    parser.add_argument("--member", default="kofi")
    parser.add_argument("--pin", default="1111", help="the demo child's PIN (fixtures/households/demo.json)")
    parser.add_argument("--wav", default=str(WAV))
    parser.add_argument("--text-only", action="store_true", help="type turn 1 instead of streaming the WAV (exercises the text fallback)")
    parser.add_argument("--receipt", default=str(RECEIPT))
    parser.add_argument("--docs-copy", default=None, help="also write the receipt here (e.g. docs/receipts/voice-<date>.json)")
    parser.add_argument("--timeout", type=float, default=60.0, help="seconds to wait for each turn to settle")
    args = parser.parse_args(argv)

    receipt: dict[str, Any] = {
        "status": "failed", "at": datetime.now(UTC).isoformat(), "url": args.url, "member": args.member,
        "text_only": args.text_only, "wav": None if args.text_only else str(Path(args.wav).relative_to(ROOT) if Path(args.wav).is_relative_to(ROOT) else args.wav),
        "checks": [], "turns": [], "fallback": None, "frame_counts": {}, "max_frame_bytes": 0, "closed": None,
    }
    try:
        with connect(args.url, max_size=4 * MAX_FRAME_BYTES, open_timeout=10) as ws:
            run = Run(ws)
            run.send({"type": "identify", "member_id": args.member, "pin": args.pin})
            identified = run.recv(timeout=15)
            if not identified or identified.get("type") != "identified":
                run.check("identified", False, json.dumps(identified))
                raise SystemExit(_finish(receipt, run, args))
            mode = identified["mode"]
            receipt.update({"identified": identified, "mode": mode, "banner": identified.get("banner"), "model": identified.get("model"), "sdk": identified.get("sdk")})
            run.check("identified", identified["member"]["id"] == args.member, f"{identified['member']['name']} ({identified['member']['role']}), mode {mode}, model {identified.get('model')}")
            if args.text_only:
                run.check("text fallback banner", mode == "text" and bool(identified.get("banner")), str(identified.get("banner")))
            else:
                run.check("voice mode offered", mode == "sonic", f"mode {mode}")
            # Sonic connects (bidi_connection_start) or fails (fallback) before the first turn.
            if mode == "sonic":
                first = run.collect(lambda fs: any(f.get("type") in ("bidi_connection_start", "fallback") for f in fs), timeout=30, grace=0)
                fallback = next((f for f in first if f.get("type") == "fallback"), None)
                if fallback is not None:
                    receipt["fallback"] = fallback
                    receipt["mode"] = mode = "text"
                    run.check("sonic connected", False, f"fallback: {fallback.get('reason')}")
                    run.check("fallback banner shown", bool(fallback.get("banner")) and fallback.get("mode") == "text", str(fallback.get("banner")))
                else:
                    run.check("sonic connected", any(f.get("type") == "bidi_connection_start" for f in first), "bidi_connection_start")
            voice = mode == "sonic" and not args.text_only

            # Turn 1: the WAV (or its words). Expected: allow under the allowance rule, with a receipt.
            if voice:
                for chunk in wav_frames(Path(args.wav)):
                    run.send_audio(chunk)
                    time.sleep(FRAME_MS / 1000)
                run.open_mic()
                sent = f"audio {Path(args.wav).name}"
            else:
                run.send({"type": "bidi_text_input", "text": TURN_ONE_TEXT})
                sent = TURN_ONE_TEXT
            frames = run.collect(turn_settled(voice), timeout=args.timeout)
            summary = summarize_turn("1", sent, frames)
            receipt["turns"].append(summary)
            run.check("turn 1: user transcript", bool(summary["user_transcripts"]), " | ".join(summary["user_transcripts"])[:200])
            done = tool_done(frames, "propose_action")
            run.check("turn 1: propose_action decided in code", done is not None, json.dumps({k: done.get(k) for k in ("outcome", "rule_id", "grant_id", "approver_ids")}) if done else "no tool event")
            run.check("turn 1: allowed under rule:minor-allowance with a receipt", bool(done) and done.get("outcome") == "allow" and bool(done.get("receipt")),
                      f"receipt {done['receipt']['mode']} {done['receipt']['id']}" if done and done.get("receipt") else "no receipt")
            run.check("turn 1: assistant spoke, no JSON", bool(summary["assistant_transcripts"]) and "{" not in " ".join(summary["assistant_transcripts"]), " | ".join(summary["assistant_transcripts"])[:200])
            if voice:
                run.check("turn 1: audio frames received", summary["audio_frames"] >= 1, f"{summary['audio_frames']} frames, {summary['audio_bytes']} bytes")

            # Turn 2: above the child's limit. Expected: needs-approval by both guardians.
            run.send({"type": "bidi_text_input", "text": TURN_TWO_TEXT})
            frames = run.collect(turn_settled(voice), timeout=args.timeout)
            summary = summarize_turn("2", TURN_TWO_TEXT, frames)
            receipt["turns"].append(summary)
            done = tool_done(frames, "propose_action")
            run.check("turn 2: needs-approval by ama and daniel", bool(done) and done.get("outcome") == "needs-approval" and set(done.get("approver_ids") or []) >= {"ama", "daniel"},
                      json.dumps({k: done.get(k) for k in ("outcome", "rule_id", "approver_ids")}) if done else "no tool event")
            run.check("turn 2: assistant announced the approval", any("asked" in t.lower() and "approve" in t.lower() for t in summary["assistant_transcripts"]), " | ".join(summary["assistant_transcripts"])[:200])

            run.close_mic()
            run.send({"type": "stop"})
            run.collect(lambda fs: False, timeout=5, grace=0)
            run.check("stop closes the socket cleanly", run.closed is not None and run.closed[0] == 1000, str(run.closed))
            run.check("every frame under 32 KB and of a known type", run.max_frame_bytes <= MAX_FRAME_BYTES and all(f.get("type") in SERVER_TYPES for f in run.frames), f"max {run.max_frame_bytes} bytes, {len(run.frames)} frames")
            return _finish(receipt, run, args)
    except (OSError, ConnectionClosed, TimeoutError) as exc:
        receipt["error"] = f"{type(exc).__name__}: {exc}"
        print(f"[FAIL] connection: {receipt['error']}")
        _write(receipt, args)
        return 1


def _finish(receipt: dict[str, Any], run: Run, args: argparse.Namespace) -> int:
    counts: dict[str, int] = {}
    for frame in run.frames:
        counts[frame.get("type", "?")] = counts.get(frame.get("type", "?"), 0) + 1
    receipt.update({"checks": run.checks, "frame_counts": counts, "max_frame_bytes": run.max_frame_bytes, "closed": run.closed})
    receipt["status"] = "succeeded" if run.checks and all(c["ok"] for c in run.checks) else "failed"
    _write(receipt, args)
    print(f"{receipt['status']}: {sum(c['ok'] for c in run.checks)}/{len(run.checks)} checks; receipt {args.receipt}")
    return 0 if receipt["status"] == "succeeded" else 1


def _write(receipt: dict[str, Any], args: argparse.Namespace) -> None:
    for target in [args.receipt, args.docs_copy]:
        if not target:
            continue
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
