"""Application settings for the Solaris Grid Intelligence web app.

The app is a read-only presentation layer over artefacts that
``python -m solaris.cli all`` has already produced. It needs no API keys, no
LLM provider and no database: if the files under ``reports/`` and
``data/processed/`` exist, the app can serve them.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # How long browsers may cache API responses. The underlying data only
    # changes when the pipeline is re-run, so a long cache is correct.
    api_cache_seconds: int = 300

    # Hours of test-period data the time-series endpoint will return at once.
    max_series_hours: int = 24 * 120


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
