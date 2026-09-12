"""The rule catalogue: the named documents and rules that bind the critic's refusal.

A rule is either law or an official form's own wording (kind = "law-or-official-form", with verbatim citations fetched on a
recorded date) or an organisation policy (kind = "organisation-policy", which is ours and says so). The refusal statement is
carried in English and Spanish here; other languages are rendered by the router agent from the English text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Policy = Literal["read-and-explain-only", "assist"]


@dataclass(frozen=True)
class Citation:
    source: str
    url: str
    fetched: str
    quote: str


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    jurisdiction: str
    kind: Literal["law-or-official-form", "organisation-policy"]
    policy: Policy
    document_classes: tuple[str, ...]
    handoff: dict[str, str]
    statement: dict[str, str]
    safe_today: dict[str, str] = field(default_factory=dict)
    citations: tuple[Citation, ...] = ()

    def citation_line(self) -> str:
        return "; ".join(f"{c.source} ({c.fetched})" for c in self.citations) or f"{self.title} (organisation policy)"


LTB_BROCHURE = Citation(
    source="LTB brochure 'Important Information about Your Hearing', last updated March 2022",
    url="https://tribunalsontario.ca/documents/ltb/Brochures/Important%20Information%20about%20Your%20Hearing.html",
    fetched="2026-09-05",
    quote="The LTB does not usually provide interpreters for languages other than French or English. If you want an "
    "interpreter with you at the hearing, then you are expected to arrange for someone to interpret for you.",
)

N4_FORM = Citation(
    source="Tribunals Ontario, Form N4 'Notice to End your Tenancy Early for Non-payment of Rent', v. 01/04/2022",
    url="https://tribunalsontario.ca/documents/ltb/Notices%20of%20Termination%20&%20Instructions/N4.pdf",
    fetched="2026-09-05",
    quote="This is a legal notice that could lead to you being evicted from your home. [...] The date that the landlord "
    "gives you in this notice to pay or move out must be at least: 14 days after the landlord gives you the notice, if you "
    "rent by the month or year, or 7 days after the landlord gives you the notice, if you rent by the day or week. [...] "
    "If you agree that you owe the amount that the landlord is claiming, you should pay this amount by the termination date "
    "in this notice. If you do so, the landlord cannot apply to the Board to evict you based on this notice. [...] You may "
    "also want to get legal advice. [...] Get legal advice immediately; you may be eligible for legal aid services.",
)

EVICTION_FORMS = Citation(
    source="Tribunals Ontario, Forms N5, N7 and N12 (v. 01/04/2022), first line under the tenant's name",
    url="https://tribunalsontario.ca/ltb/forms-filing-and-fees/",
    fetched="2026-09-05",
    quote="This is a legal notice that could lead to you being evicted from your home.",
)

IRPA_91 = Citation(
    source="Immigration and Refugee Protection Act, S.C. 2001, c. 27, s. 91(1)-(2) (Act current to 2026-06-21)",
    url="https://laws-lois.justice.gc.ca/eng/acts/i-2.5/section-91.html",
    fetched="2026-09-05",
    quote="no person shall knowingly, directly or indirectly, represent or advise a person for consideration - or offer to "
    "do so - in connection with [...] a proceeding or application under this Act. [...] A person does not contravene "
    "subsection (1) if they are (a) a lawyer who is a member in good standing of a law society of a province [...]; "
    "(b) any other member in good standing of a law society of a province [...], including a paralegal; or (c) a member in "
    "good standing of the College [of Immigration and Citizenship Consultants].",
)

RULES: tuple[Rule, ...] = (
    Rule(
        id="ON-LTB-N4",
        title="Ontario LTB Form N4 - Notice to End your Tenancy Early for Non-payment of Rent",
        jurisdiction="Ontario, Canada",
        kind="law-or-official-form",
        policy="read-and-explain-only",
        document_classes=("ltb-n4",),
        handoff={
            "en": "ask staff about contacting a community legal clinic (Legal Aid Ontario) with a qualified interpreter",
            "es": "pregunte al personal cómo contactar a una clínica legal comunitaria (Legal Aid Ontario) con un intérprete calificado",
        },
        statement={
            "en": "This is Ontario Landlord and Tenant Board Form N4, a notice to end your tenancy early for non-payment "
            "of rent. I can read it to you and tell you what it says and what the date is. I will not draft your response, "
            "because a mistake here can cost you your home. The Landlord and Tenant Board does not usually provide "
            "interpreters for languages other than French or English, so at a hearing you will need a person - not this "
            "screen - to interpret for you.",
            "es": "Este es el Formulario N4 de la Junta de Propietarios e Inquilinos de Ontario (Landlord and Tenant "
            "Board), un aviso para terminar su alquiler antes de tiempo por falta de pago. Puedo leérselo y decirle qué "
            "dice y cuál es la fecha. No voy a redactar su respuesta, porque un error aquí le puede costar su vivienda. "
            "La Junta normalmente no ofrece intérpretes en idiomas que no sean francés o inglés, así que en una audiencia "
            "necesitará una persona - no esta pantalla - que interprete por usted.",
        },
        safe_today={
            "en": "This notice is not an eviction order. Ask staff for help finding legal advice; the form describes payment "
            "options and possible Board proceedings.",
            "es": "Este aviso no es una orden de desalojo. Pida al personal ayuda para encontrar asesoramiento legal; el "
            "formulario describe opciones de pago y posibles procedimientos ante la Junta.",
        },
        citations=(N4_FORM, LTB_BROCHURE),
    ),
    Rule(
        id="ON-LTB-EVICTION-NOTICE",
        title="Ontario LTB notices to end a tenancy (N5, N6, N7, N8, N12, N13) and LTB hearing notices",
        jurisdiction="Ontario, Canada",
        kind="law-or-official-form",
        policy="read-and-explain-only",
        document_classes=("ltb-n5", "ltb-n6", "ltb-n7", "ltb-n8", "ltb-n12", "ltb-n13", "ltb-hearing-notice", "ltb-application"),
        handoff={
            "en": "ask staff about contacting a community legal clinic (Legal Aid Ontario) with a qualified interpreter",
            "es": "pregunte al personal cómo contactar a una clínica legal comunitaria (Legal Aid Ontario) con un intérprete calificado",
        },
        statement={
            "en": "This is an official Ontario Landlord and Tenant Board notice. The form says: 'This is a legal notice that "
            "could lead to you being evicted from your home.' I can read it to you and tell you the dates in it. I will not "
            "draft your response. For a hearing, the Board does not usually provide interpreters for languages other than "
            "French or English, so you will need a person to interpret for you.",
            "es": "Este es un aviso oficial de la Junta de Propietarios e Inquilinos de Ontario. El formulario dice: 'Este es "
            "un aviso legal que podría llevar a que lo desalojen de su vivienda.' Puedo leérselo y decirle las fechas que "
            "contiene. No voy a redactar su respuesta. Para una audiencia, la Junta normalmente no ofrece intérpretes en "
            "idiomas que no sean francés o inglés, así que necesitará una persona que interprete por usted.",
        },
        citations=(EVICTION_FORMS, LTB_BROCHURE),
    ),
    Rule(
        id="CA-IRPA-91",
        title="Immigration and Refugee Protection Act s. 91 - who may advise on an immigration application or proceeding",
        jurisdiction="Canada",
        kind="law-or-official-form",
        policy="read-and-explain-only",
        document_classes=("ircc-letter", "ircc-refusal", "ircc-procedural-fairness", "cbsa-notice", "irb-notice", "immigration-form"),
        handoff={
            "en": "ask staff about contacting a lawyer, licensed paralegal or College-licensed immigration consultant",
            "es": "pregunte al personal cómo contactar a un abogado, un asistente legal con licencia o un consultor de inmigración autorizado",
        },
        statement={
            "en": "This letter is about an immigration application or proceeding. I can read it to you and tell you what it "
            "says and the dates in it. I will not draft your response: under section 91 of the Immigration and Refugee "
            "Protection Act, advice on an application under the Act is reserved to lawyers, licensed paralegals and "
            "College-licensed immigration consultants.",
            "es": "Esta carta trata de una solicitud o un proceso de inmigración. Puedo leérsela y decirle qué dice y las "
            "fechas que contiene. No voy a redactar su respuesta: según el artículo 91 de la Ley de Inmigración y "
            "Protección de Refugiados, el asesoramiento sobre una solicitud bajo esa ley está reservado a abogados, "
            "asistentes legales con licencia y consultores de inmigración autorizados.",
        },
        citations=(IRPA_91,),
    ),
    Rule(
        id="ORG-COURT",
        title="Organisation policy: court documents",
        jurisdiction="this organisation",
        kind="organisation-policy",
        policy="read-and-explain-only",
        document_classes=("court-notice", "court-summons", "court-form"),
        handoff={
            "en": "ask staff about contacting Legal Aid Ontario or a community legal clinic",
            "es": "pregunte al personal cómo contactar a Legal Aid Ontario o una clínica legal comunitaria",
        },
        statement={
            "en": "This is a court document. Our front desk reads and explains court documents but does not draft anything "
            "to be filed with a court. Ask staff for help finding legal support.",
            "es": "Este es un documento judicial. Nuestra recepción lee y explica documentos judiciales, pero no redacta nada "
            "para presentar ante un tribunal. Pida al personal ayuda para encontrar apoyo legal.",
        },
    ),
    Rule(
        id="ORG-CHILD-PROTECTION",
        title="Organisation policy: children's aid / child protection correspondence",
        jurisdiction="this organisation",
        kind="organisation-policy",
        policy="read-and-explain-only",
        document_classes=("cas-letter",),
        handoff={
            "en": "ask staff about contacting a family lawyer through Legal Aid Ontario",
            "es": "pregunte al personal cómo contactar a un abogado de familia a través de Legal Aid Ontario",
        },
        statement={
            "en": "This letter is from a children's aid society. Our front desk reads and explains it but does not draft a "
            "response. Ask staff for help finding a family lawyer.",
            "es": "Esta carta es de una sociedad de ayuda a la infancia. Nuestra recepción la lee y la explica, pero no "
            "redacta una respuesta. Pida al personal ayuda para encontrar un abogado de familia.",
        },
    ),
)

FIDELITY_RULE = Rule(
    id="FIDELITY-FLOOR",
    title="Organisation policy: interpretation is only delivered when its back-translation checks out",
    jurisdiction="this organisation",
    kind="organisation-policy",
    policy="read-and-explain-only",
    document_classes=(),
    handoff={
        "en": "ask staff about contacting a human interpreter",
        "es": "pregunte al personal cómo contactar a un intérprete humano",
    },
    statement={
        "en": "I am not confident I said that correctly in your language: when I translated my own words back to English, "
        "something important was lost. I will not guess. Ask staff for help finding a person to read this letter with you.",
        "es": "No estoy seguro de haberlo dicho bien en su idioma: al traducir mis propias palabras de vuelta al inglés, se "
        "perdió algo importante. No voy a adivinar. Pida al personal ayuda para encontrar a una persona que lea esta carta con usted.",
    },
)

REVIEW_RULE = Rule(
    id="DRAFT-REVIEW-REQUIRED",
    title="Organisation policy: a draft requires explicit approval",
    jurisdiction="this organisation",
    kind="organisation-policy",
    policy="read-and-explain-only",
    document_classes=(),
    handoff={"en": "a staff member for review", "es": "un miembro del personal para revisar el borrador"},
    statement={
        "en": "This draft is not approved. It still needs a person's review before you use it.",
        "es": "Este borrador no está aprobado. Una persona debe revisarlo antes de que usted lo use.",
    },
    safe_today={
        "en": "Keep your original letter and ask staff to review it with you.",
        "es": "Conserve la carta original y pida al personal que la revise con usted.",
    },
)

_BY_CLASS: dict[str, Rule] = {cls: rule for rule in RULES for cls in rule.document_classes}


def lookup(document_class: str) -> Rule | None:
    return _BY_CLASS.get(document_class)


def policy_for(document_class: str) -> Policy:
    rule = lookup(document_class)
    return rule.policy if rule else "assist"


def catalogue_summary() -> str:
    lines = []
    for rule in RULES:
        lines.append(f"- {rule.id}: {rule.title} -> classes {', '.join(rule.document_classes)} -> {rule.policy}")
    return "\n".join(lines)
