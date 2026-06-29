from __future__ import annotations

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from ots import config


class ApiKeyMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = str(scope.get("path") or "")
        method = str(scope.get("method") or "")
        if method == "OPTIONS" or path in config.PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return
        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        api_key_header = config.API_KEY_HEADER
        expected_api_key = config.API_KEY
        api_key = headers.get(api_key_header)
        authorization = headers.get("authorization", "")
        bearer_key = authorization.removeprefix("Bearer ").strip()
        if expected_api_key in (api_key, bearer_key):
            await self.app(scope, receive, send)
            return
        response = JSONResponse(
            {
                "error": f"Missing or invalid API key. Send it in the {api_key_header!r} header."
            },
            status_code=401,
        )
        await response(scope, receive, send)
