"""Source checks and routine takeaway copy. These checks do not certify translation."""

from __future__ import annotations

import re
from datetime import date

from .schemas import CriticVerdict, DocumentReading, Draft, Interpretation, NextStepCard

MONTHS = {name: index for index, name in enumerate(
    "January February March April May June July August September October November December".split(), 1)}
DATE = re.compile(r"(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+)?"
                  r"(?P<month>" + "|".join(MONTHS) + r")\s+(?P<day>\d{1,2}),?\s+(?P<year>\d{4})")


def normalized(text: str) -> str:
    return " ".join(text.split())


def form_excerpt(source: str) -> str | None:
    """An unchanged excerpt spanning blank fields, never a replacement form."""
    lines = source.splitlines(keepends=True)
    fields = [i for i, line in enumerate(lines) if re.search(r"_{3,}|\[\s*\]", line)]
    if not fields:
        return None
    return "".join(lines[fields[0]:fields[-1] + 1]).rstrip("\r\n")


def bind_source_values(reading: DocumentReading, source: str) -> list[str]:
    """Restore ISO-normalized model dates only from matching, verbatim source quotes.

    Unsupported/ambiguous formats are review failures, never inferred dates.
    """
    issues = []
    source_normalized = normalized(source)
    for deadline in reading.deadlines:
        quote = normalized(deadline.quote)
        if not quote or quote not in source_normalized:
            issues.append(f"Deadline quote is not in the source: {deadline.label}")
            continue
        if deadline.date_text and deadline.date_text in quote:
            continue
        try:
            wanted = date.fromisoformat(deadline.date_text)
        except ValueError:
            wanted = None
        matches = []
        for match in DATE.finditer(quote):
            try:
                candidate = date(int(match['year']), MONTHS[match['month']], int(match['day']))
            except ValueError:
                continue
            if candidate == wanted:
                matches.append(match.group())
        if len(set(matches)) == 1:
            deadline.date_text = matches[0]
        else:
            issues.append(f"Date cannot be verified verbatim: {deadline.date_text}")
    for amount in reading.amounts:
        if not amount.strip() or amount not in source:
            issues.append(f"Amount is not in the source: {amount}")
    return issues


def draft_issues(draft: Draft | None, verdict: CriticVerdict, source: str) -> list[str]:
    if draft is None:
        return ["A routine response/checklist is missing."]
    issues = []
    if draft.kind == "none" or not draft.body_en.strip() or not draft.body_target.strip():
        issues.append("Supply a useful response/checklist in both languages.")
    if draft.assumptions:
        issues.append("Remove every unsupported assumption; visitor facts and choices must remain unfilled.")
    if not draft.facts_used or any(not normalized(q) or normalized(q) not in normalized(source) for q in draft.facts_used):
        issues.append("facts_used must contain verbatim source excerpts supporting the draft.")
    if form_excerpt(source):
        if not draft.preparation_steps:
            issues.append("Supply preparation_steps: one instruction_en, instruction_target and exact source_quote per action on the original form. Do not put form fields in those instructions.")
        cited = [normalized(step.source_quote) for step in draft.preparation_steps]
        for line in (form_excerpt(source) or "").splitlines():
            if re.search(r"_{3,}|\[\s*\]", line) and not any(normalized(line) in quote for quote in cited):
                issues.append(f"Cover this original form field line in preparation_steps with its exact source quote: {line.strip()}")
        for step in draft.preparation_steps:
            if not normalized(step.source_quote) or normalized(step.source_quote) not in normalized(source):
                issues.append("Each preparation step needs a verbatim supporting source_quote.")
            if not step.instruction_en.strip() or not step.instruction_target.strip():
                issues.append("Every preparation step must be present in both languages.")
        if draft.kind != "form-checklist" or "original" not in draft.body_en.lower():
            issues.append("Make a form-checklist telling the visitor to complete the original form; do not replace its consent or choices with a generated form.")
        if re.search(r"_{3,}|I give permission|Enclosed is", draft.body_en, re.I):
            issues.append("Use instructions for the original form, not a filled or recreated form or a claim that payment is enclosed.")
    payees = re.findall(r"cheque payable to (.+?)(?: is accepted|[.\n]|$)", source, re.I)
    for payee in payees:
        if payee not in draft.body_en or payee not in draft.body_target:
            issues.append(f"Keep the cheque payee exactly as written in BOTH languages: {payee}")
    if verdict.decision == "approve":
        passed = {check.name for check in verdict.checks if check.passed}
        for check in ("draft-facts", "draft-assumptions"):
            if check not in passed:
                issues.append(f"Critic must explicitly pass {check} after reviewing this draft; approval is incomplete.")
    return issues


def routine_card(reading: DocumentReading, interpretation: Interpretation, has_form: bool) -> NextStepCard:
    """An approved draft is ready for visitor review; it does not commit staff."""
    spanish = interpretation.language == "es"
    dates = "; ".join(d.date_text for d in reading.deadlines)
    next_en = ("Review the checklist, then complete the original form with your own choices and information. "
               "Keep the original letter for its return instructions." if has_form else
               "Review the draft and add your own information before using it. Keep the original letter for its instructions.")
    next_es = ("Revise la lista y después complete el formulario original con sus propias decisiones y datos. "
               "Conserve la carta original para consultar cómo entregarlo." if has_form else
               "Revise el borrador y añada sus propios datos antes de usarlo. Conserve la carta original para consultar sus instrucciones.")
    return NextStepCard(
        outcome="proceed", headline_en="Your draft is ready to review",
        headline_target="Su borrador está listo para revisar" if spanish else "Your draft is ready to review",
        statement_en="Prepared for your review. Nothing has been signed, paid, sent or booked.",
        statement_target="Preparado para que lo revise. No se ha firmado, pagado, enviado ni reservado nada." if spanish else "Prepared for your review. Nothing has been signed, paid, sent or booked.",
        who="You; ask staff if you want help", who_target="usted; pida ayuda al personal si la desea" if spanish else "You; ask staff if you want help",
        next_step_en=next_en, next_step_target=next_es if spanish else next_en,
        when="Dates in the letter: " + dates if dates else "No date verified; check the original letter",
        when_target=("Fechas de la carta: " + dates if dates else "No se verificó ninguna fecha; consulte la carta original") if spanish else ("Dates in the letter: " + dates if dates else "No date verified; check the original letter"),
        safe_today_en="Read the original letter and review these preparation notes.",
        safe_today_target="Lea la carta original y revise estas notas de preparación." if spanish else "Read the original letter and review these preparation notes.",
        summary_en=reading.what_it_is, summary_target=interpretation.target_text,
    )
