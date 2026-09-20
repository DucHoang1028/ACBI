"""Conversation Management: user-owned slots, turns and pending clarifications."""

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
                turns jsonb NOT NULL DEFAULT '[]'::jsonb,
                pending_question text,
                updated_at timestamptz NOT NULL DEFAULT now()
            )
        """))
        connection.execute(text("""
            ALTER TABLE chat_context ADD COLUMN IF NOT EXISTS turns jsonb NOT NULL
            DEFAULT '[]'::jsonb
        """))


def get_context(
    engine: Engine, conversation_id: str, user_id: int
) -> dict[str, Any] | None:
    with engine.connect() as connection:
        row = (
            connection.execute(
                text("""
            SELECT slots,turns,pending_question FROM chat_context
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
    turns: list[dict[str, str]] | None = None,
) -> None:
    with engine.begin() as connection:
        connection.execute(
            text("""
            INSERT INTO chat_context(id,user_id,slots,turns,pending_question)
            VALUES (:id,:user_id,CAST(:slots AS jsonb),CAST(:turns AS jsonb),:pending)
            ON CONFLICT (id) DO UPDATE SET slots=EXCLUDED.slots,
                turns=EXCLUDED.turns,
                pending_question=EXCLUDED.pending_question,updated_at=now()
            WHERE chat_context.user_id=EXCLUDED.user_id
        """),
            {
                "id": conversation_id,
                "user_id": user_id,
                "slots": json.dumps(slots),
                "turns": json.dumps(turns or []),
                "pending": pending_question,
            },
        )


def next_turns(
    prior: dict[str, Any] | None, question: str, answer_text: str | None
) -> list[dict[str, str]]:
    turns = list((prior or {}).get("turns") or [])
    turns.append({"question": question, "answer": answer_text or ""})
    return turns[-6:]
