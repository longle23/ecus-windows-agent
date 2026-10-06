from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.auth import require_api_key
from app.config import Settings, get_settings
from app.job_queue import get_job_queue
from app.models import EcusJobRequest, EcusJobResponse

router = APIRouter(tags=["jobs"])


@router.post(
    "/jobs",
    response_model=EcusJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_api_key)],
)
def create_job(
    job: EcusJobRequest,
    response: Response,
    settings: Settings = Depends(get_settings),
) -> EcusJobResponse:
    """Accept one ECUS declaration into the FIFO queue. Poll GET /jobs/{jobId}."""
    if job.action and job.action not in {"register_declaration", "send-ecus", ""}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported action: {job.action}",
        )

    if not job.jobId:
        job.jobId = f"local-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"

    decision = get_job_queue(settings).submit(job)
    if decision.code == "succeeded":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"job {job.jobId} already completed successfully",
        )
    if decision.code == "full":
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"ECUS job queue is full ({settings.job_queue_max} waiting); "
                "try again later."
            ),
        )
    if decision.code == "created":
        response.status_code = status.HTTP_202_ACCEPTED
    else:
        response.status_code = status.HTTP_200_OK
    return decision.response


@router.get(
    "/jobs/{job_id}",
    response_model=EcusJobResponse,
    dependencies=[Depends(require_api_key)],
)
def read_job(
    job_id: str,
    settings: Settings = Depends(get_settings),
) -> EcusJobResponse:
    found = get_job_queue(settings).get(job_id)
    if found is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"job {job_id} not found",
        )
    return found
