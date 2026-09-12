"""Ledger persistence. The JSON store seeds `data/<id>.json` from `fixtures/households/<id>.json` and writes
atomically (tmp + os.replace) so a crash mid-save can never leave a half-written ledger behind."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Protocol

from .config import ROOT
from .executor import idempotency
from .model import ActionRecord, Household, Receipt

DATA_DIR = ROOT / "data"
SEED_DIR = ROOT / "fixtures" / "households"
HOUSEHOLD_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class LedgerStore(Protocol):
    def load(self, household_id: str) -> Household: ...

    def save(self, household: Household) -> None: ...

    def append_action(self, household_id: str, record: ActionRecord) -> None: ...

    def find_receipt(self, household_id: str, idempotency_key: str) -> Receipt | None: ...


class JsonLedgerStore:
    def __init__(self, root: Path | None = None, seed_dir: Path | None = None) -> None:
        self.root = root or DATA_DIR
        self.seed_dir = seed_dir or SEED_DIR

    def path(self, household_id: str) -> Path:
        if not HOUSEHOLD_ID.match(household_id):
            raise ValueError(f"household id must be lowercase letters, digits and dashes, got {household_id!r}")
        return self.root / f"{household_id}.json"

    def load(self, household_id: str) -> Household:
        path = self.path(household_id)
        if not path.exists():
            seed = self.seed_dir / f"{household_id}.json"
            if not seed.exists():
                raise KeyError(f"unknown household {household_id!r}; no seed at {seed}")
            household = Household.model_validate(json.loads(seed.read_text(encoding="utf-8")))
            self._write(path, household)
            return household
        return Household.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def save(self, household: Household) -> None:
        validated = Household.model_validate(household.model_dump(mode="json"))
        self._write(self.path(validated.id), validated)

    def append_action(self, household_id: str, record: ActionRecord) -> None:
        household = self.load(household_id)
        household.actions.append(record)
        self.save(household)

    def find_receipt(self, household_id: str, idempotency_key: str) -> Receipt | None:
        return idempotency.is_duplicate(idempotency_key, self.load(household_id))

    def reset(self, household_id: str) -> Household:
        """Drop the working copy and re-seed from fixtures. Used by tests and the demo."""
        path = self.path(household_id)
        if path.exists():
            path.unlink()
        return self.load(household_id)

    def _write(self, path: Path, household: Household) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        try:
            temp.write_text(json.dumps(household.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8")
            os.replace(temp, path)
        finally:
            if temp.exists():
                temp.unlink()
