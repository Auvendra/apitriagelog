"""Pydantic models: the contract for every request and response body.

FastAPI reads these type hints to (1) parse and validate incoming JSON before the
endpoint runs, (2) serialize responses and (3) generate the OpenAPI docs at /docs.
"""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class CustomerWebhook(BaseModel):
    """Inbound webhook from a B2B customer system."""

    model_config = ConfigDict(
        extra="forbid",  # unknown keys (e.g. "ticketId" instead of "ticket_id") are rejected, not silently dropped
        str_strip_whitespace=True,
        json_schema_extra={
            "examples": [
                {
                    "ticket_id": "TCK-1001",
                    "customer_email": "jane.doe@acmecorp.com",
                    "payload_type": "ticket.created",
                    "data": {"priority": "high", "subject": "SSO login failing for EU tenant"},
                }
            ]
        },
    )

    ticket_id: str = Field(min_length=1, max_length=64, description="Customer-side ticket reference.")
    customer_email: EmailStr = Field(description="Contact email. Must be a syntactically valid address.")
    payload_type: str = Field(min_length=1, max_length=64, description="Event type, e.g. ticket.created.")
    data: dict[str, Any] = Field(description="Event body. Must be a JSON object.")


class WebhookAccepted(BaseModel):
    status: Literal["success"] = "success"
    tracking_id: str


class ErrorItem(BaseModel):
    location: str = Field(description="Where the problem is: body, header, query or path.")
    field: str | None = Field(default=None, description="Dotted path to the offending field, e.g. data.priority.")
    issue: str
    error_type: str = Field(description="Machine-readable Pydantic error type, e.g. missing, value_error.")
    hint: str | None = None
    received: Any | None = Field(default=None, description="The rejected value (scalars only).")


class ErrorResponse(BaseModel):
    status: Literal["error"] = "error"
    error_code: str
    message: str
    request_id: str
    details: list[ErrorItem] | None = None


class HealthResponse(BaseModel):
    status: Literal["ok"]
    database: Literal["ok"]
    version: str
    timestamp: str


class LogEntry(BaseModel):
    request_id: str
    logged_at: str
    method: str
    path: str
    status_code: int
    latency_ms: float
    client_ip: str | None
    error_code: str | None
    error_detail: list[dict[str, Any]] | None
    tracking_id: str | None
    ticket_id: str | None
    payload_type: str | None


class LogsResponse(BaseModel):
    count: int
    logs: list[LogEntry]
