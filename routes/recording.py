from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from config import RECORDING_WORKER_SECRET
from recording_service import recording_manager


router = APIRouter(
    prefix="/recording",
)


class StartRecordingRequest(BaseModel):
    room: str
    job_id: str
    s3_prefix: str


class StopRecordingRequest(BaseModel):
    job_id: str


def verify_secret(
    secret: str | None,
):
    if not secret:
        raise HTTPException(
            status_code=401,
            detail="Missing worker secret",
        )

    if secret != RECORDING_WORKER_SECRET:
        raise HTTPException(
            status_code=403,
            detail="Invalid worker secret",
        )


@router.post("/start")
async def start_recording(
    request: StartRecordingRequest,
    x_worker_secret: str | None = Header(
        default=None,
    ),
):
    verify_secret(x_worker_secret)

    try:
        job = await recording_manager.start(
            room=request.room,
            job_id=request.job_id,
            s3_prefix=request.s3_prefix,
        )

    except RuntimeError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        )

    return {
        "status": "starting",
        "job_id": job.job_id,
        "room": job.room,
    }


@router.post("/stop")
async def stop_recording(
    request: StopRecordingRequest,
    x_worker_secret: str | None = Header(
        default=None,
    ),
):
    verify_secret(x_worker_secret)

    try:
        job = await recording_manager.stop(
            request.job_id
        )

    except RuntimeError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        )

    return {
        "status": job.status,
        "job_id": job.job_id,
    }


@router.get("/{job_id}")
async def recording_status(
    job_id: str,
    x_worker_secret: str | None = Header(
        default=None,
    ),
):
    verify_secret(x_worker_secret)

    status = recording_manager.get_status(
        job_id
    )

    if status is None:
        raise HTTPException(
            status_code=404,
            detail="Recording not found",
        )

    return status