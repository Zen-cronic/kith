import asyncio
from datetime import UTC, datetime

from household.config import load_settings
from household.fixtures import FixtureStore
from household.pipeline import stream_session

FAKE = load_settings(provider="fake")
NOW = datetime(2026, 9, 11, 16, 0, tzinfo=UTC)


def collect(request_id: str) -> list[dict]:
    household = FixtureStore().household("demo")

    async def run() -> list[dict]:
        return [e async for e in stream_session(request_id, settings=FAKE, household=household, now=NOW)]

    return asyncio.run(run())


def test_stream_emits_roster_action_and_receipt_events_in_execution_order() -> None:
    events = collect("kofi-allowance-8")
    assert events[0]["event"] == "session_start" and len(events[0]["roster"]) == 6
    assert [s["id"] for s in events[0]["skills"]] == ["allowance", "benefits", "education", "recall", "household"]
    kinds = [e["event"] for e in events]
    assert kinds == ["session_start", "node_start", "node_done", "node_start", "node_done", "node_start", "node_done",
                     "node_start", "node_done", "action", "node_start", "node_done", "receipt", "node_start", "node_done", "result"]
    starts = [e["node_id"] for e in events if e["event"] == "node_start"]
    assert starts == ["intake", "matcher", "planner", "authority", "executor", "briefer"]
    dones = [(e["node_id"], e["run"], e["status"]) for e in events if e["event"] == "node_done"]
    assert all(status == "completed" for _, _, status in dones) and dones[0] == ("intake", 1, "completed")
    action = next(e for e in events if e["event"] == "action")
    assert action["decision"]["outcome"] == "allow" and action["decision"]["rule_id"] == "minor-allowance"
    assert action["explanation"].startswith("Allowed under rule:minor-allowance")
    receipt = next(e for e in events if e["event"] == "receipt")
    assert receipt["receipt"]["action_id"] == action["proposal"]["id"] and receipt["receipt"]["mode"] == "SIMULATED"
    assert events[-1]["event"] == "result" and events[-1]["result"].outcome == "executed"
    authority_output = next(e["output"] for e in events if e["event"] == "node_done" and e["node_id"] == "authority")
    assert authority_output["verdict"] == "proceed" and authority_output["decisions"][0]["grant_id"] == "rule:minor-allowance"


def test_needs_approval_surfaces_the_approvers_and_never_starts_the_executor() -> None:
    events = collect("kofi-allowance-40")
    kinds = [e["event"] for e in events]
    assert "receipt" not in kinds and "executor" not in [e.get("node_id") for e in events]
    approval = next(e for e in events if e["event"] == "approval_needed")
    assert set(approval["approver_ids"]) == {"ama", "daniel"} and approval["revision"] == 1
    assert "above" in " ".join(approval["reasons"])
    assert events[-1]["result"].outcome == "needs-approval"


def test_revise_loop_streams_two_plan_revisions_with_their_decisions() -> None:
    events = collect("daniel-payment-450")
    starts = [e["node_id"] for e in events if e["event"] == "node_start"]
    assert starts == ["intake", "matcher", "planner", "authority", "planner", "authority", "executor", "briefer"]
    actions = [(e["revision"], e["proposal"]["amount"], e["decision"]["outcome"]) for e in events if e["event"] == "action"]
    assert actions == [(1, "450.00", "needs-approval"), (2, "300.00", "allow"), (2, "150.00", "needs-approval")]
    approvals = [e for e in events if e["event"] == "approval_needed"]
    assert len(approvals) == 1 and approvals[0]["approver_ids"] == ["daniel"] and approvals[0]["revision"] == 2
    allowed_id = next(e["proposal"]["id"] for e in events if e["event"] == "action" and e["proposal"]["amount"] == "300.00")
    assert [e["receipt"]["action_id"] for e in events if e["event"] == "receipt"] == [allowed_id]
