"""Fixture collections: request fixtures (the hero path and the guardrail census), fixture documents (text extracted
from real public forms and labelled synthetic letters, reused by vision/PDF intake), canned fake-provider outputs per
request and node, and seed households."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import ROOT
from .model import Household

FIXTURES_DIR = ROOT / "fixtures"


# Documents (text intake; the vision packet adds images)


@dataclass(frozen=True)
class Expected:
    document_class: str
    stakes: str
    must_escalate: bool


@dataclass(frozen=True)
class FixtureDocument:
    id: str
    title: str
    source: str
    source_url: str | None
    notes: str
    summary_en: str
    text: str
    expected: Expected
    tags: tuple[str, ...] = ()

    @property
    def is_real(self) -> bool:
        return self.source.startswith("real")


# Requests


@dataclass(frozen=True)
class RequestExpected:
    kind: str  # in-scope | out-of-scope
    skill_id: str = ""
    allow: tuple[str, ...] = ()
    block: tuple[str, ...] = ()
    approval_by: tuple[str, ...] = ()


@dataclass(frozen=True)
class RequestFixture:
    id: str
    actor_member_id: str
    request: str
    household_id: str = "demo"
    channel: str = "text"  # text | voice-transcript | photo
    document_id: str | None = None
    overrides: dict[str, str] = field(default_factory=dict)
    expected: RequestExpected = field(default_factory=lambda: RequestExpected("in-scope"))
    tags: tuple[str, ...] = ()
    notes: str = ""

    @staticmethod
    def from_raw(raw: dict[str, Any]) -> RequestFixture:
        exp = raw.get("expected", {})
        return RequestFixture(
            id=raw["id"],
            actor_member_id=raw["actor_member_id"],
            request=raw["request"],
            household_id=raw.get("household_id", "demo"),
            channel=raw.get("channel", "text"),
            document_id=raw.get("document_id"),
            overrides=dict(raw.get("overrides", {})),
            expected=RequestExpected(
                kind=exp.get("kind", "in-scope"),
                skill_id=exp.get("skill_id", ""),
                allow=tuple(exp.get("allow", [])),
                block=tuple(exp.get("block", [])),
                approval_by=tuple(exp.get("approval_by", [])),
            ),
            tags=tuple(raw.get("tags", [])),
            notes=raw.get("notes", ""),
        )


def adhoc_request(text: str, actor_member_id: str, household_id: str = "demo") -> RequestFixture:
    """Wrap typed text (from the UI, the CLI or an AgentCore payload) as a one-off request."""
    return RequestFixture(id="adhoc", actor_member_id=actor_member_id, request=text, household_id=household_id,
                          tags=("adhoc",), notes="Typed at the session; not stored as a fixture.")


class FixtureStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or FIXTURES_DIR
        self._docs: dict[str, FixtureDocument] | None = None
        self._requests: dict[str, RequestFixture] | None = None

    # Documents

    def _load_docs(self) -> dict[str, FixtureDocument]:
        if self._docs is None:
            docs: dict[str, FixtureDocument] = {}
            for path in sorted((self.root / "documents").glob("*.json")):
                raw = json.loads(path.read_text(encoding="utf-8"))
                exp = raw["expected"]
                doc = FixtureDocument(
                    id=raw["id"],
                    title=raw["title"],
                    source=raw["source"],
                    source_url=raw.get("source_url"),
                    notes=raw.get("notes", ""),
                    summary_en=raw["summary_en"],
                    text=raw["text"],
                    expected=Expected(exp["document_class"], exp["stakes"], bool(exp["must_escalate"])),
                    tags=tuple(raw.get("tags", [])),
                )
                if doc.id != path.stem:
                    raise ValueError(f"fixture id {doc.id!r} must match filename {path.name}")
                docs[doc.id] = doc
            self._docs = docs
        return self._docs

    def list(self) -> list[FixtureDocument]:
        return list(self._load_docs().values())

    def with_adhoc(self, document: FixtureDocument) -> FixtureStore:
        """A view of this store that also resolves one session-only document by its id."""
        view = FixtureStore(self.root)
        view._docs = {**self._load_docs(), document.id: document}
        view._requests = self._requests
        return view

    def get(self, fixture_id: str) -> FixtureDocument:
        try:
            return self._load_docs()[fixture_id]
        except KeyError as exc:
            raise KeyError(f"unknown document fixture {fixture_id!r}") from exc

    # Requests

    def _load_requests(self) -> dict[str, RequestFixture]:
        if self._requests is None:
            found: dict[str, RequestFixture] = {}
            for path in sorted((self.root / "requests").glob("*.json")):
                raw = json.loads(path.read_text(encoding="utf-8"))
                fixture = RequestFixture.from_raw(raw)
                if fixture.id != path.stem:
                    raise ValueError(f"request id {fixture.id!r} must match filename {path.name}")
                found[fixture.id] = fixture
            self._requests = found
        return self._requests

    def requests(self) -> list[RequestFixture]:
        return list(self._load_requests().values())

    def request(self, request_id: str) -> RequestFixture:
        try:
            return self._load_requests()[request_id]
        except KeyError as exc:
            raise KeyError(f"unknown request fixture {request_id!r}; run `household fixtures` to list them") from exc

    def request_from(self, source: str, actor_member_id: str | None = None) -> RequestFixture:
        """A fixture id, a path to a request JSON file, or raw text (needs an actor)."""
        path = Path(source)
        if path.suffix == ".json" and path.exists():
            fixture = RequestFixture.from_raw(json.loads(path.read_text(encoding="utf-8")))
            return fixture if actor_member_id is None else RequestFixture(**{**fixture.__dict__, "actor_member_id": actor_member_id})
        if source in self._load_requests():
            fixture = self.request(source)
            return fixture if actor_member_id is None else RequestFixture(**{**fixture.__dict__, "actor_member_id": actor_member_id})
        if actor_member_id is None:
            raise KeyError(f"unknown request {source!r} and no actor given for ad-hoc text")
        return adhoc_request(source, actor_member_id)

    # Canned fake-provider outputs

    def canned(self, request_id: str, node_id: str, run: int = 1) -> dict[str, Any] | None:
        """fixtures/canned/<request>.<node>.json for the first run; <request>.<node>.<run>.json for later runs."""
        name = f"{request_id}.{node_id}.json" if run <= 1 else f"{request_id}.{node_id}.{run}.json"
        path = self.root / "canned" / name
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def canned_files(self) -> list[Path]:
        return sorted((self.root / "canned").glob("*.json"))

    # Households

    def household(self, household_id: str = "demo") -> Household:
        """A fresh, validated copy of a seed household. Never the working ledger in data/."""
        path = self.root / "households" / f"{household_id}.json"
        if not path.exists():
            raise KeyError(f"unknown household {household_id!r}; no seed at {path}")
        return Household.model_validate(json.loads(path.read_text(encoding="utf-8")))
