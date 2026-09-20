"""User-owned conversation slots and denial audit."""

import json
from typing import Any

from sqlalchemy import Engine, text


def migrate(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS chat_context (
                id text PRIMARY KEY,
                user_id bigint NOT NULL REFERENCES app_users(id),
                slots jsonb NOT NULL,
                pending_question text,
                updated_at timestamptz NOT NULL DEFAULT now()
            )
        """))
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS access_audit (
                id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                user_id bigint NOT NULL REFERENCES app_users(id),
                request_id text NOT NULL,
                outcome text NOT NULL,
                metric_id text,
                created_at timestamptz NOT NULL DEFAULT now()
            )
        """))


def get_context(
    engine: Engine, conversation_id: str, user_id: int
) -> dict[str, Any] | None:
    with engine.connect() as connection:
        row = (
            connection.execute(
                text("""
            SELECT slots,pending_question FROM chat_context
            WHERE id=:id AND user_id=:user_id
        """),
                {"id": conversation_id, "user_id": user_id},
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None


def save_context(
    engine: Engine,
    conversation_id: str,
    user_id: int,
    slots: dict[str, Any],
    pending_question: str | None,
) -> None:
    with engine.begin() as connection:
        connection.execute(
            text("""
            INSERT INTO chat_context(id,user_id,slots,pending_question)
            VALUES (:id,:user_id,CAST(:slots AS jsonb),:pending)
            ON CONFLICT (id) DO UPDATE SET slots=EXCLUDED.slots,
                pending_question=EXCLUDED.pending_question,updated_at=now()
            WHERE chat_context.user_id=EXCLUDED.user_id
        """),
            {
                "id": conversation_id,
                "user_id": user_id,
                "slots": json.dumps(slots),
                "pending": pending_question,
            },
        )


def audit(
    engine: Engine, user_id: int, request_id: str, outcome: str, metric_id: str | None
) -> None:
    with engine.begin() as connection:
        connection.execute(
            text("""
            INSERT INTO access_audit(user_id,request_id,outcome,metric_id)
            VALUES (:user_id,:request_id,:outcome,:metric_id)
        """),
            {
                "user_id": user_id,
                "request_id": request_id,
                "outcome": outcome,
                "metric_id": metric_id,
            },
        )
