"""A bare FastAPI app for the voice slice alone: the /api/voice router plus the dev page and browser client.

    python -m household.voice.devserver --port 8020

Used by scripts/verify_voice.py and for the one live Nova Sonic check; the product web app includes the same router."""

from __future__ import annotations

import argparse
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..config import Settings
from ..store import LedgerStore
from .web import STATIC_VOICE, build_router


def create_app(store: LedgerStore | None = None, settings: Settings | None = None, uploads_dir: Path | None = None) -> FastAPI:
    app = FastAPI(title="Household voice (dev)", version="0.1.0")
    app.include_router(build_router(store, settings, uploads_dir))

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_VOICE / "dev.html")

    app.mount("/static/voice", StaticFiles(directory=STATIC_VOICE), name="voice-static")
    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the household voice router and its dev page.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8020)
    args = parser.parse_args(argv)
    import uvicorn

    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
