from typing import Optional

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


class ScheduleStep(BaseModel):
    time: str  # "HH:MM", 24-hour
    action: str
    detail: str


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    risk_level: str  # "low" | "moderate" | "high" | "extreme" | "unknown"
    escalate: bool
    schedule: Optional[list[ScheduleStep]] = None
    temperature_c: Optional[float] = None
    feels_like_c: Optional[float] = None
    humidity_pct: Optional[int] = None
    peak_heat_hour: Optional[int] = None  # 0-23, today's forecast hour with the highest temperature
    hydration_logged_ml: int = 0
    hydration_target_ml: Optional[int] = None


class ChatTurn(BaseModel):
    role: str
    content: str


class WorkerStatus(BaseModel):
    onboarded: bool
    profile: Optional[WorkerProfile] = None
    session_id: Optional[str] = None
    risk_level: Optional[str] = None
    schedule: Optional[list[ScheduleStep]] = None
    acknowledged: bool = False
    temperature_c: Optional[float] = None
    feels_like_c: Optional[float] = None
    humidity_pct: Optional[int] = None
    peak_heat_hour: Optional[int] = None
    hydration_logged_ml: int = 0
    hydration_target_ml: Optional[int] = None
    history: list[ChatTurn] = []


class HydrationLogRequest(BaseModel):
    amount_ml: int = 250


class TraceEntry(BaseModel):
    timestamp: str
    input: str
    output: str
    latency_ms: int
    model: str
    delivered_to_prism: bool


class TraceLog(BaseModel):
    session_id: str
    agent_id: str
    traces: list[TraceEntry]
