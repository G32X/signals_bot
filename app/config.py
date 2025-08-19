from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    APP_NAME: str = Field(default="Tech Signals Bot")
    TIMEZONE: str = Field(default="UTC")

    TELEGRAM_BOT_TOKEN: str = Field(default="")
    ADMIN_TOKEN: str = Field(default="")
    DATABASE_URL: str = Field(default="sqlite:///./signals.db")

    ENABLE_TELEGRAM: bool = Field(default=True)
    ENABLE_SCHEDULER: bool = Field(default=True)

    TD_MAX_PER_MINUTE: int = Field(default=8)
    TD_MAX_PER_DAY: int = Field(default=800)
    SHUTDOWN_TIMEOUT_SECONDS: int = Field(default=5)

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True)

settings = Settings()
