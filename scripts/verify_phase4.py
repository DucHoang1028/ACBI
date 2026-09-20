"""Phase 4: saved answers, access, voice, admin, latency and failure outcomes."""

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import httpx
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from app.ai.client import FakeLLM  # noqa: E402
from app.ai.stt import FakeSTT  # noqa: E402
from app.main import app  # noqa: E402
from verify_phase2 import intent_for  # noqa: E402


class TimedOutLLM:
    def interpret(self, *_args: Any) -> Any:
        raise httpx.ReadTimeout("simulated")


class BrokenWarehouse:
    def connect(self) -> Any:
        raise SQLAlchemyError("simulated")


def main() -> None:
    credentials = dict(
        line.split(": ", 1)
        for line in sys.stdin.read().splitlines()[1:]
        if ": " in line
    )
    questions = yaml.safe_load(
        (ROOT / "data/eval/golden_questions.yaml").read_text(encoding="utf-8")
    )["questions"]
    normal = next(q for q in questions if q["id"] == "GQ02")
    empty = next(
        q
        for q in questions
        if q["expected_status"] == "no_data" and q["user"] == "manager"
    )
    fake = FakeLLM({q["question"]: intent_for(q) for q in (normal, empty)})
    report: dict[str, Any] = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "model": "FakeLLM",
    }
    with TestClient(app) as client:
        app.state.llm = fake
        tokens: dict[str, str] = {}
        for name, password in credentials.items():
            reply = client.post(
                "/api/auth/login", json={"username": name, "password": password}
            )
            assert reply.status_code == 200, name
            tokens[name] = reply.json()["access_token"]

        def headers(name: str) -> dict[str, str]:
            return {"Authorization": f"Bearer {tokens[name]}"}

        saved = client.post(
            "/api/chat/ask",
            json={"question": normal["question"]},
            headers=headers("sales"),
        )
        body = saved.json()
        assert saved.status_code == 200 and body["status"] == "ok"
        assert body["saved"] and body["result_id"] and body["table"]
        assert body["sources"]["data_as_of"] and body["sources"]["sql"]
        assert body["sources"]["metric_versions"] == {"revenue": 1}
        reopened = client.get(
            f"/api/results/{body['result_id']}", headers=headers("sales")
        )
        assert reopened.status_code == 200 and reopened.json()["table"] == body["table"]
        assert reopened.json()["sources"] == body["sources"]
        foreign = client.get(
            f"/api/results/{body['result_id']}", headers=headers("manager")
        )
        assert foreign.status_code == 404
        assert (
            client.get("/api/conversations", headers=headers("manager")).status_code
            == 200
        )
        history = client.get("/api/conversations", headers=headers("sales")).json()
        assert any(item["id"] == body["result_id"] for item in history["conversations"])
        assert (
            client.get(
                f"/api/conversations/{body['conversation_id']}",
                headers=headers("sales"),
            ).status_code
            == 200
        )
        assert (
            client.get(
                f"/api/conversations/{body['conversation_id']}",
                headers=headers("manager"),
            ).status_code
            == 404
        )
        report["saved_result_traceable"] = True
        report["cross_user_result_denied"] = True

        no_data = client.post(
            "/api/chat/ask",
            json={"question": empty["question"]},
            headers=headers("manager"),
        ).json()
        assert no_data["status"] == "no_data" and no_data["table"] == []
        assert no_data["saved"]
        report["no_data_not_zero"] = True

        with patch(
            "app.api.chat.save_result", side_effect=SQLAlchemyError("simulated")
        ):
            partial = client.post(
                "/api/chat/ask",
                json={"question": normal["question"]},
                headers=headers("sales"),
            ).json()
        assert partial["status"] == "partial" and partial["table"]
        assert not partial["saved"]
        report["storage_failure_partial"] = True

        app.state.stt = FakeSTT("Doanh thu tháng trước là bao nhiêu?")
        voice = client.post(
            "/api/chat/voice",
            headers=headers("sales"),
            files={"file": ("recording.webm", b"sample", "audio/webm")},
            data={"language": "vi"},
        )
        assert voice.status_code == 200 and voice.json()["transcript"]
        assert (
            client.post(
                "/api/chat/voice",
                headers=headers("it_admin"),
                files={"file": ("recording.webm", b"sample", "audio/webm")},
            ).status_code
            == 403
        )
        app.state.stt = FakeSTT("")
        assert (
            client.post(
                "/api/chat/voice",
                headers=headers("sales"),
                files={"file": ("recording.webm", b"sample", "audio/webm")},
            ).status_code
            == 422
        )
        report["voice_transcript_review"] = True

        assert (
            client.get("/api/admin/users", headers=headers("sales")).status_code == 403
        )
        for path in ("users", "roles", "scopes", "audit", "status"):
            assert (
                client.get(
                    f"/api/admin/{path}", headers=headers("it_admin")
                ).status_code
                == 200
            ), path
        created = client.post(
            "/api/admin/users",
            json={"username": "phase4_" + uuid4().hex[:10], "role": "sales"},
            headers=headers("it_admin"),
        )
        assert created.status_code == 201
        temporary = created.json()
        try:
            temporary_login = client.post(
                "/api/auth/login",
                json={
                    "username": temporary["username"],
                    "password": temporary["password"],
                },
            )
            assert temporary_login.status_code == 200
            changed = client.patch(
                f"/api/admin/users/{temporary['id']}",
                json={"role": "production"},
                headers=headers("it_admin"),
            )
            assert changed.status_code == 200
            old_token = temporary_login.json()["access_token"]
            assert (
                client.get(
                    "/api/auth/me",
                    headers={"Authorization": f"Bearer {old_token}"},
                ).status_code
                == 401
            )
        finally:
            with app.state.storage.begin() as connection:
                connection.execute(
                    text("DELETE FROM app_sessions WHERE user_id=:id"),
                    {"id": temporary["id"]},
                )
                connection.execute(
                    text("DELETE FROM app_users WHERE id=:id"),
                    {"id": temporary["id"]},
                )
        report["admin_role_boundary"] = True
        report["admin_user_management"] = True

        def concurrent(_: int) -> float:
            started = time.monotonic()
            reply = client.post(
                "/api/chat/ask",
                json={"question": normal["question"]},
                headers=headers("sales"),
            )
            assert reply.status_code == 200 and reply.json()["status"] == "ok"
            return time.monotonic() - started

        with ThreadPoolExecutor(max_workers=5) as pool:
            latencies = sorted(pool.map(concurrent, range(5)))
        report["qs7_seconds"] = [round(value, 3) for value in latencies]
        report["qs7_p95_seconds"] = round(latencies[4], 3)
        assert latencies[4] < 15

        app.state.llm = TimedOutLLM()
        started = time.monotonic()
        ai_failure = client.post(
            "/api/chat/ask",
            json={"question": normal["question"]},
            headers=headers("sales"),
        )
        assert ai_failure.status_code == 503
        assert ai_failure.json()["status"] == "technical_failure"
        assert time.monotonic() - started < 30
        app.state.llm = fake
        warehouse = app.state.warehouse
        app.state.warehouse = BrokenWarehouse()
        try:
            db_failure = client.post(
                "/api/chat/ask",
                json={"question": normal["question"]},
                headers=headers("sales"),
            )
            assert db_failure.status_code == 503
            assert db_failure.json()["status"] == "technical_failure"
            assert not db_failure.json()["table"]
        finally:
            app.state.warehouse = warehouse
        report["qs8_controlled_failures"] = True

    destination = Path(os.getenv("ACBI_PHASE4_REPORT", "/tmp/acbi-phase4.json"))
    destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("Phase 4: saved results, voice, admin, concurrency and failures passed")


if __name__ == "__main__":
    main()
