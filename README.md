# Mock API Integration & Incident Triage Workspace

A local webhook receiver that behaves like a real B2B integration endpoint: it authenticates callers, validates
payloads against a strict contract, returns diagnostic errors a customer can act on, and keeps a request log that
lets a support engineer trace any call by its request ID.

Built with **FastAPI**, **Pydantic v2** and **SQLite**. Runs entirely on `http://localhost:8000` at no cost.

## What it demonstrates

- **Contract-first validation**: one Pydantic model defines the payload, and FastAPI enforces it before any business logic runs.
- **Actionable errors**: every failure uses one JSON envelope. Validation errors list *every* problem at once, with the field, the reason, a fix hint and the rejected value.
- **Traceability**: each response carries an `X-Request-ID`, and `GET /api/v1/logs` shows the status, error code, latency and linked ticket for every call, successful or not.
- **Security basics**: API-key auth with a timing-safe comparison, a correct `401` + `WWW-Authenticate` challenge, `429` + `Retry-After`, and no customer emails or rejected values in the troubleshooting log.
- **Tested**: 17 pytest cases plus a Postman collection with assertions.

## Project layout

```
apitriagelog/
├── app/
│   ├── main.py        # App setup, request-ID/logging middleware, lifespan (DB init)
│   ├── routes.py      # /health, /webhook, /logs
│   ├── schemas.py     # Pydantic models: CustomerWebhook, responses, error envelope
│   ├── security.py    # X-API-Key check and simulated rate limit (FastAPI dependencies)
│   ├── errors.py      # Exception handlers that build the error envelope (400 instead of 422)
│   ├── database.py    # sqlite3 schema, inserts and log queries
│   └── config.py      # Settings, overridable through environment variables
├── examples/          # JSON bodies for curl / Postman
├── postman/           # Importable Postman collection
├── tests/             # pytest suite (uses a temp DB, never touches tickets.db)
└── requirements.txt
```

## Request lifecycle

```
Client ──► middleware: assign X-Request-ID, start timer
             └─► require_api_key ........ 401 AUTH_MISSING_API_KEY / AUTH_INVALID_API_KEY
                  └─► simulate_rate_limit ... 429 RATE_LIMITED (Retry-After: 60)
                       └─► Pydantic validates body against CustomerWebhook ... 400 VALIDATION_FAILED
                            └─► endpoint: insert into tickets ─► 200 {"status":"success","tracking_id":...}
         ◄── middleware: add X-Request-ID header, write a row to request_logs
```

## Setup

Requires Python 3.10 or newer (developed on 3.14).

**Windows (PowerShell)**

```powershell
git clone https://github.com/Auvendra/apitriagelog.git
cd apitriagelog
python -m venv .venv
.\.venv\Scripts\Activate.ps1
# If activation is blocked: Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass, then activate again
pip install -r requirements-dev.txt
python -m uvicorn app.main:app --reload
```

**macOS / Linux**

```bash
git clone https://github.com/Auvendra/apitriagelog.git
cd apitriagelog
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
python -m uvicorn app.main:app --reload
```

The server is ready when it prints `Uvicorn running on http://127.0.0.1:8000`. `tickets.db` is created on first start.
Open **http://localhost:8000/docs** for interactive Swagger docs: click **Authorize**, enter `test_secret_123` and
use *Try it out* on any endpoint.

Use `requirements.txt` instead of `requirements-dev.txt` if you don't need the test tools.

## Endpoints

| Method | Path              | Auth | Purpose                                                        |
|--------|-------------------|------|----------------------------------------------------------------|
| GET    | `/api/v1/health`  | No   | Liveness plus a database round-trip; `503` if the DB is unreachable |
| POST   | `/api/v1/webhook` | Yes  | Validate and store a customer webhook                          |
| GET    | `/api/v1/logs`    | Yes  | Recent requests, newest first. Query: `limit` (1–500, default 50), `status_code` |

### Webhook contract

| Field            | Type   | Rules                                   |
|------------------|--------|-----------------------------------------|
| `ticket_id`      | string | required, 1–64 chars                    |
| `customer_email` | string | required, valid email format            |
| `payload_type`   | string | required, 1–64 chars                    |
| `data`           | object | required, must be a JSON object         |

Unknown fields are rejected, so a typo such as `ticketId` is reported instead of silently ignored.

## Error catalog (support runbook)

Every error response looks like this:

```json
{
  "status": "error",
  "error_code": "VALIDATION_FAILED",
  "message": "Request failed validation with 2 issue(s). See 'details'.",
  "request_id": "4aa1865fc6344159b4b7917e51eaf382",
  "details": [
    {
      "location": "body",
      "field": "customer_email",
      "issue": "value is not a valid email address: An email address must have an @-sign.",
      "error_type": "value_error",
      "received": "jane.doe-at-acmecorp.com"
    },
    {
      "location": "body",
      "field": "payload_type",
      "issue": "Field required",
      "error_type": "missing",
      "hint": "Add this required field to the JSON body."
    }
  ]
}
```

| HTTP | `error_code`           | Typical cause                                         | What to tell the customer                          |
|------|------------------------|-------------------------------------------------------|----------------------------------------------------|
| 400  | `VALIDATION_FAILED`    | Missing/extra field, bad email, malformed JSON, wrong Content-Type | Fix each item in `details[]`, then resend  |
| 401  | `AUTH_MISSING_API_KEY` | No `X-API-Key` header                                 | Add the header; check proxies aren't stripping it  |
| 401  | `AUTH_INVALID_API_KEY` | Wrong, expired or whitespace-padded key               | Re-copy the key from the credentials vault          |
| 404  | `NOT_FOUND`            | Wrong path (e.g. `/webhooks`, `/api/v2/...`)          | Use `/api/v1/webhook`                               |
| 405  | `METHOD_NOT_ALLOWED`   | `GET` sent to `/webhook`                              | Send `POST`                                         |
| 429  | `RATE_LIMITED`         | `X-Simulate-Rate-Limit: true` was sent                | Back off for `Retry-After` seconds, then retry      |
| 500  | `INTERNAL_ERROR`       | Unhandled server bug                                  | Escalate with the `request_id`                      |
| 503  | `DATABASE_UNAVAILABLE` | Health check could not reach SQLite                   | Infrastructure issue; check disk and file locks     |

## Try it

### Option A: Postman

**Import → File →** `postman/mock-api-triage.postman_collection.json`. It contains 9 requests (200, two kinds of
400, two kinds of 401, 429 and two log queries), each with test assertions. Use **Run collection** to run them all and
see every check pass. `baseUrl` and `apiKey` are collection variables.

You can also paste any curl command below into **Import → Raw text**.

### Option B: curl

**1. Success → `200 OK`**

```bash
curl -i -X POST http://localhost:8000/api/v1/webhook \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: test_secret_123' \
  -d '{"ticket_id": "TCK-1001", "customer_email": "jane.doe@acmecorp.com", "payload_type": "ticket.created", "data": {"priority": "high", "subject": "SSO login failing for EU tenant"}}'
```

```json
{"status": "success", "tracking_id": "7b410280-751a-402e-8b28-7e0bf6fa014d"}
```

**2. Bad email and missing `payload_type` → `400 Bad Request`**

```bash
curl -i -X POST http://localhost:8000/api/v1/webhook \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: test_secret_123' \
  -d '{"ticket_id": "TCK-1002", "customer_email": "jane.doe-at-acmecorp.com", "data": {"priority": "low"}}'
```

The response lists both problems; see the error envelope above.

**3. Missing API key → `401 Unauthorized`**

```bash
curl -i -X POST http://localhost:8000/api/v1/webhook \
  -H 'Content-Type: application/json' \
  -d '{"ticket_id": "TCK-1001", "customer_email": "jane.doe@acmecorp.com", "payload_type": "ticket.created", "data": {}}'
```

```json
{"status": "error", "error_code": "AUTH_MISSING_API_KEY", "message": "Missing X-API-Key header.", "request_id": "..."}
```

**Bonus: simulated rate limit → `429` with `Retry-After: 60`**

```bash
curl -i -X POST http://localhost:8000/api/v1/webhook \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: test_secret_123' \
  -H 'X-Simulate-Rate-Limit: true' \
  -d '{"ticket_id": "TCK-1001", "customer_email": "jane.doe@acmecorp.com", "payload_type": "ticket.created", "data": {}}'
```

### Windows PowerShell note

In Windows PowerShell 5.1, `curl` is an alias for `Invoke-WebRequest`, and inline JSON quoting breaks when passed
to native programs. Call `curl.exe` explicitly and send the body from a file:

```powershell
curl.exe -i -X POST http://localhost:8000/api/v1/webhook -H "Content-Type: application/json" -H "X-API-Key: test_secret_123" --data "@examples/200_success.json"
curl.exe -i -X POST http://localhost:8000/api/v1/webhook -H "Content-Type: application/json" -H "X-API-Key: test_secret_123" --data "@examples/400_invalid_payload.json"
curl.exe -i -X POST http://localhost:8000/api/v1/webhook -H "Content-Type: application/json" --data "@examples/200_success.json"
```

## Triage walkthrough

A customer reports *"your webhook keeps rejecting us"* and quotes request ID `case-4711`
(clients can set their own `X-Request-ID`; otherwise the server generates one).

```bash
# 1. What failed recently?
curl -s "http://localhost:8000/api/v1/logs?status_code=400&limit=10" -H 'X-API-Key: test_secret_123'

# 2. Find the entry with "request_id": "case-4711" and read error_detail:
#    field=customer_email, error_type=value_error -> their system is sending a malformed email.

# 3. Confirm a fix: a successful retry shows status_code 200 with tracking_id and ticket_id filled in.
```

To inspect the raw tables: `sqlite3 tickets.db "SELECT * FROM request_logs ORDER BY id DESC LIMIT 5;"`
(or open `tickets.db` in DB Browser for SQLite).

## Tests

```bash
python -m pytest -v
```

The suite covers every status code, the order of checks (auth, then rate limit, then validation), line/column
reporting for malformed JSON, request-ID correlation, log filtering and PII redaction, the 500 envelope, and that
the OpenAPI docs match the actual 400 behavior.

## Configuration

| Env var          | Default            | Purpose               |
|------------------|--------------------|-----------------------|
| `TRIAGE_API_KEY` | `test_secret_123`  | Accepted `X-API-Key`  |
| `TRIAGE_DB_PATH` | `./tickets.db`     | SQLite file location  |

## Design notes

- **400, not 422.** FastAPI returns `422 Unprocessable Entity` for validation errors by default. This contract
  specifies `400`, so `errors.py` replaces the handler and `main.py` removes the stale 422 entries from the OpenAPI spec.
- **Check order.** Dependencies run auth, then rate limiting, then body validation, so unauthenticated callers learn
  nothing about the schema. One framework caveat: FastAPI parses JSON *before* running dependencies, so a body that
  isn't valid JSON gets `400` even without an API key. In production, auth usually happens at the API gateway before
  the app sees the request.
- **Privacy in logs.** `/logs` never returns `customer_email`, and stored validation errors keep the field and reason
  but not the rejected value. The caller still sees their own value in the live response.
- **SQLite access.** Parameterized queries only, one short-lived connection per operation (sqlite3 connections can't
  be shared across threads), WAL mode so log reads don't block webhook writes, and blocking DB calls kept off the
  async event loop.
