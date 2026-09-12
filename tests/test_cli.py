import json

from household.cli import main


def test_cli_run_renders_the_trace_decisions_and_briefing(capsys) -> None:
    assert main(["run", "--request", "kofi-allowance-8", "--actor", "kofi", "--provider", "fake"]) == 0
    out = capsys.readouterr().out
    assert "Roster trace: intake -> matcher -> planner -> authority -> executor -> briefer" in out
    assert "ALLOW [minor-allowance]" in out and "OUTCOME: EXECUTED" in out and "Briefing [EN]" in out
    assert "0 overrides" in out


def test_cli_json_carries_events_and_result_with_trace_on_stderr(capsys) -> None:
    assert main(["run", "--request", "fixtures/requests/kofi-allowance-40.json", "--actor", "kofi", "--provider", "fake", "--trace", "--json"]) == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert "> intake run 1 started" in captured.err and "> intake" not in captured.out
    approvals = [e for e in data["events"] if e["event"] == "approval_needed"]
    assert approvals and set(approvals[0]["approver_ids"]) == {"ama", "daniel"}
    assert data["result"]["outcome"] == "needs-approval" and data["result"]["guard"]["overrides"] == 0


def test_cli_lists_and_graph(capsys) -> None:
    for cmd in (["fixtures"], ["providers"], ["roster"], ["skills"], ["graph"]):
        assert main(cmd) == 0
    out = capsys.readouterr().out
    assert "kofi-allowance-8" in out and "fake" in out and "can reject" in out and "allowance" in out
    assert out.count("-->") >= 9 and "flowchart LR" in out
