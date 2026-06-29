from __future__ import annotations

from celery import Celery

from ots import config

celery_app = Celery(
    "ots",
    broker=config.CELERY_BROKER_URL,
    backend=config.CELERY_RESULT_BACKEND,
    include=["ots.tasks.embeddings"],
)

celery_app.conf.update(
    task_always_eager=config.CELERY_TASK_ALWAYS_EAGER,
    task_track_started=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    result_extended=True,
    database_table_names={
        "task": "celery_taskmeta",
        "group": "celery_groupmeta",
    },
)
