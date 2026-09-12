"""Fetch the real public forms used as fixtures and (re)generate their fixture JSON from the extracted text.

The PDFs are Crown / Government of Canada publications; they are downloaded into fixtures/pdf/ (gitignored) rather
than redistributed. The extracted text is committed so the repo works offline and judges can read the fixtures.

Usage: poetry run python scripts/fetch_fixtures.py [--no-download]
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent.parent
PDF_DIR = ROOT / "fixtures" / "pdf"
DOC_DIR = ROOT / "fixtures" / "documents"
LTB = "https://tribunalsontario.ca/documents/ltb/"

MANIFEST = [
    {
        "id": "ltb-n4-blank",
        "file": "N4.pdf",
        "url": LTB + "Notices%20of%20Termination%20&%20Instructions/N4.pdf",
        "title": "Ontario LTB Form N4 - Notice to End your Tenancy Early for Non-payment of Rent (blank official form)",
        "summary_en": "The blank official Landlord and Tenant Board form a landlord uses to say a tenant owes rent and must pay by a termination date or the landlord may apply to evict.",
        "document_class": "ltb-n4", "stakes": "high", "must_escalate": True,
    },
    {
        "id": "ltb-n1",
        "file": "N1.pdf",
        "url": LTB + "Notices%20of%20Rent%20Increase%20&%20Instructions/N1.pdf",
        "title": "Ontario LTB Form N1 - Notice of Rent Increase (blank official form)",
        "summary_en": "The official notice a landlord must give at least 90 days before a rent increase; it states the new rent and whether the increase is within the guideline.",
        "document_class": "ltb-n1", "stakes": "medium", "must_escalate": False,
    },
    {
        "id": "ltb-n5",
        "file": "N5.pdf",
        "url": LTB + "Notices%20of%20Termination%20&%20Instructions/N5.pdf",
        "title": "Ontario LTB Form N5 - Notice to End your Tenancy for Interfering with Others, Damage or Overcrowding (blank official form)",
        "summary_en": "An official notice to end a tenancy for interference, damage or overcrowding; the form says it is a legal notice that could lead to eviction and gives a 7-day period to correct the problem.",
        "document_class": "ltb-n5", "stakes": "high", "must_escalate": True,
    },
    {
        "id": "ltb-n7",
        "file": "N7.pdf",
        "url": LTB + "Notices%20of%20Termination%20&%20Instructions/N7.pdf",
        "title": "Ontario LTB Form N7 - Notice to End your Tenancy for Causing Serious Problems (blank official form)",
        "summary_en": "An official notice to end a tenancy for serious problems in the rental unit; the landlord can apply to the Board immediately.",
        "document_class": "ltb-n7", "stakes": "high", "must_escalate": True,
    },
    {
        "id": "ltb-n12",
        "file": "N12.pdf",
        "url": LTB + "Notices%20of%20Termination%20&%20Instructions/N12.pdf",
        "title": "Ontario LTB Form N12 - Notice to End your Tenancy Because the Landlord, a Purchaser or a Family Member Requires the Rental Unit (blank official form)",
        "summary_en": "An official notice to end a tenancy because the landlord, a purchaser or a family member intends to move in; at least 60 days' notice and one month's rent compensation are required.",
        "document_class": "ltb-n12", "stakes": "high", "must_escalate": True,
    },
    {
        "id": "cra-rc66",
        "file": "rc66-24e.pdf",
        "url": "https://www.canada.ca/content/dam/cra-arc/formspubs/pbg/rc66/rc66-24e.pdf",
        "title": "CRA Form RC66 - Canada Child Benefit Application (blank official form)",
        "summary_en": "The Canada Revenue Agency application for the Canada child benefit and related credits; the desk may help the visitor fill it in.",
        "document_class": "cra-rc66", "stakes": "medium", "must_escalate": False,
    },
]


def extract(pdf: Path) -> tuple[str, int]:
    reader = PdfReader(str(pdf))
    pages = [(page.extract_text() or "").strip() for page in reader.pages]
    return "\n\n".join(p for p in pages if p), len(reader.pages)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-download", action="store_true")
    args = parser.parse_args()
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    for entry in MANIFEST:
        pdf = PDF_DIR / entry["file"]
        if not pdf.exists() and not args.no_download:
            print(f"downloading {entry['url']}")
            req = urllib.request.Request(entry["url"], headers={"User-Agent": "Mozilla/5.0 (household fixtures)"})
            with urllib.request.urlopen(req, timeout=60) as resp, pdf.open("wb") as fh:
                fh.write(resp.read())
        if not pdf.exists():
            print(f"missing {pdf}; skipping", file=sys.stderr)
            continue
        text, pages = extract(pdf)
        doc = {
            "id": entry["id"],
            "title": entry["title"],
            "source": f"real form (public PDF, {pages} pages; text extracted with pypdf)",
            "source_url": entry["url"],
            "notes": "Blank official form. Text is the verbatim extraction; layout and checkboxes are lost.",
            "summary_en": entry["summary_en"],
            "text": text,
            "expected": {
                "document_class": entry["document_class"],
                "stakes": entry["stakes"],
                "must_escalate": entry["must_escalate"],
            },
            "tags": ["real-form", "blank"],
        }
        (DOC_DIR / f"{entry['id']}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {entry['id']} ({pages} pages, {len(text)} chars)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
