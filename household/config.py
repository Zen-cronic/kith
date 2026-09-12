"""Settings come from the environment (and a gitignored .env). Secrets are never read by anything but the SDK clients."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROVIDERS: tuple[str, ...] = ("fake", "anthropic", "openai", "bedrock")
SELECTABLE: tuple[str, ...] = ("auto", *PROVIDERS)
EXECUTION_MODES: tuple[str, ...] = ("simulated", "live")
SPEECH_MODES: tuple[str, ...] = ("on", "off")
DEFAULT_SONIC_MODEL_ID = "amazon.nova-2-sonic-v1:0"
DEFAULT_SONIC_VOICE = "matthew"
ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    provider: str = "fake"
    requested_provider: str = "fake"
    anthropic_model_id: str = "claude-opus-5"
    openai_model_id: str = "gpt-5"
    bedrock_model_id: str | None = None
    aws_region: str = "us-east-1"
    speech_provider: str = "browser"
    fidelity_floor: float = 0.50
    fidelity_caution: float = 0.70
    max_revisions: int = 2
    max_model_calls: int = 20
    # Per-node model overrides, e.g. {"intake": "bedrock:us.amazon.nova-pro-v1:0"}; empty = the default model everywhere.
    node_models: dict[str, str] = field(default_factory=dict)
    # simulated = every rail returns a SIMULATED receipt with the request digest; live = real rails (P3).
    execution_mode: str = "simulated"
    # Action rails (P3). Presence flags only; the secrets themselves stay in the environment for the SDK clients.
    ses_from: str | None = None
    ses_verified_identities: tuple[str, ...] = ()
    stripe_secret_key_present: bool = False
    aeroapi_key_present: bool = False
    allow_live_ses: bool = False
    # Voice (P7): Nova 2 Sonic over the /api/voice WebSocket. speech=off skips Sonic and serves the text fallback.
    sonic_model_id: str = DEFAULT_SONIC_MODEL_ID
    sonic_voice: str = DEFAULT_SONIC_VOICE
    speech: str = "on"

    def __post_init__(self) -> None:
        if self.execution_mode not in EXECUTION_MODES:
            raise ValueError(f"EXECUTION_MODE must be one of {EXECUTION_MODES}, got {self.execution_mode!r}")
        if isinstance(self.max_model_calls, bool) or not isinstance(self.max_model_calls, int) or self.max_model_calls <= 0:
            raise ValueError("MAX_MODEL_CALLS must be a positive integer")
        if self.execution_mode not in EXECUTION_MODES:
            raise ValueError(f"EXECUTION_MODE must be one of {EXECUTION_MODES}, got {self.execution_mode!r}")
        if self.speech not in SPEECH_MODES:
            raise ValueError(f"SPEECH must be one of {SPEECH_MODES}, got {self.speech!r}")
        for node_id, spec in self.node_models.items():
            if ":" not in spec or spec.split(":", 1)[0] not in PROVIDERS:
                raise ValueError(f"NODE_MODELS entry for {node_id!r} must look like provider:model_id, got {spec!r}")

    @property
    def model_id(self) -> str:
        return {
            "fake": "fake-fixture-model",
            "anthropic": self.anthropic_model_id,
            "openai": self.openai_model_id,
            "bedrock": self.bedrock_model_id or "(strands regional default)",
        }[self.provider]


def parse_node_models(raw: str | None) -> dict[str, str]:
    """NODE_MODELS="intake=bedrock:us.amazon.nova-pro-v1:0,planner=anthropic:claude-opus-5" -> {node: provider:model}."""
    parsed: dict[str, str] = {}
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            raise ValueError(f"NODE_MODELS entry must look like node=provider:model_id, got {item!r}")
        node_id, spec = item.split("=", 1)
        parsed[node_id.strip()] = spec.strip()
    return parsed


def load_settings(env_file: str | os.PathLike[str] | None = None, **overrides: object) -> Settings:
    """Read settings from .env + environment. Explicit keyword overrides win (used by tests and the CLI)."""
    load_dotenv(env_file or ROOT / ".env", override=False)
    requested = str(overrides.pop("provider", None) or os.environ.get("MODEL_PROVIDER", "auto")).strip().lower()
    if requested not in SELECTABLE:
        raise ValueError(f"MODEL_PROVIDER must be one of {SELECTABLE}, got {requested!r}")
    provider = resolve_auto() if requested == "auto" else requested
    execution_mode = str(overrides.pop("execution_mode", None) or os.environ.get("EXECUTION_MODE", "simulated")).strip().lower()
    region = str(overrides.get("aws_region") or os.environ.get("AWS_REGION", "us-east-1"))
    identities = overrides.pop("ses_verified_identities", None)
    if identities is None:  # the SES identity list is fetched once, at startup, and only when a real send is possible
        identities = ses_verified_identities(region) if execution_mode == "live" else ()
    values = {
        "provider": provider,
        "requested_provider": requested,
        "anthropic_model_id": os.environ.get("ANTHROPIC_MODEL_ID", "claude-opus-5"),
        "openai_model_id": os.environ.get("OPENAI_MODEL_ID", "gpt-5"),
        "bedrock_model_id": os.environ.get("BEDROCK_MODEL_ID") or None,
        "aws_region": os.environ.get("AWS_REGION", "us-east-1"),
        "speech_provider": os.environ.get("SPEECH_PROVIDER", "browser"),
        "max_model_calls": overrides.get("max_model_calls") if overrides.get("max_model_calls") is not None else int(os.environ.get("MAX_MODEL_CALLS", "20")),
        "node_models": parse_node_models(os.environ.get("NODE_MODELS")),
        "execution_mode": execution_mode,
        "ses_from": os.environ.get("SES_FROM", "").strip() or None,
        "ses_verified_identities": tuple(identities),
        "stripe_secret_key_present": bool(os.environ.get("STRIPE_SECRET_KEY")),
        "aeroapi_key_present": bool(os.environ.get("AEROAPI_KEY")),
        "allow_live_ses": os.environ.get("HOUSEHOLD_ALLOW_LIVE_SES", "").strip() == "1",
        "sonic_model_id": os.environ.get("SONIC_MODEL_ID", "").strip() or DEFAULT_SONIC_MODEL_ID,
        "sonic_voice": os.environ.get("SONIC_VOICE", "").strip() or DEFAULT_SONIC_VOICE,
        "speech": os.environ.get("SPEECH", "on").strip().lower() or "on",
    }
    values.update({k: v for k, v in overrides.items() if v is not None})
    return Settings(**values)  # type: ignore[arg-type]


def resolve_auto() -> str:
    """auto = the first live provider with a key in the environment, else the fake provider. Bedrock is never
    auto-selected: it needs model access granted in the account, so it is chosen explicitly."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    return "fake"


def sesv2_client(region: str):
    """The SES v2 client, built in one place so tests can substitute a botocore Stubber-wrapped client."""
    import boto3
    from botocore.config import Config

    return boto3.client(
        "sesv2", region_name=region,
        config=Config(connect_timeout=5, read_timeout=20, retries={"total_max_attempts": 1, "mode": "standard"}),
    )


def ses_verified_identities(region: str) -> tuple[str, ...]:
    """Identities SES will send from and, while the account is sandboxed, the only addresses it will send to.
    Called once at startup and only when EXECUTION_MODE=live. Any AWS error yields an empty tuple, so a missing
    credential degrades every email to a SIMULATED receipt instead of crashing the app."""
    from botocore.exceptions import BotoCoreError, ClientError

    names: set[str] = set()
    token: str | None = None
    try:
        client = sesv2_client(region)
        while True:
            page = client.list_email_identities(**({"PageSize": 100, "NextToken": token} if token else {"PageSize": 100}))
            names.update(
                item["IdentityName"] for item in page.get("EmailIdentities", [])
                if item.get("SendingEnabled") and item.get("VerificationStatus", "SUCCESS") == "SUCCESS"
            )
            token = page.get("NextToken")
            if not token:
                return tuple(sorted(names))
    except (BotoCoreError, ClientError) as exc:
        logging.getLogger(__name__).warning("SES identities unavailable in %s (%s); email will be SIMULATED", region, type(exc).__name__)
        return ()


def provider_readiness() -> dict[str, dict[str, str | bool]]:
    """Which providers could run right now. Reports presence of keys only, never their values."""
    return {
        "fake": {"ready": True, "why": "deterministic fixture-backed model; no network"},
        "anthropic": {
            "ready": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "why": "ANTHROPIC_API_KEY present" if os.environ.get("ANTHROPIC_API_KEY") else "ANTHROPIC_API_KEY missing",
        },
        "openai": {
            "ready": bool(os.environ.get("OPENAI_API_KEY")),
            "why": "OPENAI_API_KEY present" if os.environ.get("OPENAI_API_KEY") else "OPENAI_API_KEY missing",
        },
        "bedrock": {
            "ready": bool(os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("AWS_PROFILE")),
            "why": "AWS credentials in environment (validity not checked here)"
            if (os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("AWS_PROFILE"))
            else "no AWS credentials in environment",
        },
    }
