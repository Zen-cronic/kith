"""Exercise real Strands loops and entrypoints without paid provider requests."""

import asyncio
from datetime import UTC, datetime

import pytest
from strands import Agent, ModelRetryStrategy
from strands.types.exceptions import ModelThrottledException

from household.config import Settings, load_settings
from household.fixtures import FixtureStore
from household.pipeline import run_session, stream_session
from household.providers.budget import BudgetedModel, CallBudget, ModelCallLimitExceeded
from household.providers.fake import FakeModel
from household.providers.live import build_live_model

NOW = datetime(2026, 9, 11, 16, 0, tzinfo=UTC)


def demo():
    return FixtureStore().household("demo")


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, "20"])
def test_invalid_limits_are_rejected(limit):
    with pytest.raises(ValueError, match="positive integer"):
        Settings(max_model_calls=limit)
    with pytest.raises(ValueError, match="positive integer"):
        CallBudget(limit)


def test_server_setting_cannot_be_disabled(monkeypatch):
    monkeypatch.setenv("MAX_MODEL_CALLS", "3")
    assert load_settings(provider="fake").max_model_calls == 3
    assert load_settings(provider="fake", max_model_calls=9).max_model_calls == 9
    monkeypatch.setenv("MAX_MODEL_CALLS", "invalid")
    assert load_settings(provider="fake", max_model_calls=9).max_model_calls == 9
    monkeypatch.setenv("MAX_MODEL_CALLS", "0")
    with pytest.raises(ValueError):
        load_settings(provider="fake")


@pytest.mark.parametrize("request_id,outcome", [("kofi-allowance-8", "executed"), ("kofi-allowance-40", "needs-approval")])
def test_normal_graph_uses_one_shared_allowance_across_six_node_models(request_id, outcome):
    model = FakeModel()
    result = run_session(request_id, settings=Settings(), model=model, household=demo(), now=NOW)
    assert result.outcome == outcome
    assert result.model_calls == {"limit": 20, "attempted": len(model.calls), "remaining": 20 - len(model.calls), "exhausted": False}
    # Tool-result turns are model calls too, so node executions alone undercount.
    assert len(model.calls) > len(result.execution_order)


def test_limit_stops_graph_before_dispatch_and_no_final_result():
    model = FakeModel()
    events = []

    async def collect():
        async for event in stream_session("kofi-allowance-8", settings=Settings(max_model_calls=3), model=model, household=demo(), now=NOW):
            events.append(event)

    with pytest.raises(ModelCallLimitExceeded) as error:
        asyncio.run(collect())
    assert len(model.calls) == 3
    assert error.value.usage == {"limit": 3, "attempted": 3, "remaining": 0, "exhausted": True}
    assert error.value.as_event()["code"] == "model_call_limit"
    assert not any(e["event"] == "result" for e in events)
    assert any(e["event"] == "node_done" and e["node_id"] == "planner" for e in events)


def test_two_budgeted_models_share_one_call_budget():
    model = FakeModel()
    budget = CallBudget(1)
    first, second = BudgetedModel(model, budget), BudgetedModel(model, budget)

    async def exercise():
        await Agent(model=first, callback_handler=None).invoke_async("One call")
        await Agent(model=second, callback_handler=None).invoke_async("Another call")

    with pytest.raises(ModelCallLimitExceeded):
        asyncio.run(exercise())
    assert len(model.calls) == 1 and first.snapshot() == second.snapshot() == {"limit": 1, "attempted": 1, "remaining": 0, "exhausted": True}


def test_failed_strands_retries_consume_allowance():
    class ThrottledModel(FakeModel):
        attempts = 0

        async def stream(self, *args, **kwargs):
            self.attempts += 1
            raise ModelThrottledException("Synthetic throttle")
            yield  # async generator protocol

    model = ThrottledModel()
    metered = BudgetedModel(model, 2)
    agent = Agent(model=metered, callback_handler=None, retry_strategy=ModelRetryStrategy(max_attempts=10, initial_delay=0, max_delay=0))
    with pytest.raises(ModelCallLimitExceeded):
        asyncio.run(agent.invoke_async("Do not retry past the allowance"))
    assert model.attempts == 2 and metered.snapshot()["attempted"] == 2


def test_concurrent_runs_do_not_share_or_reset_each_others_allowance():
    async def collect(limit):
        model = FakeModel()
        events = []
        try:
            async for event in stream_session("kofi-allowance-8", settings=Settings(max_model_calls=limit), model=model, household=demo(), now=NOW):
                events.append(event)
        except ModelCallLimitExceeded:
            return len(model.calls), False
        return len(model.calls), events[-1]["event"] == "result"

    async def together():
        return await asyncio.gather(collect(1), collect(20), collect(1))

    results = asyncio.run(together())
    assert results[0] == results[2] == (1, False)
    assert results[1][1] is True and 1 < results[1][0] <= 20


def test_provider_legacy_api_cannot_bypass_counter():
    with pytest.raises(NotImplementedError, match="metered stream"):
        BudgetedModel(FakeModel(), 1).structured_output(dict, [])


def test_owned_provider_clients_have_no_hidden_retries(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-key")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-key")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "synthetic-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "synthetic-key")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    async def inspect_clients():
        anthropic = build_live_model(Settings(provider="anthropic"))
        assert anthropic.client.max_retries == 0
        await anthropic.client.close()
        openai = build_live_model(Settings(provider="openai"))
        # This pinned provider creates its client per stream, unlike Anthropic.
        async with openai._get_client() as client:
            assert client.max_retries == 0

    asyncio.run(inspect_clients())
    model = build_live_model(Settings(provider="bedrock", bedrock_model_id="synthetic-model"))
    assert model.client.meta.config.retries == {"mode": "standard", "total_max_attempts": 1}


def test_node_models_are_parsed_and_ignored_in_fake_mode(monkeypatch):
    monkeypatch.setenv("NODE_MODELS", "intake=bedrock:us.amazon.nova-pro-v1:0, planner=anthropic:claude-opus-5")
    settings = load_settings(provider="fake")
    assert settings.node_models == {"intake": "bedrock:us.amazon.nova-pro-v1:0", "planner": "anthropic:claude-opus-5"}
    model = FakeModel()
    result = run_session("kofi-allowance-8", settings=settings, model=model, household=demo(), now=NOW)
    assert result.outcome == "executed" and {c["role"] for c in model.calls} >= {"intake", "planner"}
    monkeypatch.setenv("NODE_MODELS", "intake=nova")
    with pytest.raises(ValueError, match="provider:model_id"):
        load_settings(provider="fake")
