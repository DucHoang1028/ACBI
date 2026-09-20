"""Local user administration; warehouse permissions remain fixed and read-only."""

import secrets
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.api.auth import current_user
from app.auth.service import HASHER

router = APIRouter(prefix="/api/admin")
Role = Literal["manager", "sales", "production", "it_admin"]


def admin(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if user["role"] != "it_admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    return user


class NewUser(BaseModel):
    username: str = Field(min_length=3, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    role: Role


class UserUpdate(BaseModel):
    role: Role | None = None
    enabled: bool | None = None


@router.get("/users")
def users(request: Request, _: dict[str, Any] = Depends(admin)) -> dict[str, Any]:
    with request.app.state.storage.connect() as connection:
        rows = connection.execute(text("""
            SELECT id,username,role,enabled FROM app_users ORDER BY username
        """)).mappings().all()
    return {"users": [dict(row) for row in rows]}


@router.post("/users", status_code=201)
def create_user(
    body: NewUser, request: Request, _: dict[str, Any] = Depends(admin)
) -> dict[str, Any]:
    password = secrets.token_urlsafe(24)
    try:
        with request.app.state.storage.begin() as connection:
            user_id = connection.execute(
                text("""
                    INSERT INTO app_users(username,password_hash,role)
                    VALUES (:username,:password_hash,:role) RETURNING id
                """),
                {
                    "username": body.username,
                    "password_hash": HASHER.hash(password),
                    "role": body.role,
                },
            ).scalar_one()
    except IntegrityError:
        raise HTTPException(status_code=409, detail="Username already exists") from None
    return {
        "id": user_id,
        "username": body.username,
        "role": body.role,
        "password": password,
    }


@router.patch("/users/{user_id}")
def update_user(
    user_id: int,
    body: UserUpdate,
    request: Request,
    current: dict[str, Any] = Depends(admin),
) -> dict[str, Any]:
    if body.role is None and body.enabled is None:
        raise HTTPException(status_code=422, detail="No changes supplied")
    if user_id == current["id"] and (
        body.enabled is False or body.role != "it_admin" and body.role is not None
    ):
        raise HTTPException(
            status_code=422, detail="Cannot remove your own administrator access"
        )
    with request.app.state.storage.begin() as connection:
        row = (
            connection.execute(
                text("SELECT id,role,enabled FROM app_users WHERE id=:id FOR UPDATE"),
                {"id": user_id},
            )
            .mappings()
            .first()
        )
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
        role = body.role or row["role"]
        enabled = body.enabled if body.enabled is not None else row["enabled"]
        connection.execute(
            text("UPDATE app_users SET role=:role,enabled=:enabled WHERE id=:id"),
            {"id": user_id, "role": role, "enabled": enabled},
        )
        if role != row["role"] or not enabled:
            connection.execute(
                text("DELETE FROM app_sessions WHERE user_id=:id"), {"id": user_id}
            )
    return {"id": user_id, "role": role, "enabled": enabled}


@router.get("/roles")
def roles(_: dict[str, Any] = Depends(admin)) -> dict[str, Any]:
    return {
        "roles": [
            {"id": "manager", "domains": ["sales", "production", "quality"]},
            {"id": "sales", "domains": ["sales"]},
            {"id": "production", "domains": ["production", "quality"]},
            {"id": "it_admin", "domains": []},
        ]
    }


@router.get("/scopes")
def scopes(request: Request, _: dict[str, Any] = Depends(admin)) -> dict[str, Any]:
    with request.app.state.storage.connect() as connection:
        rows = connection.execute(text("""
            SELECT id,username,role FROM app_users ORDER BY username
        """)).mappings().all()
    return {
        "scopes": [
            {
                "user_id": row["id"],
                "username": row["username"],
                "factories": (
                    [1]
                    if row["role"] == "production"
                    else "all" if row["role"] == "manager" else []
                ),
            }
            for row in rows
        ]
    }


@router.get("/audit")
def audit(request: Request, _: dict[str, Any] = Depends(admin)) -> dict[str, Any]:
    with request.app.state.storage.connect() as connection:
        rows = connection.execute(text("""
            SELECT a.request_id,u.username,a.outcome,a.metric_id,a.created_at
            FROM access_audit a JOIN app_users u ON u.id=a.user_id
            ORDER BY a.created_at DESC LIMIT 100
        """)).mappings().all()
    return {
        "audit": [
            {**dict(row), "created_at": row["created_at"].isoformat()} for row in rows
        ]
    }
