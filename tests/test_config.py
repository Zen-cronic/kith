from household.config import load_settings, resolve_auto
from household.doctor import run_doctor


def test_auto_resolves_to_fake_without_keys(monkeypatch) -> None:
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "MODEL_PROVIDER"):
        monkeypatch.delenv(key, raising=False)
    assert resolve_auto() == "fake"
    s = load_settings(provider="auto")
    assert s.requested_provider == "auto" and s.provider == "fake"


def test_auto_prefers_anthropic_then_openai(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert resolve_auto() == "openai"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
    assert resolve_auto() == "anthropic"
    assert load_settings(provider="auto").provider == "anthropic"


def test_doctor_in_fake_mode_makes_no_call() -> None:
    report = run_doctor(load_settings(provider="fake"))
    assert report.resolved_provider == "fake" and report.ping_ok is None
    assert "no network" in report.render()
