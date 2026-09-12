"""Non-secret Runtime handshake. A configuration snapshot is not authentication."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from typing import Any

from .config import Settings
from .fixtures import FixtureStore

PROTOCOL_VERSION = 1


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def fixture_digest() -> str:
    return digest([asdict(d) for d in sorted(FixtureStore().list(), key=lambda d: d.id)])


def runtime_metadata(settings: Settings) -> dict[str, Any]:
    values = {
        "protocol_version": PROTOCOL_VERSION,
        "provider": settings.provider,
        "model_id": settings.model_id,
        "max_model_calls": settings.max_model_calls,
        "memory_enabled": bool(os.environ.get("AGENTCORE_MEMORY_ID")),
        "fixture_digest": fixture_digest(),
    }
    # Region/graph settings also invalidate consent without exposing resource identifiers.
    values["config_token"] = digest({**values, "region": settings.aws_region,
                                    "floor": settings.fidelity_floor, "caution": settings.fidelity_caution,
                                    "revisions": settings.max_revisions})
    return values
