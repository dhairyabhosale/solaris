"""Solaris Grid Intelligence - FastAPI application.

A read-only presentation layer over the research pipeline's outputs. It needs
no API keys and no third-party model provider: run
``python -m solaris.cli all`` once, then start this app.
"""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.routes import api

app = FastAPI(
    title="Solaris Grid Intelligence",
    description=(
        "Heat-aware electricity demand forecasting and evening grid stress "
        "analysis for India."
    ),
    version="1.0.0",
)

app.include_router(api.router, tags=["api"])


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    """Return JSON the frontend can display, while the traceback still reaches logs."""
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_error",
            "message": "Something went wrong while serving this request.",
        },
    )


@app.get("/health")
async def health() -> dict:
    """Liveness plus which pipeline artefacts are present."""
    from app.services import results

    checks: dict[str, bool] = {}
    for name, loader in (
        ("overview", results.overview),
        ("models", results.models),
        ("series", results.available_range),
        ("methodology", results.methodology),
    ):
        try:
            loader()
            checks[name] = True
        except results.ArtefactMissing:
            checks[name] = False

    return {
        "status": "ok" if all(checks.values()) else "degraded",
        "artefacts": checks,
        "hint": None if all(checks.values())
                else "Run `python -m solaris.cli report` to export the web bundle.",
    }


STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
