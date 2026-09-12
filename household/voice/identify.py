"""Who is speaking. Identification, not biometrics: the member sends their id and PIN in the first frame and the
PBKDF2 hash in the ledger decides (`Member.verify_pin`). Three failures close the socket with policy code 1008."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from ..model import Household, Member
from .frames import FrameError, parse_client_frame

MAX_ATTEMPTS = 3
CLOSE_POLICY_VIOLATION = 1008
CLOSE_REASON = "identification failed"


class IdentificationFailed(Exception):
    def __init__(self, attempts: int) -> None:
        self.attempts = attempts
        super().__init__(f"identification failed after {attempts} attempts")


def check_pin(household: Household, member_id: str, pin: str) -> Member | None:
    """The member when the id and PIN match; None for an unknown member or a wrong PIN (same answer, on purpose)."""
    member = household.member(member_id)
    if member is None or not member.verify_pin(pin):
        return None
    return member


async def identify(
    receive_text: Callable[[], Awaitable[str]],
    send_json: Callable[[dict[str, Any]], Awaitable[None]],
    close: Callable[[int, str], Awaitable[None]],
    household: Household,
    max_attempts: int = MAX_ATTEMPTS,
) -> Member:
    """Read identify frames until one matches. Non-identify frames get an error and do not count as attempts;
    an unknown member or a wrong PIN counts. Raises IdentificationFailed after closing the socket with 1008."""
    attempts = 0
    while True:
        try:
            frame = parse_client_frame(await receive_text())
        except FrameError as exc:
            await send_json(exc.as_frame())
            continue
        if frame["type"] != "identify":
            await send_json({"type": "error", "code": "identify_first", "detail": "Send {\"type\": \"identify\", \"member_id\", \"pin\"} first."})
            continue
        # PBKDF2 with 200k iterations takes long enough to keep off the event loop.
        member = await asyncio.to_thread(check_pin, household, frame["member_id"], frame["pin"])
        if member is not None:
            return member
        attempts += 1
        left = max_attempts - attempts
        if left <= 0:
            await close(CLOSE_POLICY_VIOLATION, CLOSE_REASON)
            raise IdentificationFailed(attempts)
        await send_json({"type": "identify_failed", "attempts_left": left, "detail": "That member id and PIN do not match."})
