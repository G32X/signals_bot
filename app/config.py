# app/config.py
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    APP_NAME: str = Field(default="Tech Signals Bot")
    TIMEZONE: str = Field(default="UTC")

    TELEGRAM_BOT_TOKEN: str = Field(default="")
    ADMIN_TOKEN: str = Field(default="")
    DATABASE_URL: str = Field(default="sqlite:///./signals.db")

    # Feature switches
    ENABLE_TELEGRAM: bool = Field(default=True)
    ENABLE_SCHEDULER: bool = Field(default=True)

    # TwelveData throttling (naudojama data.py)
    TD_MAX_PER_MINUTE: int = Field(default=8)
    TD_MAX_PER_DAY: int = Field(default=800)

    # Shutdown
    SHUTDOWN_TIMEOUT_SECONDS: int = Field(default=5)

    # Signals / scanning
    DEFAULT_TIMEFRAMES: str = Field(default="1h,1d")
    DEFAULT_WATCHLIST: str = Field(default="AAPL,MSFT,NVDA")
    MARKETCAP_LIMIT: int = Field(default=300_000_000)  # 300 mln USD

    # Auto-filtering watchlist (jei norėsi – galima užpildyti iš NASDAQ/kitur)
    AUTO_FILTER_TECH: bool = Field(default=False)
    WATCHLIST_REFRESH_ON_START: bool = Field(default=False)

    # Scheduler crons (UTC arba pagal TIMEZONE)
    SCHED_CRON_1H: str = Field(default="*/30 * * * *")   # kas 30 min (demo)
    SCHED_CRON_1D: str = Field(default="0 20 * * MON-FRI")  # kasdien 20:00 UTC darbo dienomis

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True)

settings = Settings()
