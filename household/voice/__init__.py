"""Live voice (P7). A household member identified by member id + PIN holds a Nova 2 Sonic conversation through the
`/api/voice` WebSocket (`strands.experimental.bidi.BidiAgent`). The tools the conversation may call run under the same
`AuthorityHook` as the typed path and every proposal goes through `authority.decide`, so a minor's spoken request
becomes needs-approval by their guardians and an adult's in-scope request executes with a receipt. When Sonic is
unavailable (no credentials, model access, SPEECH=off) the same socket degrades to a text `Agent` behind a visible
banner. The frame contract lives in `frames.py`; the dev page and browser client live in `web/static/voice/`.

Wiring: the web app adds `app.include_router(voice.router)`; `python -m household.voice.devserver` serves the router
alone for the verifier and the live check."""

from .web import build_router, router

__all__ = ["build_router", "router"]
