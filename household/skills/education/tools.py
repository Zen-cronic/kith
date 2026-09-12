"""Education tools: the CESG arithmetic, the tuition schedule with its due-date check, and two proposal builders.
None of them moves money; the tuition builder refuses a past due date in code, not only in prose."""

from __future__ import annotations

import json
from typing import Any

from strands import tool

from ...model import money
from ..base import SkillTool, ToolContext, error_json, proposal_json
from . import rules
from .templates import RESP_CONTRIBUTE, TUITION_PAY


def build_cesg_room(ctx: ToolContext) -> Any:
    @tool
    def cesg_room(
        beneficiary_member_id: str,
        contribution: str,
        year_contributed_so_far: str = "",
        carry_forward_room: str = "0.00",
        lifetime_grant_received: str = "0.00",
    ) -> str:
        """The Canada Education Savings Grant a contribution earns for a child, computed from the cited canada.ca
        parameters: 20% of the contribution, capped by this year's room (500.00, up to 1,000.00 with unused room from
        earlier years) and the 7,200.00 lifetime maximum, and zero after the year the child turns 17. Returns the
        numbers, the assumptions and the citation labels. Copy them; never compute a grant yourself.

        Args:
            beneficiary_member_id: the child's member id, e.g. "kofi".
            contribution: the amount as written, e.g. "200.00".
            year_contributed_so_far: what was already contributed this calendar year, or "" to read the ledger.
            carry_forward_room: unused CESG room from earlier years in grant dollars, if the member stated it, else "0.00".
            lifetime_grant_received: CESG received to date, if the member stated it, else "0.00".
        """
        try:
            room = rules.cesg_room(ctx.household, beneficiary_member_id, contribution, ctx.now,
                                   year_contributed_so_far=year_contributed_so_far.strip() or None,
                                   carry_forward_room=carry_forward_room.strip() or "0.00",
                                   lifetime_grant_received=lifetime_grant_received.strip() or "0.00")
        except ValueError as exc:
            ctx.note("cesg_room", beneficiary_member_id=beneficiary_member_id, ok=False)
            return error_json(str(exc))
        if room is None:
            ctx.note("cesg_room", beneficiary_member_id=beneficiary_member_id, ok=False)
            return error_json(f"{beneficiary_member_id!r} is not in this household")
        ctx.note("cesg_room", beneficiary_member_id=beneficiary_member_id, contribution=room.contribution, grant=room.grant)
        return json.dumps({**room.as_dict(), "grant_room_note": room.note()}, ensure_ascii=False)

    return cesg_room


def build_tuition_schedule(ctx: ToolContext) -> Any:
    @tool
    def tuition_schedule(payee: str, due_text: str = "") -> str:
        """The tuition entries on file for a school or program, each with whether it is past due on the session
        clock, and the same check for a due date quoted from the request. Read-only.

        Args:
            payee: the school or program as written, e.g. "Bright Path Montessori".
            due_text: the due date exactly as written in the request, or "".
        """
        found = rules.tuition_schedule(ctx.household, payee, ctx.now)
        if due_text.strip():
            found["requested_due"] = rules.due_status(due_text, ctx.now)
        ctx.note("tuition_schedule", payee=payee, on_file=len(found["schedule"]), past_due=(found.get("requested_due") or {}).get("past_due"))
        return json.dumps(found, ensure_ascii=False)

    return tuition_schedule


def build_propose_resp_contribution(ctx: ToolContext) -> Any:
    @tool
    def propose_resp_contribution(beneficiary_member_id: str, amount: str, memo: str = "") -> str:
        """Build a well-formed RESP contribution proposal (payment:transfer to 'RESP for <child>' on the internal
        ledger) with the CESG grant room computed in code. It proposes only; authority decides.

        Args:
            beneficiary_member_id: the child whose RESP it is, e.g. "kofi".
            amount: the contribution as written, e.g. "200.00".
            memo: what the member said it is for, or "".
        """
        try:
            room = rules.cesg_room(ctx.household, beneficiary_member_id, amount, ctx.now)
        except ValueError as exc:
            ctx.note("propose_resp_contribution", beneficiary_member_id=beneficiary_member_id, ok=False)
            return error_json(str(exc))
        if room is None:
            ctx.note("propose_resp_contribution", beneficiary_member_id=beneficiary_member_id, ok=False)
            return error_json(f"{beneficiary_member_id!r} is not in this household")
        member = ctx.household.member(beneficiary_member_id)
        assert member is not None
        ctx.note("propose_resp_contribution", beneficiary_member_id=member.id, amount=room.contribution, grant=room.grant, ok=True)
        purpose = memo.strip() or f"RESP contribution for {member.name}"
        return proposal_json(
            RESP_CONTRIBUTE,
            subject_member_id=member.id,
            recipient=rules.resp_payee(member),
            amount_text=room.contribution,
            currency=ctx.household.currency,
            payload={"kind": "resp:contribute", "beneficiary_member_id": member.id,
                     "year_contributed_so_far": room.year_contributed_so_far, "grant_room_note": room.note(), "purpose": purpose},
            rationale=f"{ctx.actor.name} contributes {room.contribution} {ctx.household.currency} to {member.name}'s RESP; "
                      f"the CESG on it is {room.grant} by the cited canada.ca rule.",
        )

    return propose_resp_contribution


def build_propose_tuition_payment(ctx: ToolContext) -> Any:
    @tool
    def propose_tuition_payment(payee: str, amount: str, due_text: str, instalment: str = "", subject_member_id: str = "") -> str:
        """Build a well-formed tuition instalment proposal (payment:transfer to the school on the internal ledger).
        Refuses, with the reason, when the due date is already past or cannot be read. It proposes only.

        Args:
            payee: the school or program exactly as written.
            amount: the instalment as written, e.g. "450.00".
            due_text: the due date exactly as written, e.g. "September 30, 2026".
            instalment: which instalment, as written (e.g. "2 of 3"), or "".
            subject_member_id: whose tuition it is; "" means the member asking.
        """
        subject = ctx.household.member(subject_member_id.strip()) if subject_member_id.strip() else ctx.actor
        if subject is None:
            ctx.note("propose_tuition_payment", payee=payee, ok=False)
            return error_json(f"{subject_member_id!r} is not in this household")
        try:
            money(amount)
        except ValueError as exc:
            ctx.note("propose_tuition_payment", payee=payee, ok=False)
            return error_json(str(exc))
        status = rules.due_status(due_text, ctx.now)
        if status["past_due"] is None:
            ctx.note("propose_tuition_payment", payee=payee, ok=False)
            return error_json("due date not understood; nothing is proposed on a date that cannot be read", **status)
        if status["past_due"]:
            ctx.note("propose_tuition_payment", payee=payee, past_due=True, ok=False)
            return error_json(f"the instalment was due {due_text}, which is already past ({status['today']} today); "
                              "no payment is proposed on a past due date: put it in needs and ask the member to confirm "
                              "with the school what is still owed", **status)
        ctx.note("propose_tuition_payment", payee=payee, amount=amount, due=status["due"], ok=True)
        which = f" ({instalment.strip()})" if instalment.strip() else ""
        return proposal_json(
            TUITION_PAY,
            subject_member_id=subject.id,
            recipient=payee.strip(),
            amount_text=amount,
            currency=ctx.household.currency,
            payload={"kind": "tuition:pay", "payee": payee.strip(), "due": due_text.strip(), "instalment": instalment.strip(),
                     "purpose": f"tuition instalment{which} for {subject.name}, due {due_text.strip()}"},
            rationale=f"{ctx.actor.name} pays {subject.name}'s tuition instalment{which} of {amount} {ctx.household.currency} to "
                      f"{payee.strip()}, due {due_text.strip()} ({status['days_until']} days from today).",
        )

    return propose_tuition_payment


TOOLS = (
    SkillTool("cesg_room", build_cesg_room),
    SkillTool("tuition_schedule", build_tuition_schedule),
    SkillTool("propose_resp_contribution", build_propose_resp_contribution),
    SkillTool("propose_tuition_payment", build_propose_tuition_payment),
)
