"""ACBI API startup, warehouse checks, metadata and internal storage."""

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, Request
from sqlalchemy import create_engine, text

from app.ai.client import GroqClient
from app.ai.stt import GroqSTT
from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.history import router as history_router
from app.api.voice import router as voice_router
from app.auth.service import migrate
from app.conversation.service import migrate as migrate_chat
from app.core.config import Settings
from app.core.warehouse import inspect_anchor, warehouse_engine
from app.history.service import migrate as migrate_history
from app.metadata.dictionary import approved_metrics, load_dictionary
from app.metadata.retrieval import BM25Retriever
from app.metadata.vocabulary import Vocabulary, load_members
from app.metadata.vocabulary import install as install_vocabulary

logger = logging.getLogger("acbi.startup")


def startup(app: FastAPI) -> None:
    """Connect, migrate and load metadata (also called by api/index.py)."""
    settings = Settings()  # type: ignore[call-arg]
    warehouse = warehouse_engine(settings)
    storage = create_engine(
        settings.application_url(),
        hide_parameters=True,
        pool_pre_ping=True,
        connect_args={"prepare_threshold": None},
    )
    try:
        anchor = inspect_anchor(warehouse, settings.data_as_of)
        dictionary = load_dictionary(Path(settings.data_dir))
        migrate(storage)
        migrate_chat(storage)
        migrate_history(storage)
        # Phase 0 metadata snapshot, not the Phase 1 auth/domain schema.
        with storage.begin() as connection:
            connection.execute(text("""
                CREATE TABLE IF NOT EXISTS phase0_metadata (
                    id text PRIMARY KEY, payload jsonb NOT NULL,
                    updated_at timestamptz NOT NULL DEFAULT now()
                )
            """))
            connection.execute(
                text("""
                INSERT INTO phase0_metadata(id,payload)
                VALUES ('dictionary',CAST(:p AS jsonb))
                ON CONFLICT (id) DO UPDATE SET payload=EXCLUDED.payload,updated_at=now()
            """),
                {"p": json.dumps(dictionary)},
            )
        app.state.storage = storage
        app.state.settings = settings
        examples = yaml.safe_load(
            (
                Path(settings.data_dir) / "business_dictionary/sql_examples.yaml"
            ).read_text(encoding="utf-8")
        )["examples"]
        app.state.retriever = BM25Retriever(dictionary, examples)
        app.state.warehouse = warehouse
        # Names, synonyms and dimension members come from the dictionary and the data.
        vocab = Vocabulary(dictionary)
        load_members(warehouse, vocab)
        install_vocabulary(vocab)
        app.state.llm = GroqClient(settings) if settings.has_llm_keys() else None
        app.state.stt = GroqSTT(settings) if settings.groq_keys() else None
        app.state.dictionary = dictionary
        app.state.readiness = {
            "phase": 4,
            "status": "phase_4",
            **anchor,
            "approved_metric_count": len(approved_metrics(dictionary)),
            "proposed_metric_count": sum(
                any(d["approvalStatus"] == "proposed" for d in m["definitions"])
                for m in dictionary["businessMetrics"]
            ),
            "llm_provider": settings.llm_provider,
            "llm_model": settings.llm_model,
            "llm_enabled": app.state.llm is not None,
            "advanced_analysis_enabled": bool(
                app.state.llm is not None and settings.external_metadata_enabled
            ),
        }
        logger.info("Warehouse anchor checked: %s", anchor)
    except BaseException:
        warehouse.dispose()
        storage.dispose()
        raise


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    startup(app)
    try:
        yield
    finally:
        app.state.warehouse.dispose()
        app.state.storage.dispose()


app = FastAPI(title="ACBI Phase 4", lifespan=lifespan)
app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(history_router)
app.include_router(voice_router)
app.include_router(admin_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/bootstrap")
def bootstrap(request: Request) -> dict[str, Any]:
    return dict(request.app.state.readiness)
