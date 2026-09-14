from pydantic import BaseModel, Field


class WorkerProfile(BaseModel):
    worker_id: str
    work_type: str
    work_start: str  # e.g. "07:00"
    work_end: str  # e.g. "16:00"
    location: str  # city/area, e.g. "Chennai"


class OnboardingRequest(BaseModel):
    worker_id: str = Field(
        ..., description="Any stable identifier the client chooses, e.g. a name or phone number"
    )
    work_type: str
    work_start: str
    work_end: str
    location: str


class OnboardingResponse(BaseModel):
    worker_id: str
    message: str


class ChatRequest(BaseModel):
    worker_id: str
    message: str


class ChatResponse(BaseModel):
    session_id: str
    reply: str
