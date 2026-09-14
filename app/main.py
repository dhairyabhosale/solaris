from fastapi import FastAPI

from app.routes import chat, onboarding

app = FastAPI(title="Solaris - Heat Safety Companion", version="0.1.0")

app.include_router(onboarding.router, tags=["onboarding"])
app.include_router(chat.router, tags=["chat"])


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
