# apitriagelog

A fake company API that runs on your own PC. You send it webhooks (basically JSON messages from a "customer") and it either saves them or tells you exactly what's wrong with them. Every request gets logged, so you can go back and figure out what happened, which is the part support engineers actually get paid for.

Built with Python, FastAPI, Pydantic and SQLite. Runs on localhost, costs nothing.

## Why I made this

In L2/L3 support or integration work, a big chunk of the job is a customer saying "your API is broken" and you working out whose fault it actually is. It's usually one of these:

1. Their JSON is wrong (missing field, bad email, typo in a field name)
2. Their API key is missing or wrong
3. They're sending too much and getting rate limited
4. It's actually our bug

This project fakes all four so I can practice spotting them, reading the logs, and explaining the fix in plain words.

## What it does

| Endpoint | What it's for | Needs API key? |
|---|---|---|
| `GET /api/v1/health` | Checks the server and database are alive | No |
| `POST /api/v1/webhook` | Where the "customer" sends their data | Yes |
| `GET /api/v1/logs` | Shows recent requests, good and bad | Yes |

The API key is `test_secret_123` and it goes in a header called `X-API-Key`. It's a fake key for local testing, not a real secret.

> Note: a **header** is extra info that travels with a request, separate from the body. Think envelope vs the letter inside.

A webhook that gets accepted looks like this:

```json
{
  "ticket_id": "TCK-1001",
  "customer_email": "jane.doe@acmecorp.com",
  "payload_type": "ticket.created",
  "data": {
    "priority": "high",
    "subject": "SSO login failing for EU tenant"
  }
}
```

All four fields are required. `customer_email` has to be a proper email format and `data` has to be an object (the `{ }` kind, not a list). Extra fields get rejected too, so if someone sends `ticketId` instead of `ticket_id` they find out straight away, instead of the field just silently disappearing.

If it's good you get back:

```json
{"status": "success", "tracking_id": "7b410280-751a-402e-8b28-7e0bf6fa014d"}
```

The `tracking_id` is a random ID for that saved ticket.

## Running it

You need Python 3.10 or newer.

**Windows (PowerShell).** Run these one at a time, Enter after each:

```powershell
git clone https://github.com/Auvendra/apitriagelog.git
cd apitriagelog
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

**Mac / Linux**

```bash
git clone https://github.com/Auvendra/apitriagelog.git
cd apitriagelog
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m uvicorn app.main:app --reload
```

It's running when you see `Uvicorn running on http://127.0.0.1:8000`. Leave that window open, it's the live server log. Ctrl+C stops it.

Next time, just `cd` into the folder and run the last line.

> Note: `.venv` is a "virtual environment", a private folder of Python packages just for this project so it doesn't mess with anything else on your PC. Running `.venv\Scripts\python.exe` directly means you don't have to activate it first, and you skip the PowerShell "running scripts is disabled" error.

> Note: if you paste a few commands into PowerShell and they end up on one line, it breaks. One command per line, or put `;` between them.

## Using it

**Browser (easiest)**

1. Go to http://localhost:8000/docs
2. Click **Authorize**, type `test_secret_123`, click Authorize, then Close
3. Open `POST /api/v1/webhook`, click **Try it out**, then **Execute**. You should get a 200
4. Mess up the body (delete a field, take the @ out of the email) and Execute again. Now you get a 400 telling you what's wrong
5. Open `GET /api/v1/logs` and Execute to see both requests

**Postman**

Import `postman/mock-api-triage.postman_collection.json`, click the collection and hit **Run**. It sends 9 requests (good ones, broken ones, no key, wrong key, rate limited, logs) and checks every response. Everything should come back green.

**Terminal**

On Windows type `curl.exe`, not `curl`. In PowerShell plain `curl` is actually a different command and it wrecks the JSON.

```powershell
# good request, expect 200
curl.exe -X POST http://localhost:8000/api/v1/webhook -H "Content-Type: application/json" -H "X-API-Key: test_secret_123" --data "@examples/200_success.json"

# bad email + missing field, expect 400
curl.exe -X POST http://localhost:8000/api/v1/webhook -H "Content-Type: application/json" -H "X-API-Key: test_secret_123" --data "@examples/400_invalid_payload.json"

# no API key, expect 401
curl.exe -X POST http://localhost:8000/api/v1/webhook -H "Content-Type: application/json" --data "@examples/200_success.json"

# pretend they hit the rate limit, expect 429
curl.exe -X POST http://localhost:8000/api/v1/webhook -H "Content-Type: application/json" -H "X-API-Key: test_secret_123" -H "X-Simulate-Rate-Limit: true" --data "@examples/200_success.json"
```

Mac/Linux is the same, just `curl` instead of `curl.exe`.

## Errors and what they actually mean

Every error comes back in the same shape, so there's only one format to learn:

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

It lists every problem at once, not just the first one. Nobody wants to fix one thing, resend, and then get told about the next thing.

| Code | error_code | What happened | What you'd tell the customer |
|---|---|---|---|
| 400 | `VALIDATION_FAILED` | Broken JSON, missing or wrong field, bad email, or Content-Type isn't `application/json` | Fix what's listed in `details`, then resend |
| 401 | `AUTH_MISSING_API_KEY` | No `X-API-Key` header | Add the header. If they swear they did, something in between (like a proxy) might be stripping it |
| 401 | `AUTH_INVALID_API_KEY` | Key is wrong | Re-copy the key, and check it's not the key from a different environment (test vs live) |
| 404 | `NOT_FOUND` | Wrong URL, like `/webhooks` instead of `/webhook` | Use `/api/v1/webhook` |
| 405 | `METHOD_NOT_ALLOWED` | Sent GET to the webhook instead of POST | Use POST |
| 429 | `RATE_LIMITED` | Too many requests (faked here with a header) | Wait the number of seconds in the `Retry-After` header, then retry |
| 500 | `INTERNAL_ERROR` | Our bug | Escalate to engineering with the `request_id` |
| 503 | `DATABASE_UNAVAILABLE` | Database is down (health check only) | Infra problem, not theirs |

> Note: rough rule of thumb. **4xx = the caller did something wrong. 5xx = we did.** That one rule answers "whose fault is it" most of the time.

## Finding a request in the logs (the actual support part)

Every response has an `X-Request-ID` header, a unique ID for that one request. The customer can also send their own (letters, numbers, `-`, `_`, `.`) and it gets used instead. That's how you find "that one request from 3pm" without guessing.

Example: a customer says "your webhook keeps rejecting us, our request ID was case-4711."

Send it as the customer:

```powershell
curl.exe -X POST http://localhost:8000/api/v1/webhook -H "Content-Type: application/json" -H "X-API-Key: test_secret_123" -H "X-Request-ID: case-4711" --data "@examples/400_invalid_payload.json"
```

Now be support. See what's been going on:

```powershell
$h = @{ "X-API-Key" = "test_secret_123" }
(Invoke-RestMethod "http://localhost:8000/api/v1/logs?limit=10" -Headers $h).logs | Format-Table request_id, status_code, error_code, ticket_id -AutoSize
```

Then pull up their request and see why it failed:

```powershell
$entry = (Invoke-RestMethod "http://localhost:8000/api/v1/logs?status_code=400" -Headers $h).logs | Where-Object request_id -eq "case-4711"
$entry.error_detail | Format-List field, error_type, issue
```

Their email is missing the @ and `payload_type` isn't there at all. So it's on their side, and you can tell them exactly which two things to fix.

`/logs` also takes `?status_code=401` (or any code) to only show that kind of error, and `?limit=` up to 500. On Mac/Linux just run `curl "http://localhost:8000/api/v1/logs?status_code=400" -H "X-API-Key: test_secret_123"` and read the JSON.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

17 tests, should all pass. The server doesn't need to be running, and they use their own throwaway database so your real data doesn't get touched.

They cover every status code, that the key gets checked before anything else, that request IDs end up in the logs, and that customer emails don't leak into the logs.

## Notes

Stuff that isn't obvious just from using it:

1. **Why 400 and not 422.** FastAPI (the framework) normally answers bad data with a 422. I switched it to 400 since that's the more common answer for a bad request and what I wanted this API to return. The /docs page says 400 too, so the docs match what actually happens.

2. **The key gets checked first.** No valid key means a 401 before the API even looks at your JSON, so a stranger can't poke at it to learn what fields it wants. One exception: if the body isn't valid JSON at all (like a missing comma), FastAPI catches that first and you get a 400. That's just how FastAPI works. At a real company the key usually gets checked even earlier, by a gateway sitting in front of the app.

3. **Customer emails stay out of the logs.** `/logs` never shows `customer_email`. When a request gets rejected, the log saves which field failed and why, but not the bad value itself. The customer still sees their own value in the error they get back. Lots of people read logs, so personal info shouldn't be sitting in them.

4. **Broken JSON tells you where.** The error says something like `Expecting ',' delimiter (line 3, column 3)` so you can jump straight to it in Postman instead of staring at the whole body.

5. **The rate limit is fake.** Nothing is actually counted. Send `X-Simulate-Rate-Limit: true` and you get the 429. It's there so you can see what a 429 looks like and how `Retry-After` works.

6. **Where the data lives.** `tickets.db` in the project folder, a SQLite file. Two tables: `tickets` (accepted webhooks) and `request_logs` (every request, good or bad). DB Browser for SQLite can open it if you want to look inside.

7. **Starting fresh.** Stop the server, delete `tickets.db`, start it again. It makes a new one.

8. **Changing settings.** Set `TRIAGE_API_KEY` to use a different key, or `TRIAGE_DB_PATH` to put the database somewhere else.

## How the code is split up

| File | What's in it |
|---|---|
| `app/main.py` | Starts the app, gives every request its ID, writes the logs |
| `app/routes.py` | The three endpoints |
| `app/schemas.py` | What a valid webhook looks like |
| `app/security.py` | The API key check and the fake rate limit |
| `app/errors.py` | Turns every error into the same JSON shape |
| `app/database.py` | Everything that touches SQLite |
| `app/config.py` | Settings |
| `examples/` | JSON files for the curl commands |
| `postman/` | The Postman collection |
| `tests/` | The tests |

> Note: **Pydantic** is what checks the JSON. You describe what valid data looks like once (that's `schemas.py`), and it rejects anything that doesn't match, with a reason. FastAPI runs it automatically on every request.
