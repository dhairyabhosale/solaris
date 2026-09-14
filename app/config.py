from datetime import date, datetime
from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    groq_api_key: str = ""
    # llama-3.3-70b-versatile is deprecated on Groq's free tier (shut down
    # 2026-08-16) - gpt-oss-120b is Groq's own recommended replacement, and
    # one of the few models that support strict structured outputs, which
    # llm.py relies on for parseable risk_level/escalate. Check
    # https://console.groq.com/docs/rate-limits before changing this.
    groq_model: str = "openai/gpt-oss-120b"

    # PRISM (Block Convey) live tracing - from prism.blockconvey.com/onboarding.
    # Empty api_key means "not configured": chat_completion() skips trace
    # delivery entirely, the app works the same either way. This is a
    # side-channel trace POST after each model call, not a proxy the call
    # itself routes through (that was an earlier, pre-onboarding guess).
    prismtrace_api_key: str = ""
    prismtrace_project_id: str = ""
    prismtrace_host: str = "https://prism-api-prod.up.railway.app"

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
