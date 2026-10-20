"""SQLite persistence layer, built on Python's standard-library sqlite3 module (no ORM).

Each operation opens a short-lived connection. FastAPI runs the sync endpoints in a
worker thread pool, and a sqlite3 connection must not be shared between threads, so
connection-per-operation is the simplest thread-safe pattern for a local service.
"""
import json
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from app.config import settings
from app.schemas import CustomerWebhook

SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    tracking_id    TEXT PRIMARY KEY,
    ticket_id      TEXT NOT NULL,
    customer_email TEXT NOT NULL,
    payload_type   TEXT NOT NULL,
    data           TEXT NOT NULL,      -- JSON-encoded object
    request_id     TEXT NOT NULL,
    received_at    TEXT NOT NULL       -- ISO-8601, UTC
);

CREATE TABLE IF NOT EXISTS request_logs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id   TEXT NOT NULL,
    logged_at    TEXT NOT NULL,
    method       TEXT NOT NULL,
    path         TEXT NOT NULL,
    status_code  INTEGER NOT NULL,
    latency_ms   REAL NOT NULL,
    client_ip    TEXT,
    error_code   TEXT,
    error_detail TEXT,                 -- JSON-encoded list; NULL on success
    tracking_id  TEXT                  -- set when the request created a ticket
);

CREATE INDEX IF NOT EXISTS idx_request_logs_status ON request_logs(status_code);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@contextmanager
def get_connection() -> Generator[sqlite3.Connection, None, None]:
    """Open a connection; commit on success, roll back on error, always close."""
    conn = sqlite3.connect(settings.db_path, timeout=5)
    conn.row_factory = sqlite3.Row  # rows support row["column"] and dict(row)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    with get_connection() as conn:
        conn.execute("PRAGMA journal_mode=WAL")  # readers no longer block the writer
        conn.executescript(SCHEMA)


def ping() -> None:
    with get_connection() as conn:
        conn.execute("SELECT 1")


def insert_ticket(tracking_id: str, webhook: CustomerWebhook, request_id: str) -> None:
    # "?" placeholders let sqlite3 bind the values, so customer input can never be
    # interpreted as SQL (no injection, no manual quoting).
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO tickets
                (tracking_id, ticket_id, customer_email, payload_type, data, request_id, received_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tracking_id,
                webhook.ticket_id,
                webhook.customer_email,
                webhook.payload_type,
                json.dumps(webhook.data),
                request_id,
                utc_now(),
            ),
        )


def insert_request_log(
    *,
    request_id: str,
    method: str,
    path: str,
    status_code: int,
    latency_ms: float,
    client_ip: str | None,
    error_code: str | None,
    error_detail: list[dict[str, Any]] | None,
    tracking_id: str | None,
) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO request_logs
                (request_id, logged_at, method, path, status_code, latency_ms,
                 client_ip, error_code, error_detail, tracking_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                utc_now(),
                method,
                path,
                status_code,
                latency_ms,
                client_ip,
                error_code,
                json.dumps(error_detail) if error_detail is not None else None,
                tracking_id,
            ),
        )


def fetch_recent_logs(limit: int, status_code: int | None = None) -> list[dict[str, Any]]:
    """Newest request logs first, joined to the ticket they created (if any).

    customer_email is deliberately not selected: troubleshooting views should not
    expose customer PII when ticket_id is enough to correlate.
    """
    query = """
        SELECT l.request_id, l.logged_at, l.method, l.path, l.status_code, l.latency_ms,
               l.client_ip, l.error_code, l.error_detail, l.tracking_id,
               t.ticket_id, t.payload_type
        FROM request_logs AS l
        LEFT JOIN tickets AS t ON t.tracking_id = l.tracking_id
    """
    params: list[Any] = []
    if status_code is not None:
        query += " WHERE l.status_code = ?"
        params.append(status_code)
    query += " ORDER BY l.id DESC LIMIT ?"
    params.append(limit)

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()

    logs = []
    for row in rows:
        entry = dict(row)
        entry["error_detail"] = json.loads(entry["error_detail"]) if entry["error_detail"] else None
        logs.append(entry)
    return logs
