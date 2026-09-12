"""Contract and code fallback checks independent of the language-model provider."""

import pytest
from pydantic import ValidationError

from household.fixtures import FixtureStore
from household.languages import LANGUAGES
from household.pipeline import card_from_rule
from household.rules import REVIEW_RULE
from household.schemas import NextStepCard


def test_card_requires_both_target_action_fields() -> None:
    card = FixtureStore().canned("ltb-n4", "es")["router"]
    for key in ("who_target", "when_target"):
        missing = {k: v for k, v in card.items() if k != key}
        with pytest.raises(ValidationError):
            NextStepCard.model_validate(missing)


def test_review_fallback_has_localized_action_fields_for_each_supported_language() -> None:
    for code, language in LANGUAGES.items():
        card = card_from_rule(REVIEW_RULE, None, language)
        assert card.who_target and card.when_target and card.summary_target
        if code != "en":
            assert card.when_target != card.when
        assert "No appointment or response time has been confirmed" in card.when
