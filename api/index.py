"""Vercel entry point: the FastAPI app, started once per cold instance."""

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("DATA_DIR", str(ROOT / "data"))

from app.main import app, startup  # noqa: E402

# Vercel does not reliably run ASGI lifespan events, so start up at import time.
startup(app)


@asynccontextmanager
async def _already_started(_: object):  # type: ignore[no-untyped-def]
    yield


app.router.lifespan_context = _already_started
