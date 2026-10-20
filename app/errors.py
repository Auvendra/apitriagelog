"""Uniform error envelope and the exception handlers that produce it.

Every non-2xx response has the same shape, so a client integration (or a support
engineer reading logs) only ever has to parse one format:

    {"status": "error", "error_code": "...", "message": "...", "request_id": "...", "details": [...]}
"""
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.schemas import ErrorItem, ErrorResponse

# Fallback codes for HTTP errors raised by the framework itself (unknown route, wrong method...).
DEFAULT_ERROR_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
    503: "SERVICE_UNAVAILABLE",
}

# Fix-it hints keyed by Pydantic error type, returned next to the raw validator message.
VALIDATION_HINTS = {
    "missing": "Add this required field to the JSON body.",
    "extra_forbidden": "Unknown field. Names are snake_case and case-sensitive; compare against the schema at /docs.",
    "string_type": "Send this value as a JSON string (wrapped in double quotes).",
    "string_too_short": "This field cannot be empty.",
    "string_too_long": "Shorten this value; see the max length in the schema at /docs.",
    "dict_type": "Send a JSON object ({...}) here, not an array, string or number.",
    "model_attributes_type": "The body must be a JSON object. Check that Content-Type is application/json.",
    "json_invalid": "The body is not valid JSON. Look for trailing commas, single quotes or unquoted keys.",
}


class APIError(StarletteHTTPException):
    """An HTTPException that also carries a machine-readable error_code."""

    def __init__(self, status_code: int, error_code: str, message: str, headers: dict[str, str] | None = None):
        super().__init__(status_code=status_code, detail=message, headers=headers)
        self.error_code = error_code


def error_response(
    request: Request,
    status_code: int,
    error_code: str,
    message: str,
    details: list[ErrorItem] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    # Stash the outcome on request.state so the logging middleware can persist it.
    # "received" values are kept out of the stored log: the caller sees what they sent,
    # the log keeps only which field failed and why.
    request.state.error_code = error_code
    request.state.error_detail = (
        [item.model_dump(exclude={"received"}, exclude_none=True) for item in details] if details else None
    )
    body = ErrorResponse(
        error_code=error_code,
        message=message,
        request_id=getattr(request.state, "request_id", "unknown"),
        details=details,
    )
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json", exclude_none=True), headers=headers)


def _received_value(err: dict[str, Any]) -> Any:
    """Echo back the rejected value only when it is a scalar tied to a specific field."""
    value = err.get("input")
    if err["type"] == "missing" or len(err.get("loc", ())) < 2:
        return None
    if isinstance(value, str):
        return value[:200]
    if isinstance(value, (bool, int, float)):
        return value
    return None


def _format_validation_error(err: dict[str, Any], raw_body: Any) -> ErrorItem:
    loc = err.get("loc", ())
    location = str(loc[0]) if loc else "request"

    if err["type"] == "json_invalid":
        # FastAPI reports a character offset; convert it to line/column so it can be
        # matched against the body as shown in Postman or an editor.
        position = loc[1] if len(loc) > 1 and isinstance(loc[1], int) else 0
        text = raw_body if isinstance(raw_body, str) else ""
        line = text.count("\n", 0, position) + 1
        column = position - text.rfind("\n", 0, position)
        reason = err.get("ctx", {}).get("error", "JSON decode error")
        return ErrorItem(
            location=location,
            issue=f"Invalid JSON: {reason} (line {line}, column {column})",
            error_type="json_invalid",
            hint=VALIDATION_HINTS["json_invalid"],
        )

    return ErrorItem(
        location=location,
        field=".".join(str(part) for part in loc[1:]) or None,
        issue=err["msg"],
        error_type=err["type"],
        hint=VALIDATION_HINTS.get(err["type"]),
        received=_received_value(err),
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # FastAPI's default here is 422 with a terse list; this API contract specifies 400
    # with one readable entry per problem, so the client can fix everything in one pass.
    details = [_format_validation_error(err, exc.body) for err in exc.errors()]
    return error_response(
        request,
        400,
        "VALIDATION_FAILED",
        f"Request failed validation with {len(details)} issue(s). See 'details'.",
        details=details,
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    error_code = getattr(exc, "error_code", None) or DEFAULT_ERROR_CODES.get(exc.status_code, "HTTP_ERROR")
    return error_response(request, exc.status_code, error_code, str(exc.detail), headers=exc.headers)


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
