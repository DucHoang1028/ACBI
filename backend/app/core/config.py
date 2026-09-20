from datetime import date

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="deploy/.env", extra="ignore")

    warehouse_host: str = "host.docker.internal"
    warehouse_port: int = 5432
    warehouse_database: str = "Adventureworks"
    warehouse_user: str = "acbi_ro"
    warehouse_password: SecretStr
    warehouse_sslmode: str = "verify-full"
    app_db_host: str = "db"
    app_db_user: str = "acbi_app"
    app_db_password: SecretStr
    app_db_name: str = "acbi"
    data_as_of: date | None = None
    data_dir: str = "data"
    llm_provider: str = "groq"
    llm_model: str = "openai/gpt-oss-120b"
    groq_api_key: SecretStr = SecretStr("")
    stt_model: str = "whisper-large-v3-turbo"
    llm_requests_per_minute: int = Field(default=15, ge=1)
    llm_tokens_per_minute: int = Field(default=8000, ge=1)
    llm_max_calls_per_request: int = Field(default=3, ge=1, le=10)
    llm_max_regenerations: int = Field(default=2, ge=0, le=2)
    request_timeout_seconds: int = Field(default=30, ge=1, le=120)
    send_results_to_llm: bool = False
    external_results_enabled: bool = False
    external_metadata_enabled: bool = False

    @field_validator("data_as_of", mode="before")
    @classmethod
    def blank_anchor(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("warehouse_user")
    @classmethod
    def readonly_identity(cls, value: str) -> str:
        if value != "acbi_ro":
            raise ValueError("Warehouse connections must use acbi_ro")
        return value

    def warehouse_url(self) -> URL:
        return URL.create(
            "postgresql+psycopg",
            username=self.warehouse_user,
            password=self.warehouse_password.get_secret_value(),
            host=self.warehouse_host,
            port=self.warehouse_port,
            database=self.warehouse_database,
            query={"sslmode": self.warehouse_sslmode},
        )

    def application_url(self) -> URL:
        return URL.create(
            "postgresql+psycopg",
            username=self.app_db_user,
            password=self.app_db_password.get_secret_value(),
            host=self.app_db_host,
            database=self.app_db_name,
        )
