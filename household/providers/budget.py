"""One request allowance shared by every model user within a document run."""

from __future__ import annotations

from collections.abc import AsyncIterator
from threading import Lock
from typing import Any

from strands.models import Model


class ModelCallLimitExceeded(RuntimeError):
    """No final result may be released after the session exhausts its allowance."""

    def __init__(self, usage: dict[str, Any]) -> None:
        self.usage = usage
        super().__init__(f"This reading reached its {usage['limit']}-call model limit. Ask staff for help before trying again.")

    def as_event(self) -> dict[str, Any]:
        return {"event": "error", "code": "model_call_limit", "detail": str(self), "model_calls": self.usage}


class BudgetedModel(Model):
    """Count attempts before dispatch, including nested agents and Strands retries.

    Create once per stream_session, never cache across visitors. Owned live provider
    clients disable their own retries; arbitrary injected models may have internal
    behavior this stream boundary cannot observe. This is not a token/dollar cap.
    """

    def __init__(self, model: Model, limit: int) -> None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("MAX_MODEL_CALLS must be a positive integer")
        self.model = model
        self.limit = limit
        self._attempted = 0
        self._exhausted = False
        self._lock = Lock()

    @property
    def stateful(self) -> bool:
        return self.model.stateful

    def get_config(self) -> Any:
        return self.model.get_config()

    def update_config(self, **model_config: Any) -> None:
        self.model.update_config(**model_config)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"limit": self.limit, "attempted": self._attempted,
                    "remaining": self.limit - self._attempted, "exhausted": self._exhausted}

    def raise_if_exhausted(self) -> None:
        usage = self.snapshot()
        if usage["exhausted"]:
            raise ModelCallLimitExceeded(usage)

    async def stream(self, messages: Any, tool_specs: Any = None, system_prompt: str | None = None, **kwargs: Any) -> AsyncIterator[Any]:
        with self._lock:
            if self._attempted >= self.limit:
                self._exhausted = True
            else:
                self._attempted += 1
            blocked = self._exhausted
        if blocked:
            self.raise_if_exhausted()
        # Failed/cancelled attempts are deliberately not refunded.
        async for event in self.model.stream(messages, tool_specs=tool_specs, system_prompt=system_prompt, **kwargs):
            yield event

    def structured_output(self, *args: Any, **kwargs: Any) -> Any:
        # The project uses Agent(structured_output_model=...), which traverses stream.
        # Delegating this deprecated provider API could hide uncounted inner requests.
        raise NotImplementedError("Use Agent(structured_output_model=...) through the metered stream")
