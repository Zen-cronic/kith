"""One tool-call veto for every channel. `AuthorityHook` registers the same callback for the text agents'
`BeforeToolCallEvent` and the voice agent's `BidiBeforeToolCallEvent`, so a spoken request can never reach a tool the
typed path would refuse. It vetoes by cancelling the call (`cancel_tool`), which the SDK turns into an error tool
result the model must read back; it never raises, because an exception inside a bidi tool ends the conversation.

The hook is not the authority: `authority.decide` still judges every proposal. The hook only keeps the model inside
the tool surface the session was given (an allowlist, a per-session call budget, and an optional guard on inputs)."""

from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from typing import Any

from strands.experimental.hooks.events import BidiBeforeToolCallEvent
from strands.hooks import BeforeToolCallEvent, HookProvider, HookRegistry

VETO_PREFIX = "vetoed"
DEFAULT_MAX_CALLS = 40

Guard = Callable[[str, dict[str, Any]], str | None]  # (tool name, tool input) -> reason to veto, or None


@dataclass(frozen=True)
class ToolVeto:
    tool: str
    reason: str
    channel: str  # "text" | "voice"


@dataclass
class AuthorityHook(HookProvider):
    """Veto tool calls outside the session's allowlist, guard or call budget, on both text and voice agents."""

    allowed: Collection[str]
    guard: Guard | None = None
    max_calls: int = DEFAULT_MAX_CALLS
    on_veto: Callable[[ToolVeto], None] | None = None
    calls: int = 0
    vetoes: list[ToolVeto] = field(default_factory=list)

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self.before_text_tool)
        registry.add_callback(BidiBeforeToolCallEvent, self.before_voice_tool)

    def before_text_tool(self, event: BeforeToolCallEvent) -> None:
        self._veto(event, "text")

    def before_voice_tool(self, event: BidiBeforeToolCallEvent) -> None:
        self._veto(event, "voice")

    def reason_for(self, name: str, tool_input: dict[str, Any]) -> str | None:
        """The veto reason for one call, or None when it may run. Counts the call against the budget."""
        self.calls += 1
        if name not in self.allowed:
            return f"{name} is not a tool this conversation may call"
        if self.calls > self.max_calls:
            return f"this conversation reached its budget of {self.max_calls} tool calls"
        if self.guard is not None:
            return self.guard(name, tool_input)
        return None

    def _veto(self, event: BeforeToolCallEvent | BidiBeforeToolCallEvent, channel: str) -> None:
        name = str(event.tool_use.get("name", ""))
        tool_input = event.tool_use.get("input")
        reason = self.reason_for(name, tool_input if isinstance(tool_input, dict) else {})
        if reason is None:
            return
        veto = ToolVeto(tool=name, reason=reason, channel=channel)
        self.vetoes.append(veto)
        event.cancel_tool = f"{VETO_PREFIX}: {reason}"
        if self.on_veto is not None:
            self.on_veto(veto)
