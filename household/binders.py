"""Source binders: checks that what the model quoted is really in the source. These do not certify anything else.

`bind_source_values` restores a model-normalized date only when exactly one verbatim source date matches it, and
reports every amount or date it could not find. The vision packet adds the image analogue for transcribed lines.
"""

from __future__ import annotations

import re
from datetime import date

from .schemas import IntakeReading

MONTHS = {name: index for index, name in enumerate(
    "January February March April May June July August September October November December".split(), 1)}
DATE = re.compile(r"(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+)?"
                  r"(?P<month>" + "|".join(MONTHS) + r")\s+(?P<day>\d{1,2}),?\s+(?P<year>\d{4})")
BLANK_FIELD = re.compile(r"_{3,}|\[\s*\]")


def normalized(text: str) -> str:
    return " ".join(text.split())


def form_excerpt(source: str) -> str | None:
    """An unchanged excerpt spanning blank fields, never a replacement form."""
    lines = source.splitlines(keepends=True)
    fields = [i for i, line in enumerate(lines) if BLANK_FIELD.search(line)]
    if not fields:
        return None
    return "".join(lines[fields[0]:fields[-1] + 1]).rstrip("\r\n")


def bind_source_values(reading: IntakeReading, source: str) -> list[str]:
    """Restore ISO-normalized model dates only from matching, verbatim source quotes.

    Unsupported/ambiguous formats are review failures, never inferred dates. Amounts must appear verbatim.
    """
    issues: list[str] = []
    source_normalized = normalized(source)
    for item in reading.dates:
        quote = normalized(item.quote)
        if not quote or quote not in source_normalized:
            issues.append(f"Date quote is not in the source: {item.label}")
            continue
        if item.date_text and item.date_text in quote:
            continue
        try:
            wanted = date.fromisoformat(item.date_text)
        except ValueError:
            wanted = None
        matches = []
        for match in DATE.finditer(quote):
            try:
                candidate = date(int(match["year"]), MONTHS[match["month"]], int(match["day"]))
            except ValueError:
                continue
            if candidate == wanted:
                matches.append(match.group())
        if len(set(matches)) == 1:
            item.date_text = matches[0]
        else:
            issues.append(f"Date cannot be verified verbatim: {item.date_text}")
    for item in reading.amounts:
        quote = normalized(item.quote)
        if not item.amount_text.strip() or item.amount_text not in source:
            issues.append(f"Amount is not in the source: {item.amount_text or item.label}")
        elif quote and quote not in source_normalized:
            issues.append(f"Amount quote is not in the source: {item.label}")
    return issues
