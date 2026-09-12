import household.trapset.harness as harness
from household.config import load_settings
from household.fixtures import FixtureStore
from household.trapset.harness import markdown_table, run_trapset


def test_trapset_has_at_least_twenty_of_each_kind_and_hard_negatives() -> None:
    docs = [d for d in FixtureStore().list() if "trapset" in d.tags]
    must_escalate = [d for d in docs if d.expected.must_escalate]
    must_assist = [d for d in docs if not d.expected.must_escalate]
    assert len(must_escalate) >= 20 and len(must_assist) >= 20
    assert sum("hard-negative" in d.tags for d in must_assist) >= 4
    assert sum("hard-positive" in d.tags for d in must_escalate) >= 2
    assert all(d.source.startswith(("real", "synthetic")) for d in docs)


def test_fake_mode_replay_is_labelled_as_a_pipeline_check_and_scores_the_rule_catalogue() -> None:
    summary = run_trapset(load_settings(provider="fake"), "es")
    assert summary.n >= 40
    # In fake mode the reader's class is taken from fixture metadata, so this checks the catalogue + guard, not a model.
    assert summary.escalated_correctly == summary.must_escalate_n
    assert summary.false_refusals == 0
    table = markdown_table(summary)
    assert "fake" in table and "not a measurement of a model" in table
    assert "false-refusal rate" in table


def test_a_provider_error_on_one_fixture_is_captured_not_fatal(monkeypatch) -> None:
    """A live sweep must survive a single provider/stream error: the fixture is retried, then
    recorded as an ERROR row, excluded from the rates, and surfaced in the table — the run still
    completes and every other fixture is still scored."""
    real_run_session = harness.run_session
    calls = {"n": 0}

    def flaky(doc_id, language, *, settings, store):
        # Fail every attempt for exactly one fixture id; run the rest normally.
        if doc_id == "ltb-n4":
            calls["n"] += 1
            raise RuntimeError("modelStreamErrorException: invalid ToolUse sequence")
        return real_run_session(doc_id, language, settings=settings, store=store)

    monkeypatch.setattr(harness, "run_session", flaky)
    monkeypatch.setattr(harness, "TRAPSET_BACKOFF_SECONDS", 0.0)  # no real sleeping in tests

    summary = run_trapset(load_settings(provider="fake"), "es")
    assert summary.errors == 1
    assert calls["n"] == harness.TRAPSET_MAX_ATTEMPTS  # it was retried, not abandoned on first failure
    assert summary.n >= 40  # the run still completed over every fixture
    errored = [r for r in summary.rows if r.error is not None]
    assert len(errored) == 1 and errored[0].fixture_id == "ltb-n4"
    # The errored (must-escalate) fixture is excluded from the recall denominator, not counted as a miss.
    assert summary.escalated_correctly == summary.must_escalate_n
    assert "not scored" in markdown_table(summary).lower()
