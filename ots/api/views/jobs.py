from __future__ import annotations

from celery.result import AsyncResult
from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse

from ots import config
from ots.api.responses import json_error, json_ready, json_response
from ots.api.schemas import EmbeddingPopulateRequest
from ots.tasks.embeddings import populate_embeddings
from ots.worker import celery_app


def _task_payload(task: AsyncResult) -> dict:
    info = task.info if isinstance(task.info, dict) else None
    payload = {
        "jobId": task.id,
        "state": task.state,
        "ready": task.ready(),
        "successful": task.successful() if task.ready() else False,
        "failed": task.failed(),
    }
    if info is not None:
        if task.failed():
            payload["error"] = info
        else:
            payload["progress"] = info
    elif task.ready():
        payload["result"] = json_ready(task.result)
    if task.successful():
        payload["result"] = json_ready(task.result)
    if task.failed() and task.traceback:
        payload["traceback"] = task.traceback
    return payload


async def embedding_jobs_endpoint(request: Request) -> JSONResponse:
    if request.method == "GET":
        return json_response(
            {
                "broker": config.CELERY_BROKER_URL,
                "resultBackend": config.CELERY_RESULT_BACKEND,
                "taskAlwaysEager": config.CELERY_TASK_ALWAYS_EAGER,
                "enqueue": "POST /embeddings/jobs",
                "status": "GET /embeddings/jobs/{jobId}",
            }
        )
    try:
        payload = await request.json()
    except Exception:
        return json_error("Request body must be JSON")
    try:
        model = EmbeddingPopulateRequest.model_validate(payload)
    except ValidationError as exc:
        return json_error(
            "Invalid embedding job payload", status_code=422, details=exc.errors()
        )
    try:
        task = populate_embeddings.delay(model.task_payload())
    except Exception as exc:
        return json_error(f"Could not enqueue embedding job: {exc}", status_code=503)
    return json_response(
        {
            "jobId": task.id,
            "state": task.state,
            "statusUrl": f"/embeddings/jobs/{task.id}",
        },
        status_code=202,
    )


async def embedding_job_endpoint(request: Request) -> JSONResponse:
    job_id = request.path_params["job_id"]
    task = celery_app.AsyncResult(job_id)
    return json_response(_task_payload(task))
