"""household doctor: prove the configured provider works with one cheap structured-output call. Never prints a key."""

from __future__ import annotations

import time
from dataclasses import dataclass

from pydantic import BaseModel
from strands import Agent

from .config import ROOT, Settings, load_settings, provider_readiness
from .providers import build_model


class Ping(BaseModel):
    ok: bool
    model_name_you_believe_you_are: str


@dataclass
class DoctorReport:
    env_file_present: bool
    requested_provider: str
    resolved_provider: str
    model_id: str
    readiness: dict[str, dict[str, str | bool]]
    ping_ok: bool | None
    ping_detail: str
    latency_ms: int | None

    def render(self) -> str:
        lines = [
            f".env present: {self.env_file_present}",
            f"MODEL_PROVIDER requested: {self.requested_provider} -> resolved: {self.resolved_provider} ({self.model_id})",
        ]
        for name, status in self.readiness.items():
            lines.append(f"  {name:10} ready={status['ready']!s:5} {status['why']}")
        if self.ping_ok is None:
            lines.append(f"ping: skipped ({self.ping_detail})")
        else:
            lines.append(f"ping: {'ok' if self.ping_ok else 'FAILED'} in {self.latency_ms} ms - {self.ping_detail}")
        return "\n".join(lines)


def run_doctor(settings: Settings | None = None, ping: bool = True) -> DoctorReport:
    settings = settings or load_settings()
    report = DoctorReport(
        env_file_present=(ROOT / ".env").exists(),
        requested_provider=settings.requested_provider,
        resolved_provider=settings.provider,
        model_id=settings.model_id,
        readiness=provider_readiness(),
        ping_ok=None,
        ping_detail="",
        latency_ms=None,
    )
    if settings.provider == "fake":
        report.ping_detail = "fake provider makes no network calls; run with a key in .env to test a live host"
        return report
    if not ping:
        report.ping_detail = "ping disabled"
        return report
    try:
        model = build_model(settings)
        agent = Agent(model=model, system_prompt="[[role:doctor]] Reply through the Ping tool with ok=true.", structured_output_model=Ping, callback_handler=None, name="doctor")
        t0 = time.perf_counter()
        result = agent("Confirm you are reachable.")
        report.latency_ms = int((time.perf_counter() - t0) * 1000)
        out = result.structured_output
        report.ping_ok = bool(out and isinstance(out, Ping) and out.ok)
        report.ping_detail = f"model says it is {out.model_name_you_believe_you_are!r}" if isinstance(out, Ping) else "no structured output returned"
    except Exception as exc:  # the point of doctor is to show the failure, not hide it
        report.ping_ok = False
        report.ping_detail = f"{type(exc).__name__}: {str(exc)[:300]}"
    return report
