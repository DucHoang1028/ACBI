"""Create initial local users once; write generated credentials to a private file."""

import os
import secrets
import sys
from pathlib import Path

import yaml
from argon2 import PasswordHasher
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))
from app.auth.service import migrate  # noqa: E402
from app.core.config import Settings  # noqa: E402


def main() -> None:
    settings = Settings()  # type: ignore[call-arg]
    engine = create_engine(settings.application_url(), hide_parameters=True)
    migrate(engine)
    specs = yaml.safe_load(
        (ROOT / "data/business_dictionary/users.yaml").read_text(encoding="utf-8")
    )["users"]
    created = []
    with engine.begin() as connection:
        for spec in specs:
            username = spec["username"]
            exists = connection.execute(
                text("SELECT 1 FROM app_users WHERE username=:u"), {"u": username}
            ).first()
            if exists:
                continue
            password = secrets.token_urlsafe(24)
            connection.execute(
                text(
                    "INSERT INTO app_users(username,password_hash,role) "
                    "VALUES (:u,:p,:r)"
                ),
                {
                    "u": username,
                    "p": PasswordHasher().hash(password),
                    "r": spec["role"],
                },
            )
            created.append(f"{username}: {password}")
    if created:
        output = Path(
            os.environ.get("ACBI_CREDENTIALS_PATH", "/tmp/acbi-seed-credentials.txt")
        )
        output.write_text(
            "Initial local accounts (keep private):\n" + "\n".join(created) + "\n",
            encoding="utf-8",
        )
        os.chmod(output, 0o600)
    print(f"Created {len(created)} accounts. Passwords were not logged.")
    engine.dispose()


if __name__ == "__main__":
    main()
