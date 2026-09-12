"""Fixture documents (real public forms with extracted text, plus labelled synthetic letters) and canned demo outputs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import ROOT

FIXTURES_DIR = ROOT / "fixtures"


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


class FixtureStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or FIXTURES_DIR
        self._docs: dict[str, FixtureDocument] | None = None

    def _load(self) -> dict[str, FixtureDocument]:
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
        return list(self._load().values())

    def with_adhoc(self, document: FixtureDocument) -> FixtureStore:
        """A view of this store that also resolves one session-only document by its id."""
        view = FixtureStore(self.root)
        view._docs = {**self._load(), document.id: document}
        return view

    def get(self, fixture_id: str) -> FixtureDocument:
        try:
            return self._load()[fixture_id]
        except KeyError as exc:
            raise KeyError(f"unknown fixture {fixture_id!r}; run `household fixtures` to list them") from exc

    def canned(self, fixture_id: str, language: str) -> dict[str, Any] | None:
        path = self.root / "canned" / f"{fixture_id}.{language}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
