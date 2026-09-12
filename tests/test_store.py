"""JSON ledger store: seeded from fixtures, atomic writes, re-validated saves, reset."""

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

import household.store as store_module
from household.config import ROOT
from household.model import ActionProposal, ActionRecord, Household, Receipt
from household.store import SEED_DIR, JsonLedgerStore

SEED = ROOT / "fixtures" / "households" / "demo.json"
NOW = datetime(2026, 9, 11, 16, 0, tzinfo=UTC).isoformat()


@pytest.fixture
def store(tmp_path):
    return JsonLedgerStore(root=tmp_path / "data", seed_dir=SEED_DIR)


def test_first_load_seeds_from_fixtures(store: JsonLedgerStore) -> None:
    assert not store.path("demo").exists()
    household = store.load("demo")
    assert store.path("demo").exists()
    assert json.loads(store.path("demo").read_text(encoding="utf-8")) == json.loads(SEED.read_text(encoding="utf-8"))
    assert household == Household.model_validate(json.loads(SEED.read_text(encoding="utf-8")))
    assert store.load("demo") == household


def test_unknown_and_unsafe_household_ids(store: JsonLedgerStore) -> None:
    with pytest.raises(KeyError, match="unknown household"):
        store.load("nobody")
    for unsafe in ("../etc", "Demo", "", "demo.json"):
        with pytest.raises(ValueError, match="household id"):
            store.load(unsafe)


def test_save_is_atomic_when_replace_fails(store: JsonLedgerStore, monkeypatch) -> None:
    household = store.load("demo")
    original = store.path("demo").read_text(encoding="utf-8")
    household.name = "changed"

    def boom(src, dst):
        raise OSError("simulated crash between tmp write and replace")

    monkeypatch.setattr(store_module.os, "replace", boom)
    with pytest.raises(OSError, match="simulated crash"):
        store.save(household)
    assert store.path("demo").read_text(encoding="utf-8") == original
    assert not store.path("demo").with_suffix(".tmp").exists()
    monkeypatch.undo()
    store.save(household)
    assert store.load("demo").name == "changed"
    assert not store.path("demo").with_suffix(".tmp").exists()


def test_save_revalidates_before_writing(store: JsonLedgerStore) -> None:
    household = store.load("demo")
    original = store.path("demo").read_text(encoding="utf-8")
    household.members[0].role = "alien"  # assignment is not validated; the write must be
    with pytest.raises(ValidationError):
        store.save(household)
    assert store.path("demo").read_text(encoding="utf-8") == original


def test_append_action_find_receipt_and_reset(store: JsonLedgerStore) -> None:
    proposal = ActionProposal(id="act-1", skill_id="test", action_type="payment:transfer", rail="internal-ledger",
                              actor_member_id="ama", subject_member_id="daniel", amount="100.00", idempotency_key="k-1")
    store.append_action("demo", ActionRecord(proposal=proposal, created_at=NOW))
    assert store.find_receipt("demo", "k-1") is None
    household = store.load("demo")
    assert [a.proposal.id for a in household.actions] == ["act-1"]
    receipt = Receipt(id="r-1", action_id="act-1", rail="internal-ledger", mode="SIMULATED", request_digest="d",
                      response_digest="", at=NOW, executed_under_grant="g-daniel-ama-payments", label_reason="test")
    household.actions[0].receipt = receipt
    household.receipts.append(receipt)
    store.save(household)
    assert store.find_receipt("demo", "k-1") == receipt
    assert store.load("demo").actions[0].receipt == receipt
    reseeded = store.reset("demo")
    assert reseeded.actions == [] and reseeded.receipts == []
    assert store.find_receipt("demo", "k-1") is None
    assert json.loads(store.path("demo").read_text(encoding="utf-8")) == json.loads(SEED.read_text(encoding="utf-8"))


def test_defaults_point_at_the_repository() -> None:
    default = JsonLedgerStore()
    assert default.root == ROOT / "data" and default.seed_dir == ROOT / "fixtures" / "households"
    assert (default.seed_dir / "demo.json").exists()
