"""Logging for the API server: everything goes to stdout, which Render shows under Logs.

One line per request (method, path, status, time, body size, request id), plus story events
from the story code. Request bodies are never logged: CRIF reports carry PAN, phone numbers,
addresses and emails. Set LOG_LEVEL=DEBUG to also log health checks and static file requests.
"""
import json
import logging
import os
import sys
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

log = logging.getLogger("api")

QUIET_PATHS = ("/api/status", "/health")   # Render's health check hits these every few seconds


def setup_logging():
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
        force=True,
    )
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)   # replaced by the request log below


def _looks_like_crif(body: bytes) -> bool:
    head = body[:4000]
    return b"INDV-REPORT" in head or b"crifReport" in head


def install(app: FastAPI):
    """Request logging middleware, validation-error logging and a catch-all error log."""

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:8]
        request.state.rid = rid
        start = time.perf_counter()
        client = (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "-")).split(",")[0].strip()
        size = request.headers.get("content-length", "0")
        try:
            response = await call_next(request)
        except Exception:
            ms = (time.perf_counter() - start) * 1000
            log.exception("[%s] %s %s -> 500 in %.0f ms (unhandled error)", rid, request.method, request.url.path, ms)
            return JSONResponse({"detail": "Internal server error", "request_id": rid}, status_code=500)
        ms = (time.perf_counter() - start) * 1000
        path = request.url.path
        quiet = path in QUIET_PATHS or path.startswith("/stories/")
        level = (logging.ERROR if response.status_code >= 500 else logging.WARNING if response.status_code >= 400
                 else logging.DEBUG if quiet else logging.INFO)
        query = f"?{request.url.query}" if request.url.query else ""
        log.log(level, "[%s] %s %s%s -> %d in %.0f ms (body %s B, client %s)",
                rid, request.method, path, query, response.status_code, ms, size, client)
        response.headers["X-Request-ID"] = rid
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        rid = getattr(request.state, "rid", "-")
        # Field locations and messages only, never the submitted values.
        problems = [f"{'.'.join(str(p) for p in e.get('loc', []))}: {e.get('msg')}" for e in exc.errors()]
        body = await request.body()
        hint = None
        if request.url.path.startswith("/api/story") and not _looks_like_crif(body):
            hint = "POST /api/story takes the CRIF High Mark report (the bureau API response) as the body."
        log.warning("[%s] 422 on %s %s: %s%s", rid, request.method, request.url.path, "; ".join(problems[:6]),
                    f" | {hint}" if hint else "")
        errors = [{k: v for k, v in e.items() if k not in ("input", "ctx", "url")} for e in exc.errors()]
        content = {"detail": json.loads(json.dumps(errors, default=str)), "request_id": rid}
        if hint:
            content["hint"] = hint
        return JSONResponse(content, status_code=422)
