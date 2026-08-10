from functools import cached_property

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from zoneinfo import ZoneInfo

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # =========================
    # PostgreSQL
    # =========================

    postgres_host: str = Field(alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")
    postgres_db: str = Field(alias="POSTGRES_DB")
    postgres_user: str = Field(alias="POSTGRES_USER")
    postgres_password: str = Field(alias="POSTGRES_PASSWORD")

    # =========================
    # Telegram
    # =========================

    sender_api_id: int = Field(alias="SENDER_API_ID")
    sender_api_hash: str = Field(alias="SENDER_API_HASH")

    reader_api_id: int = Field(alias="READER_API_ID")
    reader_api_hash: str = Field(alias="READER_API_HASH")
    sessions_dir: str = Field(default="sessions", alias="SESSIONS_DIR")
    sender_session: str = Field(alias="SENDER_SESSION")
    reader_session: str = Field(alias="READER_SESSION")

    admin_bot_token: str = Field(alias="ADMIN_BOT_TOKEN")
    raw_admins: str = Field(alias="ADMINS")
    dev_channel: int = Field(alias="DEV_CHANNEL")
    royal_channel: int = Field(alias="ROYAL_CHANNEL")
    test_channel: int = Field(alias="TEST_CHANNEL")


    # =========================
    # Market
    # =========================

    binance_ws: str = Field(alias="BINANCE_WS")
    bybit_ws: str = Field(alias="BYBIT_WS")
    okx_ws: str = Field(alias="OKX_WS")

    # =========================
    # Engine
    # =========================

    emergency_entry_timeout: int = Field(alias="EMERGENCY_ENTRY_TIMEOUT", default=3)
    signal_entry_timeout: int = Field(alias="SIGNAL_ENTRY_TIMEOUT", default=2)

    # =========================
    # SVG
    # =========================

    profit_template_path: str = Field(alias="PROFIT_TEMPLATE_PATH")
    entry_template_path: str = Field(alias="ENTRY_TEMPLATE_PATH")
    svg_output_dir: str = Field(alias="SVG_OUTPUT_DIR", default="generated")

    # =========================
    # Application
    # =========================

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    timezone: str = Field(default="UTC", alias="TIMEZONE")

    @cached_property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://"
            f"{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}"
            f"/{self.postgres_db}"
        )

    @cached_property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @cached_property
    def admins(self) -> list:
        return [int(x.strip()) for x in self.raw_admins.split(',') if x.strip()]

    @cached_property
    def alembic_database_url(self) -> str:
        return self.database_url.replace(
            "postgresql+asyncpg",
            "postgresql+psycopg",
        )


settings = Settings()
