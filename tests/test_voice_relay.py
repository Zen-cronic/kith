"""The web-side voice relay: frame validation on the browser -> Runtime pump, transparent Runtime -> browser
forwarding, the opening session_open frame, and close propagation. No AWS and no real sockets."""

import asyncio
import json
from contextlib import asynccontextmanager

from household.fixtures import FixtureStore
from household.web import voice_relay
from household.web.runtime import RuntimeTarget


class FakeClosed(Exception):
    pass


class Channel:
    """An in-memory duplex endpoint: `recv` drains `incoming`, `send` appends to `sent`, `close` ends both."""

    def __init__(self, incoming: list[str]) -> None:
        self._incoming = list(incoming)
        self.sent: list[str] = []
        self.closed = False

    async def recv(self) -> str:
        await asyncio.sleep(0)
        if self._incoming:
            return self._incoming.pop(0)
        self.closed = True
        raise FakeClosed()

    async def send(self, text: str) -> None:
        if self.closed:
            raise FakeClosed()
        self.sent.append(text)

    # Starlette-flavoured aliases the relay uses on the browser side.
    async def receive_text(self) -> str:
        return await self.recv()

    async def send_text(self, text: str) -> None:
        await self.send(text)

    async def close(self) -> None:
        self.closed = True


def test_pump_validates_and_drops_bad_frames_then_forwards_good_ones():
    good = json.dumps({"type": "bidi_text_input", "text": "hi"})
    source = Channel([good, "{not json", good])
    dest = Channel([])
    reports = Channel([])
    reason = asyncio.run(voice_relay.pump(source.recv, dest.send, validate=True, report=reports.send))
    assert reason == "source-closed"
    assert dest.sent == [good, good]  # the invalid frame is never forwarded
    assert json.loads(reports.sent[0])["code"] == "bad_json"


def test_relay_opens_with_session_open_and_forwards_both_directions(monkeypatch):
    household = FixtureStore().household("demo")
    member = household.member("kofi")
    browser = Channel([json.dumps({"type": "bidi_text_input", "text": "eight dollars"}), json.dumps({"type": "stop"})])
    runtime = Channel([json.dumps({"type": "identified", "member": {"id": "kofi"}}),
                       json.dumps({"type": "bidi_connection_close", "reason": "complete"})])

    @asynccontextmanager
    async def fake_connect(target, session_id):
        yield runtime

    monkeypatch.setattr(voice_relay, "connect_runtime", fake_connect)
    target = RuntimeTarget("agentcore", "arn:aws:bedrock-agentcore:us-east-1:1:runtime/household_preview-x")
    asyncio.run(voice_relay.relay_voice_session(browser, target, household, member))

    opener = json.loads(runtime.sent[0])
    assert opener["type"] == "session_open" and opener["actor_member_id"] == "kofi"
    assert opener["household_snapshot"]["id"] == household.id
    # Browser frames reached the Runtime after the opener; Runtime frames reached the browser.
    forwarded = [json.loads(m)["type"] for m in runtime.sent[1:]]
    assert "bidi_text_input" in forwarded and "stop" in forwarded
    assert [json.loads(m)["type"] for m in browser.sent] == ["identified", "bidi_connection_close"]
