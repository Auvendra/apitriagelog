"""HTTP endpoints under /api/v1.

The endpoints are plain `def` (not `async def`) on purpose: sqlite3 calls block, and
FastAPI runs sync endpoints in a thread pool so they don't stall the event loop.
"""
import sqlite3
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app import database
from app.config import settings
from app.errors import APIError
from app.schemas import CustomerWebhook, ErrorResponse, HealthResponse, LogsResponse, WebhookAccepted
from app.security import require_api_key, simulate_rate_limit

VALIDATION_ERROR_DOC = {400: {"model": ErrorResponse, "description": "Request failed validation"}}

# Open routes: monitors and load balancers must reach /health without credentials.
public_router = APIRouter(prefix="/api/v1", tags=["Monitoring"])

# Every route here runs require_api_key, then simulate_rate_limit, then body validation.
protected_router = APIRouter(
    prefix="/api/v1",
    dependencies=[Depends(require_api_key), Depends(simulate_rate_limit)],
    responses={
        401: {"model": ErrorResponse, "description": "Missing or invalid X-API-Key"},
        429: {"model": ErrorResponse, "description": "Rate limited (simulated)"},
    },
)


@public_router.get(
    "/health",
    response_model=HealthResponse,
    responses={503: {"model": ErrorResponse, "description": "Database unreachable"}},
)
def health() -> HealthResponse:
    """Liveness plus a database round-trip, so a broken DB fails the check."""
    try:
        database.ping()
    except sqlite3.Error as exc:
        raise APIError(503, "DATABASE_UNAVAILABLE", f"Database check failed: {exc}") from exc
    return HealthResponse(status="ok", database="ok", version=settings.app_version, timestamp=database.utc_now())


@protected_router.post("/webhook", response_model=WebhookAccepted, tags=["Webhooks"], responses=VALIDATION_ERROR_DOC)
def receive_webhook(payload: CustomerWebhook, request: Request) -> WebhookAccepted:
    """Validate and store an inbound customer webhook."""
    # Declaring `payload: CustomerWebhook` makes FastAPI validate the JSON body before this
    # function is called. An invalid payload never gets here; it becomes a 400 in errors.py.
    tracking_id = str(uuid.uuid4())
    database.insert_ticket(tracking_id, payload, request_id=request.state.request_id)
    request.state.tracking_id = tracking_id  # links the request log entry to the stored ticket
    return WebhookAccepted(tracking_id=tracking_id)


@protected_router.get("/logs", response_model=LogsResponse, tags=["Troubleshooting"], responses=VALIDATION_ERROR_DOC)
def get_logs(
    limit: Annotated[int, Query(ge=1, le=500, description="Maximum entries to return, newest first.")] = 50,
    status_code: Annotated[int | None, Query(ge=100, le=599, description="Only entries with this HTTP status.")] = None,
) -> LogsResponse:
    """Recent API requests (successes and failures) for troubleshooting."""
    logs = database.fetch_recent_logs(limit=limit, status_code=status_code)
    return LogsResponse(count=len(logs), logs=logs)
