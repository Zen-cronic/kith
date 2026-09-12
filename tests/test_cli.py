from household.cli import main


def test_cli_run_renders_dual_language_frame(capsys) -> None:
    assert main(["run", "--fixture", "ltb-n4", "--lang", "es", "--provider", "fake"]) == 0
    out = capsys.readouterr().out
    assert "Roster trace: reader -> interpreter -> critic -> router" in out
    assert "OUTCOME: ESCALATE" in out
    assert "Fidelity gauge" in out and "Card [ES]" in out


def test_cli_lists(capsys) -> None:
    for cmd in (["fixtures"], ["languages"], ["providers"], ["roster"]):
        assert main(cmd) == 0
    out = capsys.readouterr().out
    assert "ltb-n4" in out and "text-only" in out and "fake" in out and "can reject" in out
