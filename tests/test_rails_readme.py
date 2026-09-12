"""The README "What is real" block regenerates deterministically from RAILS and only touches its own markers."""

from household.cli import main
from household.config import ROOT, Settings
from household.executor import RAILS, readme


def test_block_is_deterministic_and_covers_every_rail() -> None:
    block = readme.render_block()
    assert block == readme.render_block()
    assert block.startswith(readme.START) and block.endswith(readme.END)
    for rail, meta in RAILS.items():
        assert f"(`{rail}`)" in block and meta["name"] in block and meta["label"] in block
        assert all(f"`{reason}`" in block for reason in meta["reasons"])
    for label in ("`COMPLETE`", "`PREPARE-ONLY`", "`SIMULATED-replay`", "`SIMULATED`"):
        assert label in block
    assert "household rails --write-readme" in block


def test_write_readme_replaces_only_the_block(tmp_path) -> None:
    target = tmp_path / "README.md"
    prose = "Sections marked `<!-- rails:start -->` and `<!-- guardrails:start -->` are regenerated; do not hand-edit them.\n"
    target.write_text("# Title\n\n" + prose + "\n<!-- rails:start -->\nstale\n<!-- rails:end -->\n\n## After\n\nkeep me\n", encoding="utf-8")
    assert readme.write_readme(target) is True
    text = target.read_text(encoding="utf-8")
    assert "stale" not in text and text.startswith("# Title\n\n" + prose + "\n") and text.endswith("\n\n## After\n\nkeep me\n")
    assert readme.render_block() in text
    assert readme.write_readme(target) is False and target.read_text(encoding="utf-8") == text
    assert not (tmp_path / "README.tmp").exists()


def test_write_readme_appends_a_section_when_the_markers_are_missing(tmp_path) -> None:
    target = tmp_path / "README.md"
    target.write_text("# Title\n", encoding="utf-8")
    assert readme.write_readme(target) is True
    text = target.read_text(encoding="utf-8")
    assert text.startswith("# Title\n\n## What is real\n\n" + readme.START) and text.endswith(readme.END + "\n")
    assert readme.write_readme(target) is False


def test_committed_readme_is_current() -> None:
    assert readme.render_block() in (ROOT / "README.md").read_text(encoding="utf-8")


def modes(text: str) -> dict[str, str]:
    return {line.split()[0]: line.split()[1] for line in text.splitlines()[1:]}


def test_current_labels_reflect_the_environment() -> None:
    simulated = readme.current_labels(Settings())
    assert simulated.startswith("In this environment (EXECUTION_MODE=simulated, SES_FROM unset, 0 verified SES identities")
    assert modes(simulated) == {"ses-email": "SIMULATED", "internal-ledger": "SIMULATED", "stripe-test": "SIMULATED",
                                "official-form": "PREPARE-ONLY", "external-api-readonly": "SIMULATED-replay"}
    assert "replayed CPSC record fetched 2026-09-12" in simulated
    live = readme.current_labels(Settings(execution_mode="live", ses_from="ops@example.test", ses_verified_identities=("ops@example.test",), stripe_secret_key_present=True))
    assert modes(live) == {"ses-email": "COMPLETE", "internal-ledger": "COMPLETE", "stripe-test": "COMPLETE",
                           "official-form": "PREPARE-ONLY", "external-api-readonly": "COMPLETE"}


def test_cli_rails_prints_the_table_and_the_current_labels(capsys, monkeypatch) -> None:
    monkeypatch.setenv("EXECUTION_MODE", "simulated")
    assert main(["rails"]) == 0
    out = capsys.readouterr().out
    assert "(`ses-email`)" in out and "EXECUTION_MODE=simulated" in out and "SIMULATED-replay" in out and "README" not in out.splitlines()[-1]
