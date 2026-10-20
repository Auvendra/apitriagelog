import uuid

import pytest

from app import database

WEBHOOK = "/api/v1/webhook"
LOGS = "/api/v1/logs"


def issues_by_field(response):
    return {item["field"]: item for item in response.json()["details"]}


def test_health_is_public(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["database"] == "ok"


def test_valid_webhook_is_stored(client, auth, valid_payload):
    response = client.post(WEBHOOK, json=valid_payload, headers=auth)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    uuid.UUID(body["tracking_id"])  # raises if not a valid UUID
    assert response.headers["X-Request-ID"]

    with database.get_connection() as conn:
        row = conn.execute("SELECT * FROM tickets WHERE tracking_id = ?", (body["tracking_id"],)).fetchone()
    assert row["ticket_id"] == "TCK-1001"
    assert row["customer_email"] == "jane.doe@acmecorp.com"
    assert row["received_at"]


def test_invalid_email_returns_400_with_diagnostics(client, auth, valid_payload):
    valid_payload["customer_email"] = "jane.doe-at-acmecorp.com"
    response = client.post(WEBHOOK, json=valid_payload, headers=auth)

    assert response.status_code == 400
    assert response.json()["error_code"] == "VALIDATION_FAILED"
    issue = issues_by_field(response)["customer_email"]
    assert issue["error_type"] == "value_error"
    assert "@-sign" in issue["issue"]
    assert issue["received"] == "jane.doe-at-acmecorp.com"


def test_every_problem_is_reported_at_once(client, auth, valid_payload):
    del valid_payload["ticket_id"]
    valid_payload["ticketId"] = "TCK-1001"  # camelCase typo
    valid_payload["data"] = ["not", "an", "object"]
    response = client.post(WEBHOOK, json=valid_payload, headers=auth)

    assert response.status_code == 400
    issues = issues_by_field(response)
    assert issues["ticket_id"]["error_type"] == "missing"
    assert issues["ticketId"]["error_type"] == "extra_forbidden"
    assert issues["data"]["error_type"] == "dict_type"
    assert all(item["hint"] for item in issues.values())


def test_malformed_json_reports_line_and_column(client, auth):
    body = '{\n  "ticket_id": "TCK-1"\n  "customer_email": "a@acmecorp.com"\n}'  # missing comma
    response = client.post(WEBHOOK, content=body, headers={**auth, "Content-Type": "application/json"})

    assert response.status_code == 400
    [issue] = response.json()["details"]
    assert issue["error_type"] == "json_invalid"
    assert "line 3, column 3" in issue["issue"]


def test_non_json_content_type_is_explained(client, auth, valid_payload):
    response = client.post(WEBHOOK, content=str(valid_payload), headers={**auth, "Content-Type": "text/plain"})

    assert response.status_code == 400
    [issue] = response.json()["details"]
    assert "Content-Type" in issue["hint"]


@pytest.mark.parametrize(
    "headers, expected_code",
    [
        ({}, "AUTH_MISSING_API_KEY"),
        ({"X-API-Key": "wrong_key"}, "AUTH_INVALID_API_KEY"),
    ],
)
def test_bad_credentials_return_401(client, valid_payload, headers, expected_code):
    response = client.post(WEBHOOK, json=valid_payload, headers=headers)

    assert response.status_code == 401
    assert response.json()["error_code"] == expected_code
    assert "WWW-Authenticate" in response.headers


def test_auth_runs_before_body_validation(client):
    response = client.post(WEBHOOK, json={"customer_email": "broken"})
    assert response.status_code == 401


def test_auth_runs_before_rate_limit(client, valid_payload):
    response = client.post(WEBHOOK, json=valid_payload, headers={"X-Simulate-Rate-Limit": "true"})
    assert response.status_code == 401


def test_simulated_rate_limit_returns_429(client, auth, valid_payload):
    response = client.post(WEBHOOK, json=valid_payload, headers={**auth, "X-Simulate-Rate-Limit": "true"})

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "60"
    assert response.json()["error_code"] == "RATE_LIMITED"


def test_logs_require_auth(client):
    assert client.get(LOGS).status_code == 401


def test_logs_trace_successes_and_failures(client, auth, valid_payload):
    ok = client.post(WEBHOOK, json=valid_payload, headers=auth)
    bad_payload = {**valid_payload, "customer_email": "not-an-email"}
    bad = client.post(WEBHOOK, json=bad_payload, headers={**auth, "X-Request-ID": "case-4711"})
    client.post(WEBHOOK, json=valid_payload)  # 401

    logs = client.get(LOGS, headers=auth).json()["logs"]
    assert [entry["status_code"] for entry in logs] == [401, 400, 200]

    rejected = logs[1]
    assert rejected["request_id"] == "case-4711" == bad.headers["X-Request-ID"]
    assert rejected["error_code"] == "VALIDATION_FAILED"
    assert rejected["error_detail"][0]["field"] == "customer_email"
    assert "received" not in rejected["error_detail"][0]  # rejected values aren't persisted

    accepted = logs[2]
    assert accepted["tracking_id"] == ok.json()["tracking_id"]
    assert accepted["ticket_id"] == "TCK-1001"
    assert "customer_email" not in accepted


def test_logs_filter_and_query_validation(client, auth, valid_payload):
    client.post(WEBHOOK, json=valid_payload, headers=auth)
    client.post(WEBHOOK, json={}, headers=auth)

    only_400 = client.get(LOGS, params={"status_code": 400}, headers=auth).json()
    assert only_400["count"] == 1
    assert only_400["logs"][0]["status_code"] == 400

    response = client.get(LOGS, params={"limit": 0}, headers=auth)
    assert response.status_code == 400
    assert issues_by_field(response)["limit"]["location"] == "query"


def test_unknown_route_uses_error_envelope_and_is_logged(client, auth):
    response = client.post("/api/v1/webhooks", json={}, headers=auth)  # note the typo
    assert response.status_code == 404
    assert response.json()["error_code"] == "NOT_FOUND"

    [entry] = client.get(LOGS, headers=auth).json()["logs"]
    assert entry["path"] == "/api/v1/webhooks"


def test_unhandled_error_returns_500_envelope(client, auth, valid_payload, monkeypatch):
    def explode(*args, **kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(database, "insert_ticket", explode)
    response = client.post(WEBHOOK, json=valid_payload, headers=auth)

    assert response.status_code == 500
    body = response.json()
    assert body["error_code"] == "INTERNAL_ERROR"
    assert body["request_id"] == response.headers["X-Request-ID"]
    assert "disk on fire" not in response.text  # internals are not leaked to the caller


def test_openapi_documents_400_not_422(client):
    responses = client.get("/openapi.json").json()["paths"][WEBHOOK]["post"]["responses"]
    assert "400" in responses
    assert "422" not in responses
