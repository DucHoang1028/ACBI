"""Exercise live authentication and role boundaries without logging credentials."""

import json
import os
import sys
from pathlib import Path

import httpx


def main() -> None:
    credentials = dict(
        line.split(": ", 1)
        for line in sys.stdin.read().splitlines()[1:]
        if ": " in line
    )
    base = "http://127.0.0.1:8000"
    role_counts = {}
    for username, expected_count in (
        ("manager", 4),
        ("sales", 2),
        ("production_a", 2),
        ("it_admin", 0),
    ):
        with httpx.Client(base_url=base) as client:
            response = client.post(
                "/api/auth/login",
                json={"username": username, "password": credentials[username]},
            )
            assert response.status_code == 200, (username, response.status_code)
            access = response.json()["access_token"]
            headers = {"Authorization": f"Bearer {access}"}
            identity = client.get("/api/auth/me", headers=headers)
            assert (
                identity.status_code == 200 and identity.json()["username"] == username
            )
            metadata = client.get("/api/metadata", headers=headers)
            assert metadata.status_code == 200
            metrics = metadata.json()["metrics"]
            assert len(metrics) == expected_count, username
            if username == "production_a":
                assert metadata.json()["factory_scope"] == [1]
                assert all(m["domain"] in {"production", "quality"} for m in metrics)
            if username == "sales":
                assert all(m["domain"] == "sales" for m in metrics)
            admin = client.get("/api/admin/status", headers=headers)
            assert admin.status_code == (200 if username == "it_admin" else 403)
            old_refresh = client.cookies["acbi_refresh"]
            rotated = client.post("/api/auth/refresh")
            assert (
                rotated.status_code == 200 and rotated.json()["access_token"] != access
            )
            assert client.get("/api/auth/me", headers=headers).status_code == 401
            with httpx.Client(
                base_url=base, cookies={"acbi_refresh": old_refresh}
            ) as replay:
                assert replay.post("/api/auth/refresh").status_code == 401
            new_headers = {"Authorization": f"Bearer {rotated.json()['access_token']}"}
            assert client.post("/api/auth/logout").status_code == 200
            assert client.get("/api/auth/me", headers=new_headers).status_code == 401
            role_counts[username] = expected_count
    with httpx.Client(base_url=base) as client:
        assert client.get("/api/metadata").status_code == 401
        assert (
            client.post(
                "/api/auth/login", json={"username": "sales", "password": "wrong"}
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/api/auth/refresh", headers={"Origin": "https://other.invalid"}
            ).status_code
            == 403
        )
    report = {
        "phase": 1,
        "status": "passed",
        "role_metric_counts": role_counts,
        "checks": [
            "login",
            "metadata scope",
            "admin boundary",
            "refresh rotation",
            "replay rejected",
            "logout revocation",
            "anonymous denied",
            "invalid login",
            "origin denied",
        ],
        "credentials_in_report": False,
    }
    output = Path(os.environ.get("ACBI_REPORT_DIR", "/tmp/acbi-phase1"))
    output.mkdir(parents=True, exist_ok=True)
    (output / "phase1-verification.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(json.dumps(report))


if __name__ == "__main__":
    main()
