"""History and Audit: saved answers, traceability and the access audit log."""

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
    domain: str | None = None,
) -> str:
    result_id = str(uuid4())
    domain = domain or (
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


def conversation_results(
    engine: Engine, user_id: int, conversation_id: str, role: str
) -> list[dict[str, Any]]:
    """Every saved result of one owned conversation, oldest first."""
    with engine.connect() as connection:
        rows = (
            connection.execute(
                text("""
            SELECT id,domain,factory_id,question,payload FROM saved_results
            WHERE user_id=:user_id AND conversation_id=:conversation_id
            ORDER BY created_at ASC LIMIT 200
            """),
                {"user_id": user_id, "conversation_id": conversation_id},
            )
            .mappings()
            .all()
        )
    return [dict(row) for row in rows if permitted(dict(row), role)]


def build_transcript(
    saved: list[dict[str, Any]], turns: list[dict[str, str]]
) -> list[dict[str, Any]]:
    """Saved answers in order, with clarification turns (never saved) between them.

    The conversation context remembers only the last few turns; older saved
    results are placed before them.
    """

    def answered(item: dict[str, Any]) -> dict[str, Any]:
        payload = dict(item["payload"])
        payload.update(saved=True, result_id=item["id"])
        return {"question": item["question"], "answer": payload}

    def clarification(turn: dict[str, str]) -> dict[str, Any]:
        return {
            "question": turn["question"],
            "answer": {
                "status": "needs_clarification",
                "message": turn["answer"],
                "answer_text": None,
                "table": [],
                "viz_config": None,
                "sources": None,
                "saved": False,
            },
        }

    first = next((t for t in turns if not t.get("answer")), None)
    start = next(
        (
            i
            for i, s in enumerate(saved)
            if first and s["question"] == first["question"]
        ),
        len(saved),
    )
    transcript = [answered(s) for s in saved[:start]]
    position = start
    for turn in turns:
        if turn.get("answer"):
            transcript.append(clarification(turn))
            continue
        match = next(
            (
                i
                for i in range(position, len(saved))
                if saved[i]["question"] == turn["question"]
            ),
            None,
        )
        if match is not None:
            transcript += [answered(s) for s in saved[position : match + 1]]
            position = match + 1
    return transcript + [answered(s) for s in saved[position:]]


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


def latest_in_conversation(
    engine: Engine, user_id: int, conversation_id: str, role: str
) -> dict[str, Any] | None:
    """Newest saved result of the caller's own conversation, if still permitted."""
    with engine.connect() as connection:
        row = (
            connection.execute(
                text("""
            SELECT id,metric_id,domain,factory_id,payload FROM saved_results
            WHERE user_id=:user_id AND conversation_id=:conversation_id
            ORDER BY created_at DESC LIMIT 1
            """),
                {"user_id": user_id, "conversation_id": conversation_id},
            )
            .mappings()
            .first()
        )
    return dict(row) if row and permitted(dict(row), role) else None


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
