import asyncio

from household.config import load_settings
from household.pipeline import stream_session


def test_stream_emits_roster_events_in_execution_order() -> None:
    async def collect() -> list[dict]:
        return [e async for e in stream_session("school-trip-letter", "es", settings=load_settings(provider="fake"))]

    events = asyncio.run(collect())
    assert events[0]["event"] == "session_start" and len(events[0]["roster"]) == 5
    starts = [e["node_id"] for e in events if e["event"] == "node_start"]
    dones = [(e["node_id"], e["run"], e["status"]) for e in events if e["event"] == "node_done"]
    assert starts == ["reader", "interpreter", "drafter", "critic", "drafter", "critic", "router"]
    assert dones[4] == ("drafter", 2, "completed") and all(s == "completed" for _, _, s in dones)
    assert events[-1]["event"] == "result" and events[-1]["result"].outcome == "proceed"
    critic_outputs = [e["output"] for e in events if e["event"] == "node_done" and e["node_id"] == "critic"]
    assert [o["decision"] for o in critic_outputs] == ["revise", "approve"]
