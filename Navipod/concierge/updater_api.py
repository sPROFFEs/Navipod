from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
import asyncio

import operations_service


app = FastAPI()


class ApplyUpdatePayload(BaseModel):
    job_id: int
    triggered_by: str | None = None


def _require_internal_auth(authorization: str | None):
    expected = f"Bearer {operations_service.get_internal_updater_token()}"
    if authorization != expected:
        raise HTTPException(status_code=403, detail="Forbidden")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/internal/update/apply")
async def internal_apply_update(payload: ApplyUpdatePayload, authorization: str | None = Header(default=None)):
    _require_internal_auth(authorization)
    asyncio.create_task(
        asyncio.to_thread(
            operations_service.run_apply_update_job_from_updater,
            payload.job_id,
            payload.triggered_by,
        )
    )
    return {"status": "accepted", "job_id": payload.job_id}
