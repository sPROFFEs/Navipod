from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
import asyncio
import html
import secrets

import database
import operations_service


app = FastAPI()


class ApplyUpdatePayload(BaseModel):
    job_id: int
    triggered_by: str | None = None


def _require_internal_auth(authorization: str | None):
    expected = f"Bearer {operations_service.get_internal_updater_token()}"
    if authorization != expected:
        raise HTTPException(status_code=403, detail="Forbidden")


def _require_monitor_access(job_id: int, access: str | None):
    expected = operations_service.get_update_monitor_token(job_id)
    if not access or not secrets.compare_digest(access, expected):
        raise HTTPException(status_code=403, detail="Forbidden")


def _load_job(job_id: int):
    db = database.SessionLocal()
    try:
        return operations_service.get_admin_job(db, job_id)
    finally:
        db.close()


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_progress_page(job_id: int, access: str | None = Query(default=None)):
    _require_monitor_access(job_id, access)
    job = _load_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    initial_status = html.escape(job.get("status") or "queued", quote=True)
    initial_phase = html.escape((job.get("details") or {}).get("phase") or "queued", quote=True)
    initial_progress = html.escape(str((job.get("details") or {}).get("progress") or 0), quote=True)
    message = html.escape(job.get("message") or "Waiting...", quote=False)
    job_type = html.escape(job.get("job_type") or "unknown", quote=False)
    triggered_by = html.escape(job.get("triggered_by") or "system", quote=False)
    endpoint = html.escape(f"/updater/api/jobs/{job_id}?access={access}", quote=True)
    success_url = html.escape("/admin/system?msg=Update applied successfully", quote=True)
    fallback_url = html.escape("/admin/system", quote=True)
    logs_html = "".join(
        f"<div class=\"update-log-item\"><time>{html.escape((item or {}).get('at') or '', quote=False)}</time><div>{html.escape((item or {}).get('message') or '', quote=False)}</div></div>"
        for item in (job.get("details") or {}).get("logs", [])
    )
    if not logs_html:
        logs_html = "<div class=\"update-log-item\"><time></time><div>Waiting for first update event...</div></div>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Update Progress</title>
    <link rel="stylesheet" href="/assets/css/admin_system.css">
    <style>
        body {{
            margin: 0;
            min-height: 100vh;
            background: #0b0d12;
            color: #f5f7fb;
            font-family: "IBM Plex Sans", system-ui, sans-serif;
        }}
        .update-progress-page {{
            max-width: 980px;
            margin: 0 auto;
            padding: 32px 20px 48px;
        }}
        .update-banner {{
            margin: 0 0 20px;
            padding: 14px 16px;
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 14px;
            background: rgba(255,255,255,0.04);
            color: #cfd7e6;
        }}
        .update-banner strong {{
            color: #fff;
        }}
        a.update-back {{
            color: #9ad1ff;
            text-decoration: none;
        }}
    </style>
</head>
<body>
    <div class="update-progress-page">
        <div class="update-banner">
            <strong>Update monitor served by updater.</strong>
            The main app may briefly stop responding while services rebuild. Keep this page open.
        </div>
        <div class="admin-container update-shell"
             data-update-job-id="{job_id}"
             data-initial-status="{initial_status}"
             data-initial-phase="{initial_phase}"
             data-initial-progress="{initial_progress}"
             data-job-endpoint="{endpoint}"
             data-job-success-url="{success_url}"
             data-job-fallback-url="{fallback_url}">
            <section class="update-header">
                <div>
                    <h1>Job #{job_id}</h1>
                    <p>Background execution state for the requested administrative operation.</p>
                </div>
                <a href="/admin/system" class="update-back">Back to Monitor</a>
            </section>

            <section class="update-panel">
                <div class="update-panel-body">
                    <div class="update-meta-grid">
                        <div class="update-meta">
                            <div class="update-meta-label">Type</div>
                            <div class="update-meta-value">{job_type}</div>
                        </div>
                        <div class="update-meta">
                            <div class="update-meta-label">Status</div>
                            <div class="update-meta-value"><span id="job-status" class="update-status-pill {initial_status}">{initial_status}</span></div>
                        </div>
                        <div class="update-meta">
                            <div class="update-meta-label">Triggered by</div>
                            <div class="update-meta-value">{triggered_by}</div>
                        </div>
                    </div>

                    <div>
                        <div class="update-progress-head">
                            <span class="update-meta-label">Progress</span>
                            <span id="job-progress-label" class="update-meta-value">{initial_progress}%</span>
                        </div>
                        <div class="update-progress-track">
                            <div id="job-progress-bar" class="update-progress-bar" style="width: {initial_progress}%;"></div>
                        </div>
                    </div>

                    <div class="update-phase">
                        <div>
                            <div class="update-meta-label">Current phase</div>
                            <strong id="job-phase">{initial_phase}</strong>
                        </div>
                        <div>
                            <div class="update-meta-label">Latest message</div>
                            <strong id="job-message">{message}</strong>
                        </div>
                    </div>
                </div>
            </section>

            <section class="update-panel">
                <div class="update-panel-body">
                    <div class="update-meta-label">Activity log</div>
                    <div id="job-log" class="update-log">{logs_html}</div>
                </div>
            </section>
        </div>
    </div>
    <script type="module">
        import {{ initUpdateProgress }} from "/assets/js/modules/update_progress.js";
        initUpdateProgress(document);
    </script>
</body>
</html>"""


@app.get("/api/jobs/{job_id}")
async def get_job_status(job_id: int, access: str | None = Query(default=None)):
    _require_monitor_access(job_id, access)
    job = _load_job(job_id)
    if not job:
        return JSONResponse({"error": "Job not found"}, status_code=404)
    return JSONResponse(job)


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
