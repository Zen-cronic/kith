"""Product-recall rules come from the recall record itself: the CPSC publishes the remedy and the consumer contact,
and this skill quotes them word for word. The recorded record on disk (fixtures/skills/recall/cpsc-<number>.json) is
the offline source the tools search; the external-api-readonly rail replays the same file or fetches the live record.
tests/skills/test_recall.py asserts the quotes below are byte-for-byte the recorded record's fields."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ...config import ROOT
from ..base import Citation, RulesTable

RECALL_FIXTURES = ROOT / "fixtures" / "skills" / "recall"
CPSC_API_URL = "https://www.saferproducts.gov/RestWebServices/Recall?format=json&RecallNumber=26639"
RECALL_26639_URL = (
    "https://www.cpsc.gov/Recalls/2026/Peony-Design-Recalls-Personalized-Baby-Bibs-and-Stroller-Bags-Due-to-Risk-of-"
    "Serious-Injury-or-Death-from-Choking-Hazard"
)
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
WORD = re.compile(r"[a-z0-9]+")

RULES = RulesTable(
    title="Product recall remedy (CPSC recall record, quoted verbatim; recorded 2026-09-12)",
    citations=(
        Citation(
            "CPSC recall 26639, remedy",
            RECALL_26639_URL,
            "Consumers should take the recalled baby bibs and stroller bags away from children immediately, stop using "
            "them, and contact Peony Design to receive a full refund. Consumers will be asked to discard the recalled "
            "baby bibs and stroller bags.",
        ),
        Citation(
            "CPSC recall 26639, consumer contact",
            RECALL_26639_URL,
            "Peony Design by email at peonydesigncoshoppe@gmail.com for more information.",
        ),
        Citation(
            "CPSC recall 26639, hazard",
            RECALL_26639_URL,
            "The snap can detach from the recalled bibs and stroller bags, posing a risk of serious injury or death from "
            "a choking hazard to young children.",
        ),
    ),
    params={
        "remedy": "the claim email quotes the record's Remedies text and the recall URL verbatim; a remedy is never paraphrased",
        "recipient": "the email address in the record's ConsumerContact; when the record has none, the member's own "
        "email and a needs entry saying so",
        "lookup": "recall:remedy is read-only: it never writes to the CPSC, the manufacturer or the retailer",
        "money": "a refund is the manufacturer's to pay; nothing moves in this ledger until it arrives",
    },
)


# The recorded records on disk


def recorded_recalls(directory: Path | None = None) -> list[dict[str, Any]]:
    """Every recorded CPSC record under fixtures/skills/recall, with the date it was fetched."""
    found: list[dict[str, Any]] = []
    for path in sorted((directory or RECALL_FIXTURES).glob("cpsc-*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        for record in raw.get("response") or []:
            if isinstance(record, dict) and record.get("RecallNumber"):
                found.append({"record": record, "fetched": raw.get("fetched"), "recorded": bool(raw.get("recorded")),
                              "path": path.relative_to(ROOT).as_posix()})
    return found


def _haystack(record: dict[str, Any]) -> str:
    parts = [str(record.get("Title", "")), str(record.get("Description", ""))]
    parts += [str(p.get("Name", "")) for p in record.get("Products") or []]
    parts += [str(m.get("Name", "")) for m in record.get("Manufacturers") or []]
    return " ".join(parts).lower()


def find_recall(query: str, directory: Path | None = None) -> dict[str, Any] | None:
    """A recorded record by recall number, else the record whose title, description, products or manufacturer match
    the most words of the query (at least half of them). None when nothing matches."""
    needle = query.strip().lower().removeprefix("#")
    if not needle:
        return None
    entries = recorded_recalls(directory)
    by_number = next((e for e in entries if str(e["record"].get("RecallNumber")) == needle), None)
    if by_number is not None:
        return by_number
    words = [w for w in WORD.findall(needle) if len(w) > 2]
    if not words:
        return None
    best: tuple[int, dict[str, Any]] | None = None
    for entry in entries:
        text = _haystack(entry["record"])
        score = sum(1 for w in words if w in text)
        if score and (best is None or score > best[0]):
            best = (score, entry)
    if best is None or best[0] * 2 < len(words):
        return None
    return best[1]


# What the record says


def remedy_text(record: dict[str, Any]) -> str:
    return " ".join(str(r.get("Name", "")).strip() for r in record.get("Remedies") or [] if r.get("Name")).strip()


def contact_email(record: dict[str, Any]) -> str | None:
    match = EMAIL.search(str(record.get("ConsumerContact") or ""))
    return match.group() if match else None


def manufacturer(record: dict[str, Any]) -> str:
    names = [str(m.get("Name", "")).strip() for m in record.get("Manufacturers") or [] if m.get("Name")]
    return names[0].split(",")[0] if names else "the manufacturer"


def summary(entry: dict[str, Any]) -> dict[str, Any]:
    """The record as the planner reads it: every quoted field verbatim, plus the fetch date and the contact email."""
    record = entry["record"]
    return {
        "recall_number": str(record.get("RecallNumber")),
        "title": record.get("Title"),
        "url": record.get("URL"),
        "recall_date": str(record.get("RecallDate") or "")[:10],
        "remedy": remedy_text(record),
        "remedy_options": [o.get("Option") for o in record.get("RemedyOptions") or []],
        "consumer_contact": record.get("ConsumerContact"),
        "contact_email": contact_email(record),
        "hazards": [h.get("Name") for h in record.get("Hazards") or []],
        "products": [p.get("Name") for p in record.get("Products") or []],
        "units": [p.get("NumberOfUnits") for p in record.get("Products") or []],
        "manufacturers": [m.get("Name") for m in record.get("Manufacturers") or []],
        "retailers": [r.get("Name") for r in record.get("Retailers") or []],
        "fetched": entry.get("fetched"),
        "source": "recorded CPSC response" if entry.get("recorded") else "constructed fixture (not a recorded response)",
        "path": entry.get("path"),
    }


# The claim email, written from the record


def claim_subject(record: dict[str, Any], product: str) -> str:
    return f"Recall {record.get('RecallNumber')}: refund request for {product}"


def claim_body(record: dict[str, Any], product: str, member_name: str, purchase_text: str = "") -> str:
    """The email a member sends to claim the remedy. The remedy and the recall URL are quoted verbatim."""
    bought = f", {purchase_text.strip().rstrip('.')}" if purchase_text.strip() else ""
    return "\n".join([
        f"Hello {manufacturer(record)},",
        "",
        f'I am writing about CPSC recall {record.get("RecallNumber")}, "{record.get("Title")}".',
        f"We own {product}{bought}, and I am requesting the remedy the recall notice promises.",
        "",
        "The recall notice says:",
        f'"{remedy_text(record)}"',
        "",
        f"Recall notice: {record.get('URL')}",
        "",
        "Please tell me how to receive the refund and what you need from me (photos, proof of purchase, or the items).",
        "",
        "Thank you,",
        member_name,
    ])
