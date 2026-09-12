"""Source-checked explanations for supported local forms, separate from model extraction.

This first policy covers the English Ontario N4 form. It is product policy informed by the
official form, not a determination of validity, money owed or a tenant's legal options.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .rules import N4_FORM
from .schemas import Deadline, DocumentReading

N4_EXPLANATION = {
    "en": {
        "is": "Form N4 is a landlord's notice, not an eviction order. It records a landlord's rent claim; it does not establish that the claim is correct.",
        "asks": "The form describes a request for payment by a stated date. You do not have to leave just because the rent is not paid. The landlord may apply to the Landlord and Tenant Board, where you can dispute the claim. Ask staff for help finding legal advice.",
        "date": "Payment date requested by the landlord",
        "amount": "Rent amount claimed by the landlord",
        "missing": "A payment date or claimed amount could not be verified from the supplied text. Check the original notice with staff.",
    },
    "es": {
        "is": "El formulario N4 es un aviso del propietario, no es una orden de desalojo. Registra una reclamación de alquiler del propietario; no demuestra que la reclamación sea correcta.",
        "asks": "El formulario describe una solicitud de pago para una fecha indicada. Usted no tiene que mudarse solo porque no se haya pagado el alquiler. El propietario puede presentar una solicitud ante la Landlord and Tenant Board (Junta de Propietarios e Inquilinos), donde usted puede disputar la reclamación. Pida al personal ayuda para encontrar asesoramiento legal.",
        "date": "Fecha de pago solicitada por el propietario",
        "amount": "Alquiler que reclama el propietario",
        "missing": "No se pudo verificar una fecha de pago o una cantidad reclamada en el texto proporcionado. Revise el aviso original con el personal.",
    },
}


def _flat(text: str) -> str:
    return " ".join(text.split())


@dataclass(frozen=True)
class ReadingPolicyBinding:
    model_reading: DocumentReading
    reading: DocumentReading
    rule_id: str = "ON-LTB-N4"
    version: str = "2026-09-10"

    def provenance(self) -> dict[str, str]:
        return {"rule_id": self.rule_id, "version": self.version, "source_url": N4_FORM.url,
                "method": "source-checked local policy explanation; model reading retained separately"}

    def summary(self, language: str) -> str | None:
        words = N4_EXPLANATION.get(language)
        if words is None:
            return None
        parts = [words["is"], words["asks"]]
        if self.reading.deadlines:
            parts.append(f"{words['date']}: {self.reading.deadlines[0].date_text}.")
        if self.reading.amounts:
            parts.append(f"{words['amount']}: {self.reading.amounts[0]}.")
        if not self.reading.deadlines or not self.reading.amounts:
            parts.append(words["missing"])
        return " ".join(parts)


def bind_reading(reading: DocumentReading, source: str) -> ReadingPolicyBinding | None:
    """Bind only a classified N4 corroborated by its actual form heading.

    Extract its explicitly labelled claim/date from the supplied form, independently of
    model prose. Missing/unrecognised fields stay absent rather than borrowing guesses.
    Flexible whitespace handles extracted PDF line breaks; no date/amount calculation.
    """
    flat = _flat(source)
    if reading.document_class != "ltb-n4" or not re.search(r"\bN4\b", flat, re.I):
        return None
    if not re.search(r"Notice to End (?:your|a) Tenancy.*?For Non-payment of Rent", flat, re.I):
        return None
    amount = re.search(r"I believe you owe me (\$[\d,]+(?:\.\d{2})?) in rent", flat, re.I)
    date = re.search(r"pay this amount by (\d{1,2}/\d{1,2}/\d{4})\.", flat, re.I)
    deadlines = [Deadline(label=N4_EXPLANATION["en"]["date"], date_text=date[1], quote=date[0])] if date else []
    amounts = [amount[1]] if amount else []
    evidence = [match[0] for match in [amount, date] if match]
    bound = reading.model_copy(update={
        "title": "Ontario Landlord and Tenant Board Form N4",
        "what_it_is": N4_EXPLANATION["en"]["is"],
        "what_it_asks": N4_EXPLANATION["en"]["asks"],
        "deadlines": deadlines, "amounts": amounts, "evidence": evidence,
        "stakes": "high", "stakes_reason": "The supplied form describes possible Board eviction proceedings.",
    })
    return ReadingPolicyBinding(model_reading=reading.model_copy(deep=True), reading=bound)
