"""Round-trip fidelity: how much of the English reading survives translation and independent back-translation.

This is computed in code from two English strings, so it is the same measurement in fake and live mode, and a
monolingual judge can check it. It is a proxy, not a translation-quality score: it rewards keeping every number and
date and most content words, and penalises dropped or added material.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "by", "for", "is", "it", "this", "that", "you", "your",
    "are", "be", "as", "at", "with", "from", "if", "do", "does", "can", "will", "has", "have", "was", "were",
    "but", "only", "also", "which", "who", "than", "then", "so", "its", "into", "about", "one", "before",
}
_NUM = re.compile(r"\$?\d[\d,]*(?:\.\d+)?(?::\d{2})?(?:/\d{1,2}/\d{2,4})?(?:st|nd|rd|th)?")


@dataclass(frozen=True)
class FidelityScore:
    score: float
    band: str
    token_overlap: float
    sequence_ratio: float
    numbers_expected: list[str]
    numbers_missing: list[str]
    meaning_warnings: list[str] = field(default_factory=list)

    @property
    def reliable(self) -> bool:
        return self.band == "reliable"


def _normalize_negation(text: str) -> str:
    text = text.lower().replace("’", "'")
    for contraction, expanded in (("cannot", "can not"), ("can't", "can not"), ("won't", "will not")):
        text = re.sub(r"\b" + re.escape(contraction) + r"\b", expanded, text)
    return re.sub(r"\b(\w+)n't\b", r"\1 not", text)


def _negations(text: str) -> int:
    # A mismatch is a review signal, not a semantic-equivalence test. Equal
    # counts do not prove the same scope; a faithful paraphrase may also differ.
    return len(re.findall(r"\b(?:not|no|never|neither|nor|without)\b", text))


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9$']+", text.lower()) if t not in _STOP and len(t) > 1}


def _numbers(text: str) -> list[str]:
    return sorted({m.group(0).replace(",", "") for m in _NUM.finditer(text)})


def score_fidelity(source_en: str, back_translation_en: str, floor: float = 0.50, caution: float = 0.70) -> FidelityScore:
    source_en = _normalize_negation(source_en)
    back_translation_en = _normalize_negation(back_translation_en)
    src, back = _tokens(source_en), _tokens(back_translation_en)
    overlap = len(src & back) / len(src | back) if (src | back) else 0.0
    ratio = SequenceMatcher(None, source_en.lower(), back_translation_en.lower()).ratio()
    expected = _numbers(source_en)
    present = set(_numbers(back_translation_en))
    missing = [n for n in expected if n not in present]
    coverage = 1.0 if not expected else (len(expected) - len(missing)) / len(expected)
    score = 0.45 * overlap + 0.25 * ratio + 0.30 * coverage
    if missing:
        score = min(score, caution - 0.01)  # a dropped number or date can never read as reliable
    warnings = []
    if _negations(source_en) != _negations(back_translation_en):
        warnings.append("Negation changed; a person must check the meaning.")
        score = min(score, floor - 0.01)
    if not source_en.strip() or not back_translation_en.strip():
        warnings.append("Text is missing; the round trip cannot be checked.")
        score = 0.0
    band = "reliable" if score >= caution else ("caution" if score >= floor else "unreliable")
    return FidelityScore(round(score, 3), band, round(overlap, 3), round(ratio, 3), expected, missing, warnings)
