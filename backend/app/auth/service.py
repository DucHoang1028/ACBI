"""Application-only accounts and opaque sessions. Warehouse access remains acbi_ro."""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import Engine, text

HASHER = PasswordHasher()
ACCESS_LIFETIME = timedelta(minutes=15)
REFRESH_LIFETIME = timedelta(days=7)


def migrate(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS app_users (
                id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                username text NOT NULL UNIQUE,
                password_hash text NOT NULL,
                role text NOT NULL CHECK
                  (role IN ('manager','sales','production','it_admin')),
                enabled boolean NOT NULL DEFAULT true,
                failed_attempts integer NOT NULL DEFAULT 0,
                locked_until timestamptz
            )
        """))
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS app_sessions (
                id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                user_id bigint NOT NULL REFERENCES app_users(id),
                access_hash text NOT NULL UNIQUE,
                refresh_hash text NOT NULL UNIQUE,
                access_expires timestamptz NOT NULL,
                refresh_expires timestamptz NOT NULL
            )
        """))


def digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_tokens() -> tuple[str, str]:
    return secrets.token_urlsafe(32), secrets.token_urlsafe(48)


def login(engine: Engine, username: str, password: str) -> tuple[str, str] | None:
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT id,password_hash,enabled,locked_until FROM app_users "
                    "WHERE username=:username FOR UPDATE"
                ),
                {"username": username},
            )
            .mappings()
            .first()
        )
        if row is None or not row["enabled"]:
            # Keep unknown-account attempts comparable to real password checks.
            HASHER.hash(password)
            return None
        if row["locked_until"] and row["locked_until"] > now:
            return None
        try:
            valid: bool = HASHER.verify(row["password_hash"], password)
        except VerifyMismatchError:
            valid = False
        if not valid:
            connection.execute(
                text("""
                UPDATE app_users SET failed_attempts=failed_attempts+1,
                  locked_until=CASE WHEN failed_attempts>=4 THEN :until ELSE NULL END
                WHERE id=:id
            """),
                {"id": row["id"], "until": now + timedelta(minutes=5)},
            )
            return None
        connection.execute(
            text(
                "UPDATE app_users SET failed_attempts=0,locked_until=NULL WHERE id=:id"
            ),
            {"id": row["id"]},
        )
        access, refresh = new_tokens()
        connection.execute(
            text("""
            INSERT INTO app_sessions
              (user_id,access_hash,refresh_hash,access_expires,refresh_expires)
            VALUES (:id,:access,:refresh,:access_expires,:refresh_expires)
        """),
            {
                "id": row["id"],
                "access": digest(access),
                "refresh": digest(refresh),
                "access_expires": now + ACCESS_LIFETIME,
                "refresh_expires": now + REFRESH_LIFETIME,
            },
        )
    return access, refresh


def rotate(engine: Engine, refresh: str) -> tuple[str, str] | None:
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        row = connection.execute(
            text("""
            SELECT s.id FROM app_sessions s JOIN app_users u ON u.id=s.user_id
            WHERE s.refresh_hash=:hash AND s.refresh_expires>:now AND u.enabled
            FOR UPDATE OF s
        """),
            {"hash": digest(refresh), "now": now},
        ).first()
        if row is None:
            return None
        access, renewal = new_tokens()
        connection.execute(
            text("""
            UPDATE app_sessions SET access_hash=:access,refresh_hash=:refresh,
                access_expires=:access_expires,refresh_expires=:refresh_expires
            WHERE id=:id
        """),
            {
                "id": row[0],
                "access": digest(access),
                "refresh": digest(renewal),
                "access_expires": now + ACCESS_LIFETIME,
                "refresh_expires": now + REFRESH_LIFETIME,
            },
        )
    return access, renewal


def user_for_token(engine: Engine, access: str) -> dict[str, Any] | None:
    with engine.connect() as connection:
        row = (
            connection.execute(
                text("""
            SELECT u.id,u.username,u.role FROM app_sessions s
            JOIN app_users u ON u.id=s.user_id
            WHERE s.access_hash=:hash AND s.access_expires>now() AND u.enabled
        """),
                {"hash": digest(access)},
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None


def revoke(engine: Engine, refresh: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            text("DELETE FROM app_sessions WHERE refresh_hash=:hash"),
            {"hash": digest(refresh)},
        )


def allows(role: str, domain: str, factory_id: int | None = None) -> bool:
    if role == "manager":
        return True
    if role == "sales":
        return domain == "sales" and factory_id is None
    if role == "production":
        return domain in {"production", "quality"} and factory_id == 1
    return False
