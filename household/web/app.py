"""FastAPI app for the household screen: members identify with a PIN, read the ledger, ask for something, watch the
six-node session stream, approve or decline queued actions, and read receipts whose labels come from the executor.

Everything that decides is code that already exists: `pipeline.stream_session` runs the graph, `authority.decide`
decides, `pipeline.execute_approved` verifies a PIN and executes. This module only routes, validates and shapes.
"""

from __future__ import annotations

import asyncio
import atexit
import hashlib
import hmac
import inspect
import json
import os
import secrets
import shutil
import tempfile
import time
import uuid
from contextlib import aclosing
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import authority, executor
from ..agents.graph import describe_graph
from ..agents.roster import ROSTER
from ..config import Settings, load_settings, provider_readiness
from ..executor import rails as rail_functions
from ..executor.readme import INTRO as RAILS_INTRO
from ..executor.receipts import label_for
from ..fixtures import FixtureStore, RequestFixture, adhoc_request
from ..model import (
    ActionRecord,
    AuthorityDecision,
    ConsentRecord,
    Household,
    Member,
    Receipt,
    as_utc,
    consent_proof,
    parse_iso,
)
from ..pipeline import execute_approved, stream_session
from ..providers.budget import ModelCallLimitExceeded
from ..skills import ALL_SKILLS
from ..store import JsonLedgerStore
from ..voice.web import build_router as build_voice_router
from .runtime import RuntimeFailure, remote_metadata, remote_session, runtime_target

STATIC = Path(__file__).parent / "static"
HOUSEHOLD_ID = os.environ.get("HOUSEHOLD_ID", "demo")
SESSION_TTL_SECONDS = 8 * 60 * 60
UPLOAD_MAX_BYTES = 5 * 1024 * 1024
UPLOAD_KINDS: dict[str, tuple[bytes, ...]] = {
    "png": (b"\x89PNG\r\n\x1a\n",),
    "jpeg": (b"\xff\xd8\xff",),
    "gif": (b"GIF87a", b"GIF89a"),
    "webp": (b"RIFF",),
    "pdf": (b"%PDF",),
}
# Photo and PDF runs belong to the vision packet; this branch passes `upload=` only once that parameter exists.
UPLOAD_SUPPORTED = "upload" in inspect.signature(stream_session).parameters
# The demo household is fictional; its PINs are published so a visitor can identify as each member. Each hint is
# checked against the seed's PBKDF2 hash before it is shown, so the hint can never drift from the ledger.
DEMO_PINS = {"ama": "2468", "daniel": "1357", "kofi": "1111", "mei": "2222"}


# Request bodies


class IdentifyRequest(BaseModel):
    member_id: str
    pin: str


class RunRequest(BaseModel):
    actor_member_id: str
    session_token: str
    request_text: str | None = None
    upload_id: str | None = None
    fixture_id: str | None = None


class ApprovalRequest(BaseModel):
    approver_member_id: str
    pin: str


# Session tokens: HMAC over member id + expiry with a per-process secret. Not a login system: a restart signs
# everyone out, which is the right behaviour for a kiosk demo.


class SessionSigner:
    def __init__(self) -> None:
        self.secret = secrets.token_bytes(32)

    def issue(self, member_id: str, now: float | None = None) -> tuple[str, int]:
        expires = int(now if now is not None else time.time()) + SESSION_TTL_SECONDS
        return f"{member_id}.{expires}.{self._sign(member_id, expires)}", expires

    def member_of(self, token: str | None, now: float | None = None) -> str | None:
        parts = (token or "").split(".")
        if len(parts) != 3 or not parts[1].isdigit():
            return None
        member_id, expires, signature = parts[0], int(parts[1]), parts[2]
        if expires <= (now if now is not None else time.time()):
            return None
        if not hmac.compare_digest(self._sign(member_id, expires), signature):
            return None
        return member_id

    def _sign(self, member_id: str, expires: int) -> str:
        return hmac.new(self.secret, f"{member_id}|{expires}".encode(), hashlib.sha256).hexdigest()


# Views: what the browser sees. Never a PIN hash or salt; provider references are masked.


def mask(value: str | None, keep: int = 8) -> str | None:
    if not value:
        return value
    return value if len(value) <= keep else value[:keep] + "…"


def member_view(member: Member) -> dict[str, Any]:
    return {
        "id": member.id, "name": member.name, "role": member.role, "language": member.language,
        "guardians": list(member.guardians), "email": member.email, "birth_year": member.birth_year,
        "has_pin": bool(member.pin_hash),
    }


def grant_state(grant: Any, now: datetime) -> str:
    if grant.status == "revoked":
        return "revoked"
    if now >= parse_iso(grant.expires_at):
        return "expired"
    return "active"


def receipt_view(receipt: Receipt) -> dict[str, Any]:
    return {
        "id": receipt.id, "action_id": receipt.action_id, "rail": receipt.rail, "mode": receipt.mode,
        "label_reason": receipt.label_reason, "provider_ref": mask(receipt.provider_ref), "at": receipt.at,
        "executed_under_grant": receipt.executed_under_grant, "request_digest": mask(receipt.request_digest, 12),
    }


def decision_view(decision: AuthorityDecision) -> dict[str, Any]:
    return {**decision.model_dump(mode="json"), "explanation": authority.explain(decision)}


def action_status(record: ActionRecord) -> str:
    if record.receipt is not None:
        return "executed"
    latest = record.decisions[-1] if record.decisions else None
    if latest is None:
        return "proposed"
    if latest.outcome == "needs-approval":
        return "needs-approval"
    if latest.outcome == "allow":
        return "allowed"
    if (latest.grant_id or "").startswith("declined:"):
        return "declined"
    return "blocked"


def action_view(record: ActionRecord) -> dict[str, Any]:
    p = record.proposal
    latest = record.decisions[-1] if record.decisions else None
    return {
        "id": p.id, "skill_id": p.skill_id, "action_type": p.action_type, "rail": p.rail,
        "actor_member_id": p.actor_member_id, "subject_member_id": p.subject_member_id, "recipient": p.recipient,
        "amount": p.amount, "currency": p.currency, "rationale": p.rationale, "payload": dict(p.payload),
        "claimed_grant_id": p.claimed_grant_id, "created_at": record.created_at, "status": action_status(record),
        "decision": decision_view(latest) if latest else None,
        "decisions": [decision_view(d) for d in record.decisions],
        "receipt": receipt_view(record.receipt) if record.receipt else None,
    }


def household_view(household: Household, now: datetime, demo_pins: dict[str, str]) -> dict[str, Any]:
    return {
        "id": household.id, "name": household.name, "jurisdiction": household.jurisdiction,
        "currency": household.currency, "self_confirm_limit": household.self_confirm_limit,
        "members": [member_view(m) for m in household.members],
        "grants": [
            {**g.model_dump(mode="json"), "state": grant_state(g, now)} for g in household.grants
        ],
        "consents": [c.model_dump(mode="json", exclude={"proof"}) for c in household.consents],
        "accounts": [a.model_dump(mode="json") for a in household.accounts],
        "actions": [action_view(a) for a in reversed(household.actions)],
        "receipts": [receipt_view(r) for r in reversed(household.receipts)],
        "ledger": [e.model_dump(mode="json") for e in reversed(household.ledger)],
        "demo_pins": demo_pins,
    }


def rails_view(settings: Settings) -> dict[str, Any]:
    """The 'What is real' panel: the executor's own rail metadata plus the label each rail earns right now."""
    items = []
    for rail, meta in executor.RAILS.items():
        context: dict[str, Any] = {}
        if rail == "external-api-readonly":
            context = {"fetched": rail_functions.recorded_fetch_date(rail_functions.cpsc_fixture_path("26639"))}
        label = label_for(rail, settings, context)
        items.append({
            "id": rail, "name": meta["name"], "label": meta["label"], "real": meta["real"],
            "reasons": list(meta["reasons"]), "now": {"mode": label.mode, "reason": label.reason},
        })
    return {"intro": RAILS_INTRO, "execution_mode": settings.execution_mode, "items": items}


def fixture_view(fixture: RequestFixture, household: Household) -> dict[str, Any]:
    actor = household.member(fixture.actor_member_id)
    return {
        "id": fixture.id, "actor_member_id": fixture.actor_member_id,
        "actor_name": actor.name if actor else fixture.actor_member_id, "request": fixture.request,
        "channel": fixture.channel, "tags": list(fixture.tags), "notes": fixture.notes,
        "expected": {"kind": fixture.expected.kind, "skill_id": fixture.expected.skill_id,
                     "allow": list(fixture.expected.allow), "approval_by": list(fixture.expected.approval_by)},
    }


def sniff_kind(head: bytes) -> str | None:
    for kind, magics in UPLOAD_KINDS.items():
        if any(head.startswith(magic) for magic in magics):
            if kind == "webp" and head[8:12] != b"WEBP":
                continue
            return kind
    return None


def normalized(text: str) -> str:
    return " ".join(text.split()).strip().lower()


def sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def reset_enabled() -> bool:
    return os.environ.get("HOUSEHOLD_ALLOW_RESET", "").strip() == "1"


# App


def create_app(data_dir: str | os.PathLike[str] | None = None) -> FastAPI:
    app = FastAPI(title="Kith", version="0.2.0")
    root = Path(data_dir or os.environ.get("HOUSEHOLD_DATA_DIR") or JsonLedgerStore().root)
    ledger = JsonLedgerStore(root=root)
    signer = SessionSigner()
    uploads = Path(tempfile.mkdtemp(prefix="household-uploads-"))
    atexit.register(shutil.rmtree, uploads, True)
    lock = asyncio.Lock()  # one ledger, one writer at a time: a run, an approval, a decline or a reset
    verified_pins: dict[str, dict[str, str]] = {}

    def demo_pins(household: Household) -> dict[str, str]:
        if household.id != "demo":
            return {}
        if household.id not in verified_pins:
            verified_pins[household.id] = {
                m.id: DEMO_PINS[m.id] for m in household.members if m.id in DEMO_PINS and m.verify_pin(DEMO_PINS[m.id])
            }
        return verified_pins[household.id]

    def load_household() -> Household:
        return ledger.load(HOUSEHOLD_ID)

    def require_session(token: str | None, member_id: str) -> None:
        if signer.member_of(token) != member_id:
            raise HTTPException(status_code=401, detail="Identify with your PIN first; this screen's session has ended.")

    def session_dir(token: str) -> Path:
        return uploads / hashlib.sha256(token.encode()).hexdigest()[:16]

    def approver_for(household: Household, body: ApprovalRequest) -> Member:
        approver = household.member(body.approver_member_id)
        if approver is None:
            raise HTTPException(status_code=404, detail=f"unknown member {body.approver_member_id!r}")
        if approver.role == "minor":
            raise HTTPException(status_code=403, detail=f"{approver.name} is a minor and cannot approve or decline for anyone.")
        if not approver.verify_pin(body.pin):
            raise HTTPException(status_code=403, detail=f"That PIN does not match {approver.name}'s.")
        return approver

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/api/meta")
    async def meta() -> dict[str, Any]:
        settings = load_settings()
        try:
            target = runtime_target()
            remote = await remote_metadata(target) if target.backend != "local" else None
        except RuntimeFailure as exc:
            raise HTTPException(status_code=503, detail=exc.detail) from exc
        return {
            "provider": remote["provider"] if remote else settings.provider,
            "model_id": remote["model_id"] if remote else settings.model_id,
            "max_model_calls": remote["max_model_calls"] if remote else settings.max_model_calls,
            "providers": {} if remote else provider_readiness(),
            "backend": target.backend,
            "consent_token": target.consent_token(remote) if remote else None,
            "sdk": "Strands Agents SDK (strands-agents 1.54.0)",
            "execution_mode": settings.execution_mode,
            "household_id": HOUSEHOLD_ID,
            "roster": [{"id": s.id, "name": s.name, "job": s.job, "can_reject": s.can_reject, "tools": list(s.tools)} for s in ROSTER],
            "skills": [{"id": s.id, "name": s.name, "action_types": list(s.action_types)} for s in ALL_SKILLS],
            "rails": rails_view(settings),
            "upload": {"available": UPLOAD_SUPPORTED, "kinds": sorted(UPLOAD_KINDS), "max_bytes": UPLOAD_MAX_BYTES},
            "reset_enabled": reset_enabled(),
            "graph_mermaid": describe_graph(settings),
        }

    @app.get("/api/household")
    def household_state() -> dict[str, Any]:
        household = load_household()
        return household_view(household, datetime.now(UTC), demo_pins(household))

    @app.get("/api/fixtures")
    def fixtures() -> list[dict[str, Any]]:
        household = load_household()
        return [fixture_view(f, household) for f in FixtureStore().requests()]

    @app.get("/api/receipts")
    def receipts() -> list[dict[str, Any]]:
        household = load_household()
        by_id = {a.proposal.id: a for a in household.actions}
        out = []
        for receipt in reversed(household.receipts):
            record = by_id.get(receipt.action_id)
            view = receipt_view(receipt)
            if record is not None:
                p = record.proposal
                view.update({"action_type": p.action_type, "amount": p.amount, "currency": p.currency,
                             "subject_member_id": p.subject_member_id, "actor_member_id": p.actor_member_id,
                             "recipient": p.recipient, "rationale": p.rationale})
            out.append(view)
        return out

    @app.post("/api/identify")
    def identify(body: IdentifyRequest) -> dict[str, Any]:
        household = load_household()
        member = household.member(body.member_id)
        if member is None:
            raise HTTPException(status_code=404, detail=f"unknown member {body.member_id!r}")
        if not member.verify_pin(body.pin):
            raise HTTPException(status_code=403, detail=f"That PIN does not match {member.name}'s.")
        token, expires = signer.issue(member.id)
        return {"session_token": token, "expires_at": datetime.fromtimestamp(expires, UTC).isoformat(), "member": member_view(member)}

    @app.post("/api/intake")
    async def intake(file: Annotated[UploadFile, File()], session_token: Annotated[str, Form()]) -> dict[str, Any]:
        member_id = signer.member_of(session_token)
        if member_id is None:
            raise HTTPException(status_code=401, detail="Identify with your PIN first; this screen's session has ended.")
        data = await file.read(UPLOAD_MAX_BYTES + 1)
        if len(data) > UPLOAD_MAX_BYTES:
            raise HTTPException(status_code=413, detail=f"That file is larger than {UPLOAD_MAX_BYTES // (1024 * 1024)} MB.")
        kind = sniff_kind(data[:16])
        if kind is None:
            raise HTTPException(status_code=415, detail="Only PNG, JPEG, GIF, WebP images and PDF files can be read.")
        upload_id = uuid.uuid4().hex
        target = session_dir(session_token)
        target.mkdir(parents=True, exist_ok=True)
        (target / f"{upload_id}.{kind}").write_bytes(data)
        return {"upload_id": upload_id, "filename": file.filename or f"upload.{kind}", "size": len(data), "kind": kind,
                "available": UPLOAD_SUPPORTED}

    @app.post("/api/run")
    async def run(req: RunRequest) -> StreamingResponse:
        given = [name for name in ("request_text", "upload_id", "fixture_id") if getattr(req, name)]
        if req.request_text is not None and not req.request_text.strip():
            given = [name for name in given if name != "request_text"]
        if len(given) != 1:
            raise HTTPException(status_code=400, detail="Send exactly one of request_text, upload_id or fixture_id.")
        require_session(req.session_token, req.actor_member_id)
        settings = load_settings()
        store = FixtureStore()
        household = load_household()
        actor = household.member(req.actor_member_id)
        if actor is None:
            raise HTTPException(status_code=404, detail=f"unknown member {req.actor_member_id!r}")
        upload: Path | None = None
        if req.upload_id:
            if not UPLOAD_SUPPORTED:
                raise HTTPException(status_code=501, detail="Photo and PDF reading is not part of this build yet. Type the request instead.")
            matches = list(session_dir(req.session_token).glob(f"{req.upload_id}.*")) if req.upload_id.isalnum() else []
            if not matches:
                raise HTTPException(status_code=404, detail="That upload is no longer available. Upload the file again.")
            upload = matches[0]
            fixture: RequestFixture = adhoc_request(f"(uploaded {upload.suffix.lstrip('.')} document {upload.name})", actor.id, HOUSEHOLD_ID)
        elif req.fixture_id:
            try:
                fixture = store.request(req.fixture_id)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=f"unknown request fixture {req.fixture_id!r}") from exc
        else:
            text = (req.request_text or "").strip()
            # Typed text that matches a fixture word for word runs as that fixture, so the fake provider has canned
            # outputs for it. Anything else is an ad-hoc request.
            match = next((f for f in store.requests() if normalized(f.request) == normalized(text)), None)
            fixture = match if match is not None else adhoc_request(text, actor.id, HOUSEHOLD_ID)

        async def events():
            try:
                target = runtime_target()
                if target.backend != "local":
                    remote = await remote_metadata(target)
                    payload = {"fixture_id": fixture.id if fixture.id != "adhoc" else None, "request_text": fixture.request,
                               "actor_member_id": actor.id, "language": actor.language}
                    async with aclosing(remote_session(target, payload, remote)) as stream:
                        async for event in stream:
                            yield sse(event)
                    return
                kwargs: dict[str, Any] = {"settings": settings, "store": store, "ledger": ledger, "now": datetime.now(UTC)}
                if upload is not None:
                    kwargs["upload"] = upload
                async with lock:
                    async with aclosing(stream_session(fixture, actor.id, **kwargs)) as stream:
                        async for event in stream:
                            if event["event"] == "result":
                                yield sse({"event": "result", "result": event["result"].model_dump(mode="json", exclude_none=True)})
                            else:
                                yield sse(event)
            except RuntimeFailure as exc:
                yield sse(exc.as_event())
            except ModelCallLimitExceeded as exc:
                yield sse(exc.as_event())
            except Exception as exc:  # surface the failure to the screen instead of a dead stream
                yield sse({"event": "error", "detail": str(exc)})

        return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.post("/api/actions/{action_id}/approve")
    async def approve(action_id: str, body: ApprovalRequest) -> dict[str, Any]:
        settings = load_settings()
        async with lock:
            household = load_household()
            approver = approver_for(household, body)
            try:
                result = execute_approved(household, action_id, approver.id, body.pin, settings=settings,
                                          now=datetime.now(UTC), channel="ui", ledger=ledger)
            except PermissionError as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from exc
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc.args[0] if exc.args else exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            record = household.action(action_id)
        return {
            "action": action_view(record) if record else None,
            "consent": result.consent.model_dump(mode="json", exclude={"proof"}),
            "decision": decision_view(result.decision),
            "receipt": receipt_view(result.receipt) if result.receipt else None,
            "explanation": result.explanation,
        }

    @app.post("/api/actions/{action_id}/decline")
    async def decline(action_id: str, body: ApprovalRequest) -> dict[str, Any]:
        async with lock:
            household = load_household()
            approver = approver_for(household, body)
            record = household.action(action_id)
            if record is None:
                raise HTTPException(status_code=404, detail=f"no action {action_id!r} in household {household.id!r}")
            latest = record.decisions[-1] if record.decisions else None
            if latest is None or latest.outcome != "needs-approval" or record.receipt is not None:
                raise HTTPException(status_code=409, detail=f"{action_id} is not waiting for approval.")
            if approver.id not in latest.approver_ids:
                raise HTTPException(status_code=403, detail=f"{approver.name} is not an approver for this action; it needs {', '.join(latest.approver_ids)}.")
            at = as_utc(datetime.now(UTC)).isoformat()
            consent = ConsentRecord(
                id=f"c-{action_id}-{approver.id}-decline", member_id=approver.id, kind="action-decline", target_id=action_id,
                at=at, channel="ui", proof=consent_proof(approver.id, action_id, at, approver.pin_hash),
            )
            household.consents.append(consent)
            decision = AuthorityDecision(
                action_id=action_id, outcome="block", rule_id=latest.rule_id, grant_id=f"declined:{approver.id}",
                approver_ids=[approver.id],
                reasons=[*latest.reasons, f"declined by {approver.name} (action-decline consent on file)"],
            )
            record.decisions.append(decision)
            ledger.save(household)
        return {
            "action": action_view(record),
            "consent": consent.model_dump(mode="json", exclude={"proof"}),
            "decision": decision_view(decision),
            "receipt": None,
            "explanation": authority.explain(decision),
        }

    @app.post("/api/reset")
    async def reset() -> dict[str, Any]:
        if not reset_enabled():
            raise HTTPException(status_code=403, detail="Reset is disabled. Start the server with HOUSEHOLD_ALLOW_RESET=1 to re-seed the demo household.")
        async with lock:
            household = ledger.reset(HOUSEHOLD_ID)
            shutil.rmtree(uploads, ignore_errors=True)
            uploads.mkdir(parents=True, exist_ok=True)
        return {"reset": True, "household_id": household.id, "members": [m.id for m in household.members]}

    # Voice: the same ledger and upload directory, identified by member id + PIN over WebSocket (P7)
    app.include_router(build_voice_router(store=ledger, uploads_dir=uploads))
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


app = create_app()
