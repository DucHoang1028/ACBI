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
    # Extra keys, comma-separated. Used in order; an error moves to the next key.
    groq_api_keys: SecretStr = SecretStr("")
    # Other OpenAI-compatible providers, tried in LLM_PROVIDER_ORDER; a provider
    # with no key is skipped. Keys are comma-separated.
    llm_provider_order: str = "gemini,groq,literouter"
    gemini_api_keys: SecretStr = SecretStr("")
    # Comma-separated, tried in order. A key a model is closed to rests for an hour.
    gemini_model: str = "gemini-3.6-flash,gemini-2.5-flash"
    literouter_api_key: SecretStr = SecretStr("")
    literouter_model: str = "deepseek-v3.2:free"
    # Demo only: one-click sign-in without a password (see /api/auth/demo-login).
    demo_login_enabled: bool = False
    stt_model: str = "whisper-large-v3-turbo"
    llm_requests_per_minute: int = Field(default=15, ge=1)
    llm_tokens_per_minute: int = Field(default=8000, ge=1)
    llm_max_calls_per_request: int = Field(default=3, ge=1, le=10)
    llm_max_regenerations: int = Field(default=2, ge=0, le=2)
    request_timeout_seconds: int = Field(default=30, ge=1, le=120)
    send_results_to_llm: bool = False
    # Build a Structured Intent locally when the wording is unambiguous, sparing a
    # model call. Turn off to send every question to the LLM.
    local_intent_enabled: bool = False
    # Forecasting: at least this many complete months, and a hold-out error no worse
    # than this share, otherwise the request is refused rather than answered weakly.
    forecast_min_months: int = Field(default=24, ge=12)
    forecast_max_mape: float = Field(default=0.35, gt=0, le=1)
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

    def gemini_keys(self) -> list[str]:
        raw = self.gemini_api_keys.get_secret_value().split(",")
        return list(dict.fromkeys(k.strip() for k in raw if k.strip()))

    def literouter_keys(self) -> list[str]:
        raw = self.literouter_api_key.get_secret_value().split(",")
        return list(dict.fromkeys(k.strip() for k in raw if k.strip()))

    def groq_keys(self) -> list[str]:
        """Every configured Groq key, primary first, without duplicates."""
        extra = self.groq_api_keys.get_secret_value().split(",")
        keys = [self.groq_api_key.get_secret_value(), *extra]
        return list(dict.fromkeys(k.strip() for k in keys if k.strip()))

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
