import json

from household.fixtures import FIXTURES_DIR, FixtureStore
from household.schemas import CriticVerdict, DocumentReading, Draft, Interpretation, NextStepCard

SCHEMAS = {"reader": DocumentReading, "interpreter": Interpretation, "drafter": Draft, "drafter_revised": Draft,
           "critic": CriticVerdict, "critic_final": CriticVerdict, "router": NextStepCard}


def test_fixtures_load_and_have_metadata() -> None:
    docs = FixtureStore().list()
    assert len(docs) >= 3
    for doc in docs:
        assert doc.text.strip() and doc.summary_en.strip()
        assert doc.expected.stakes in {"high", "medium", "low"}
        assert doc.source.startswith(("real", "synthetic"))


def test_canned_outputs_validate_against_schemas() -> None:
    for path in sorted((FIXTURES_DIR / "canned").glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        for key, schema in SCHEMAS.items():
            if key in raw:
                schema.model_validate(raw[key])
