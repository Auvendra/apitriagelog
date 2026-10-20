"""Request guards, implemented as FastAPI dependencies.

A dependency is a function FastAPI calls before the endpoint; if it raises, the
endpoint never runs. They are attached at router level (see routes.py), so every
protected route gets the same checks in the same order: auth, then rate limit.
"""
import secrets
from typing import Annotated

from fastapi import Header, Security
from fastapi.security import APIKeyHeader

from app.config import settings
from app.errors import APIError

# auto_error=False: raise our own 401 with an error_code instead of FastAPI's default.
# Declaring the header through APIKeyHeader also adds an "Authorize" button to /docs.
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False, description="Shared secret for this mock API.")

AUTH_CHALLENGE = {"WWW-Authenticate": 'ApiKey header="X-API-Key"'}


async def require_api_key(api_key: Annotated[str | None, Security(api_key_header)]) -> None:
    if not api_key:
        raise APIError(401, "AUTH_MISSING_API_KEY", "Missing X-API-Key header.", headers=AUTH_CHALLENGE)
    # compare_digest takes the same time wherever the first mismatch is, so response
    # timing can't be used to guess the key one character at a time.
    if not secrets.compare_digest(api_key.encode(), settings.api_key.encode()):
        raise APIError(401, "AUTH_INVALID_API_KEY", "X-API-Key is not valid.", headers=AUTH_CHALLENGE)


async def simulate_rate_limit(
    x_simulate_rate_limit: Annotated[str | None, Header(description="Send `true` to force a 429 response.")] = None,
) -> None:
    # FastAPI maps the parameter name to the header "X-Simulate-Rate-Limit" (underscores -> hyphens).
    if x_simulate_rate_limit is not None and x_simulate_rate_limit.strip().lower() == "true":
        raise APIError(
            429,
            "RATE_LIMITED",
            "Rate limit exceeded (simulated). Retry after 60 seconds.",
            headers={"Retry-After": "60"},
        )
