"""Read-only JSON API over the research artefacts."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Response

from app.config import get_settings
from app.services import results

router = APIRouter(prefix="/api")


def _cache(response: Response) -> None:
    response.headers["Cache-Control"] = (
        f"public, max-age={get_settings().api_cache_seconds}"
    )


def _guard(fn, *args, **kwargs):
    """Turn a missing pipeline artefact into an actionable 503, not a 500."""
    try:
        return fn(*args, **kwargs)
    except results.ArtefactMissing as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "pipeline_artefact_missing",
                "message": str(exc),
                "command": exc.command,
            },
        ) from exc


@router.get("/overview")
async def get_overview(response: Response) -> dict:
    """Headline KPIs, data coverage and model version."""
    _cache(response)
    return _guard(results.overview)


@router.get("/models")
async def get_models(response: Response) -> dict:
    """Per-model test metrics, for the performance comparison view."""
    _cache(response)
    return _guard(results.models)


@router.get("/series")
async def get_series(
    response: Response,
    start: str | None = Query(None, description="ISO date, IST"),
    end: str | None = Query(None, description="ISO date, IST"),
    hours: int | None = Query(None, ge=24, le=24 * 365),
) -> dict:
    """Hourly actual vs predicted demand over the held-out test period."""
    _cache(response)
    settings = get_settings()
    limit = min(hours or settings.max_series_hours, settings.max_series_hours)
    try:
        return _guard(results.series, start=start, end=end, max_hours=limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid date: {exc}") from exc


@router.get("/series/range")
async def get_series_range(response: Response) -> dict:
    """The date range the series endpoint can serve."""
    _cache(response)
    return _guard(results.available_range)


@router.get("/daily-profile")
async def get_daily_profile(response: Response) -> dict:
    """Mean demand, solar, wind and residual load by hour of day."""
    _cache(response)
    return _guard(results.daily_profile)


@router.get("/evening-stress")
async def get_evening_stress(response: Response) -> dict:
    """Evening residual-load ramp statistics."""
    _cache(response)
    return _guard(results.evening_stress)


@router.get("/explainability")
async def get_explainability(response: Response) -> dict:
    """Feature importance for the headline model, with its caveats."""
    _cache(response)
    return _guard(results.explainability)


@router.get("/methodology")
async def get_methodology(response: Response) -> dict:
    """Sources, definitions, sensitivity analyses and limitations."""
    _cache(response)
    return _guard(results.methodology)


@router.get("/figures")
async def list_figures(response: Response) -> dict:
    """Names of the pre-rendered Plotly figures the app can display."""
    _cache(response)
    return {"figures": results.available_figures()}


@router.get("/figures/{name}")
async def get_figure(name: str, response: Response) -> dict:
    """A saved Plotly figure specification, ready to render."""
    _cache(response)
    return _guard(results.figure, name)
