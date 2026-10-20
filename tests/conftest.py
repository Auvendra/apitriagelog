import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Point the app at a throwaway database so tests never touch the real tickets.db.
    monkeypatch.setattr(settings, "db_path", tmp_path / "test.db")
    with TestClient(app) as test_client:  # "with" runs the lifespan, which creates the tables
        yield test_client


@pytest.fixture
def auth():
    return {"X-API-Key": settings.api_key}


@pytest.fixture
def valid_payload():
    return {
        "ticket_id": "TCK-1001",
        "customer_email": "jane.doe@acmecorp.com",
        "payload_type": "ticket.created",
        "data": {"priority": "high", "subject": "SSO login failing for EU tenant"},
    }
