from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.routes import chat, onboarding

app = FastAPI(title="Solaris - Heat Safety Companion", version="0.1.0")

app.include_router(onboarding.router, tags=["onboarding"])
app.include_router(chat.router, tags=["chat"])


# Starlette still re-raises after this, so the traceback reaches the server logs;
# the client just gets JSON the frontend can show instead of plain-text "Internal Server Error".
@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={"detail": "Something went wrong on Solaris's side. Please try again."},
    )


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
