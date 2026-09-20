"""User-owned saved answers; role and factory scope are rechecked on read."""

import json
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, text

from app.auth.service import allows


def migrate(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS saved_results (
                id text PRIMARY KEY,
                user_id bigint NOT NULL REFERENCES app_users(id),
                conversation_id text NOT NULL REFERENCES chat_context(id),
                request_id text NOT NULL UNIQUE,
                metric_id text NOT NULL,
                domain text NOT NULL,
                factory_id integer,
                question text NOT NULL,
                payload jsonb NOT NULL,
                created_at timestamptz NOT NULL DEFAULT now()
            )
        """))
        connection.execute(text("""
            CREATE INDEX IF NOT EXISTS saved_results_owner_time
            ON saved_results(user_id,created_at DESC)
        """))


def permitted(row: dict[str, Any], role: str) -> bool:
    if role == "manager":
        return True
    if role == "production":
        return row["factory_id"] == 1 and allows(role, row["domain"], 1)
    return row["factory_id"] is None and allows(role, row["domain"])


def save(
    engine: Engine,
    user_id: int,
    question: str,
    metric_id: str,
    factory_id: int | None,
    payload: dict[str, Any],
) -> str:
    result_id = str(uuid4())
    domain = (
        "sales"
        if metric_id in {"revenue", "sales_growth"}
        else "quality" if metric_id == "defect_rate" else "production"
    )
    with engine.begin() as connection:
        connection.execute(
            text("""
            INSERT INTO saved_results
              (id,user_id,conversation_id,request_id,metric_id,domain,
               factory_id,question,payload)
            VALUES
              (:id,:user_id,:conversation_id,:request_id,:metric_id,:domain,
               :factory_id,:question,CAST(:payload AS jsonb))
            """),
            {
                "id": result_id,
                "user_id": user_id,
                "conversation_id": payload["conversation_id"],
                "request_id": payload["request_id"],
                "metric_id": metric_id,
                "domain": domain,
                "factory_id": factory_id,
                "question": question,
                "payload": json.dumps(payload),
            },
        )
    return result_id


def list_owned(engine: Engine, user_id: int, role: str) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        rows = (
            connection.execute(
                text("""
            SELECT id,conversation_id,question,metric_id,domain,factory_id,created_at
            FROM saved_results WHERE user_id=:user_id
            ORDER BY created_at DESC LIMIT 200
            """),
                {"user_id": user_id},
            )
            .mappings()
            .all()
        )
    return [
        {
            "id": row["id"],
            "conversation_id": row["conversation_id"],
            "question": row["question"],
            "metric_id": row["metric_id"],
            "created_at": row["created_at"].isoformat(),
        }
        for row in rows
        if permitted(dict(row), role)
    ]


def get_owned(engine: Engine, result_id: str, user_id: int) -> dict[str, Any] | None:
    with engine.connect() as connection:
        row = (
            connection.execute(
                text("""
            SELECT id,domain,factory_id,payload FROM saved_results
            WHERE id=:id AND user_id=:user_id
            """),
                {"id": result_id, "user_id": user_id},
            )
            .mappings()
            .first()
        )
    return dict(row) if row else None
