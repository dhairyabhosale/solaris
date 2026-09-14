from fastapi import APIRouter

from app.models import OnboardingRequest, OnboardingResponse, WorkerProfile
from app.services import store

router = APIRouter()


@router.post("/onboarding", response_model=OnboardingResponse)
async def onboard_worker(payload: OnboardingRequest) -> OnboardingResponse:
    profile = WorkerProfile(**payload.model_dump())
    store.save_worker(profile)

    return OnboardingResponse(
        worker_id=profile.worker_id,
        message=(
            f"Got it - {profile.work_type} worker in {profile.location}, "
            f"shift {profile.work_start}-{profile.work_end}. "
            "Message me each day for your heat safety check-in."
        ),
    )
