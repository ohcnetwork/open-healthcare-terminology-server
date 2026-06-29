from __future__ import annotations

import os

DEFAULT_API_KEY = "open-terminology-server-dev-key"
DEFAULT_API_KEY_HEADER = "x-api-key"
DEFAULT_PUBLIC_PATHS = frozenset({"/docs", "/openapi.json", "/favicon.ico"})

DEFAULT_DATABASE_URL = "postgresql://ots:ots@127.0.0.1:5432/ots"
DEFAULT_SQLALCHEMY_DATABASE_URL = "postgresql+psycopg://ots:ots@127.0.0.1:5432/ots"

DEFAULT_TERMINOLOGY_KEY = "snomed"
DEFAULT_EMBEDDING_DIMENSIONS = 768
DEFAULT_EMBEDDING_PROVIDER = "ollama"
DEFAULT_EMBEDDING_PARALLEL_REQUESTS = 1
DEFAULT_QUERY_EMBEDDING_CACHE_SIZE = 1024
DEFAULT_DISABLE_QUERY_EMBEDDING_CACHE = True
DEFAULT_CELERY_TASK_ALWAYS_EAGER = False


def _env_str(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value


def _env_int(name: str, default: int) -> int:
    value = _env_str(name)
    return int(value) if value is not None else default


def _env_float(name: str, default: float) -> float:
    value = _env_str(name)
    return float(value) if value is not None else default


def _env_bool(name: str, default: bool) -> bool:
    value = _env_str(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_csv(name: str, default: frozenset[str]) -> frozenset[str]:
    value = _env_str(name)
    if value is None:
        return default
    return frozenset(item.strip() for item in value.split(",") if item.strip())


def _sqlalchemy_url(database_url: str) -> str:
    explicit_url = _env_str("OTS_SQLALCHEMY_DATABASE_URL")
    if explicit_url:
        return explicit_url
    if database_url.startswith("postgresql://"):
        return f"postgresql+psycopg://{database_url.removeprefix('postgresql://')}"
    return database_url or DEFAULT_SQLALCHEMY_DATABASE_URL


def _celery_broker_url(sqlalchemy_database_url: str) -> str:
    explicit_url = _env_str("OTS_CELERY_BROKER_URL")
    if explicit_url:
        return explicit_url
    return f"sqla+{sqlalchemy_database_url}"


def _celery_result_backend(sqlalchemy_database_url: str) -> str:
    explicit_url = _env_str("OTS_CELERY_RESULT_BACKEND")
    if explicit_url:
        return explicit_url
    return f"db+{sqlalchemy_database_url}"


API_KEY = _env_str("OTS_API_KEY", DEFAULT_API_KEY) or DEFAULT_API_KEY
API_KEY_HEADER = (_env_str("OTS_API_KEY_HEADER", DEFAULT_API_KEY_HEADER) or DEFAULT_API_KEY_HEADER).lower()
PUBLIC_PATHS = _env_csv("OTS_PUBLIC_PATHS", DEFAULT_PUBLIC_PATHS)

DATABASE_URL = _env_str("OTS_DATABASE_URL", DEFAULT_DATABASE_URL) or DEFAULT_DATABASE_URL
SQLALCHEMY_DATABASE_URL = _sqlalchemy_url(DATABASE_URL)
CELERY_BROKER_URL = _celery_broker_url(SQLALCHEMY_DATABASE_URL)
CELERY_RESULT_BACKEND = _celery_result_backend(SQLALCHEMY_DATABASE_URL)
CELERY_TASK_ALWAYS_EAGER = _env_bool(
    "OTS_CELERY_TASK_ALWAYS_EAGER",
    DEFAULT_CELERY_TASK_ALWAYS_EAGER,
)

TERMINOLOGY_KEY = _env_str("OTS_TERMINOLOGY", DEFAULT_TERMINOLOGY_KEY) or DEFAULT_TERMINOLOGY_KEY
_EMBEDDING_DIMENSIONS_VALUE = _env_str("OTS_EMBEDDING_DIMENSIONS")
EMBEDDING_DIMENSIONS_OVERRIDE = (
    int(_EMBEDDING_DIMENSIONS_VALUE) if _EMBEDDING_DIMENSIONS_VALUE is not None else None
)
EMBEDDING_DIMENSIONS = EMBEDDING_DIMENSIONS_OVERRIDE or DEFAULT_EMBEDDING_DIMENSIONS
EMBEDDING_PROVIDER = _env_str("OTS_EMBEDDING_PROVIDER", DEFAULT_EMBEDDING_PROVIDER) or DEFAULT_EMBEDDING_PROVIDER
EMBEDDING_MODEL = _env_str("OTS_EMBEDDING_MODEL")
EMBEDDING_MODEL_KEY = _env_str("OTS_EMBEDDING_MODEL_KEY")
EMBEDDING_PARALLEL_REQUESTS = _env_int(
    "OTS_EMBEDDING_PARALLEL_REQUESTS",
    DEFAULT_EMBEDDING_PARALLEL_REQUESTS,
)
QUERY_EMBEDDING_CACHE_SIZE = _env_int(
    "OTS_QUERY_EMBEDDING_CACHE_SIZE",
    DEFAULT_QUERY_EMBEDDING_CACHE_SIZE,
)
DISABLE_QUERY_EMBEDDING_CACHE = _env_bool(
    "OTS_DISABLE_QUERY_EMBEDDING_CACHE",
    DEFAULT_DISABLE_QUERY_EMBEDDING_CACHE,
)


def set_database_url(value: str) -> None:
    global CELERY_BROKER_URL, CELERY_RESULT_BACKEND, DATABASE_URL, SQLALCHEMY_DATABASE_URL

    os.environ["OTS_DATABASE_URL"] = value
    DATABASE_URL = value
    SQLALCHEMY_DATABASE_URL = _sqlalchemy_url(DATABASE_URL)
    CELERY_BROKER_URL = _celery_broker_url(SQLALCHEMY_DATABASE_URL)
    CELERY_RESULT_BACKEND = _celery_result_backend(SQLALCHEMY_DATABASE_URL)
