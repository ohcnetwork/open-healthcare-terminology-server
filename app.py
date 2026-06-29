from __future__ import annotations

from starlette.applications import Starlette
from starlette.middleware import Middleware

from ots.api.auth import ApiKeyMiddleware
from ots.api.routes import routes


def create_app() -> Starlette:
    return Starlette(
        debug=True, routes=routes, middleware=[Middleware(ApiKeyMiddleware)]
    )


app = create_app()
