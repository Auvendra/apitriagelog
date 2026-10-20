"""Application entry point: builds the FastAPI app and wires middleware, handlers and routes.

Run with:  python -m uvicorn app.main:app --reload
"""
import logging
import re
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool

from app import database
from app.config import settings
from app.errors import error_response, register_exception_handlers
from app.routes import protected_router, public_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s")
logger = logging.getLogger("triage")

# A client-supplied X-Request-ID is reused only if it is safe to store and print.
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
# Reading the log shouldn't add to it, and health checks are polled too often to be useful.
UNLOGGED_PATHS = {"/api/v1/logs", "/api/v1/health"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    database.init_db()  # runs once at startup: creates tickets.db and its tables if missing
    logger.info("Database ready at %s", settings.db_path)
    yield


app = FastAPI(
    title="Mock API Integration & Incident Triage Workspace",
    version=settings.app_version,
    description=(
        "Local mock of a B2B webhook receiver. Click **Authorize** and enter `test_secret_123` "
        "to call the protected endpoints. Every response carries an `X-Request-ID`; look it up "
        "via `GET /api/v1/logs` to trace what happened."
    ),
    lifespan=lifespan,
)
register_exception_handlers(app)
app.include_router(public_router)
app.include_router(protected_router)


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Give every request an ID, time it and record /api/ calls in request_logs."""
    incoming_id = request.headers.get("X-Request-ID", "")
    request_id = incoming_id if REQUEST_ID_PATTERN.match(incoming_id) else uuid.uuid4().hex
    request.state.request_id = request_id
    started = time.perf_counter()

    try:
        response = await call_next(request)
    except Exception:
        # Only errors no exception handler claimed end up here; the client still gets the envelope.
        logger.exception("Unhandled error request_id=%s", request_id)
        response = error_response(
            request, 500, "INTERNAL_ERROR", "Unexpected server error. Quote the request_id when reporting this."
        )

    latency_ms = round((time.perf_counter() - started) * 1000, 2)
    response.headers["X-Request-ID"] = request_id

    path = request.url.path
    if path.startswith("/api/") and path not in UNLOGGED_PATHS:
        error_code = getattr(request.state, "error_code", None)
        if response.status_code >= 400:
            logger.warning(
                "request_id=%s %s %s -> %d %s", request_id, request.method, path, response.status_code, error_code
            )
        try:
            # sqlite3 blocks, so run the insert in a worker thread rather than on the event loop.
            await run_in_threadpool(
                database.insert_request_log,
                request_id=request_id,
                method=request.method,
                path=path,
                status_code=response.status_code,
                latency_ms=latency_ms,
                client_ip=request.client.host if request.client else None,
                error_code=error_code,
                error_detail=getattr(request.state, "error_detail", None),
                tracking_id=getattr(request.state, "tracking_id", None),
            )
        except Exception:
            # Losing a log line must never turn a successful request into a failed one.
            logger.exception("Could not write request log request_id=%s", request_id)

    return response


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/docs")


def openapi_without_422() -> dict[str, Any]:
    """FastAPI documents validation errors as 422; this API returns 400, so drop the stale entries."""
    if app.openapi_schema is None:
        schema = FastAPI.openapi(app)
        for path_item in schema["paths"].values():
            for operation in path_item.values():
                operation.get("responses", {}).pop("422", None)
        for name in ("HTTPValidationError", "ValidationError"):
            schema.get("components", {}).get("schemas", {}).pop(name, None)
    return app.openapi_schema


app.openapi = openapi_without_422
