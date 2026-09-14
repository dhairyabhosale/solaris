from datetime import date, datetime
from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    openrouter_api_key: str = ""
    # ":free" models rotate - check https://openrouter.ai/models?max_price=0 before a demo.
    openrouter_model: str = "nex-agi/nex-n2.5-mini:free"
    # Comma-separated; OpenRouter tries these in order if the primary is down or rate-limited upstream.
    openrouter_fallback_models: str = "dots-studio/dots-3-note-preview:free"

    prism_enabled: bool = False
    prism_api_key: str = ""
    prism_project_id: str = ""
    prism_proxy_url: str = ""

    # Redis connection string - the Vercel "Redis by Redis" marketplace
    # integration injects this automatically once connected to the
    # project. Empty means "not configured": store.py then falls back
    # to an in-memory dict (fine for local dev; not durable on serverless).
    redis_url: str = ""

    # The server runs in UTC on Vercel; "today" has to be the worker's day,
    # or sessions would roll over at 05:30 IST in the middle of a shift.
    app_timezone: str = "Asia/Kolkata"


settings = Settings()


def local_today() -> date:
    return datetime.now(ZoneInfo(settings.app_timezone)).date()
