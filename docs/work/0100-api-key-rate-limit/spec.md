# Spec: Rate-limit API endpoints per API key

- Intent: [intent.md](intent.md)
- Status: draft

## 1. Task
Add a per-API-key, fixed-window rate limit to the shared API-key auth dependency, so every endpoint that authenticates with `X-API-Key` allows at most 100 requests per key per clock minute, or fewer when the key's `requests_per_minute` is set below 100. A request over the limit gets `429 Too Many Requests`. Every non-`5xx` response to a request whose key passed authentication carries `X-RateLimit-Limit`, `X-RateLimit-Remaining`, and `X-RateLimit-Reset`. A key whose `requests_per_minute` is 0 or negative is always blocked. The counter lives in one app process.

## 2. Context
- `app/auth/api_key.py:29-46`: `api_key_auth` reads `X-API-Key`, returns `401` with `missing api key` or `invalid api key`, and otherwise returns the `ApiKey` row. Line 45 is `# TODO: rate limiting`. The module docstring (lines 3-6) names this dependency as the single place to add rate limiting.
- `app/models/api_key.py:14`: `ApiKey.requests_per_minute` is a nullable `Integer` with default `None`. Nothing reads it today.
- `app/routes/workflows.py:153-161`: `POST /workflows/{workflow_id}/run` is the only mounted route that depends on `api_key_auth`. It takes a JSON body (`payload: WorkflowRunIn`, line 157) and raises `404` with `workflow not found` after authentication when the workflow is unknown (lines 163-168).
- `app/routes/workflows.py:86-90`: `POST /workflows` (`create_workflow`) has no API-key auth.
- `app/main.py:1-18`: the app mounts the auth, hook, run, and workflow routers and `/health`; it has no middleware.
- `app/services/workflows/idempotency.py:29-72`: the repo's pattern for stateful backends: a `Protocol`, an in-memory implementation with an injectable clock and a `Lock`. Its provider `get_idempotency_store` and default instance are at `app/routes/workflows.py:51-57`.
- `app/repositories/api_keys.py:24-28`: `APIKeyRepository.create` exists, but no route calls it, so nothing in the running app creates an `ApiKey` row.
- `app/middleware/throttle.py:1-23`: an unwired placeholder: a module-global sliding-window list per key string that returns `None` instead of a `429`. Only `tests/middleware/test_throttle.py:1-5` imports it, to assert the function exists.
- `tests/routes/test_workflows.py:42`: the workflow route tests replace `api_key_auth` with a stub through `app.dependency_overrides`, so they never reach the real dependency.
- `tests/auth/test_api_key.py:22-58`: tests the real `api_key_auth` on a throwaway `FastAPI` app with an in-memory SQLite database seeded with one key.
- `Makefile:47-52`: `make typecheck` runs `mypy --strict` only on `app/services/workflows`, `app/services/billing`, `app/auth/passwords.py`, and `app/auth/migrations.py`. The `Makefile` cannot change in this work item.
- The repo has no migrations: a fresh database needs `Base.metadata.create_all(engine)` after importing every `app.models` module (root `AGENTS.md`, Running and testing).

## 3. Constraints
- Follow the stateful-backend pattern from `app/services/workflows/idempotency.py`: a `Protocol`, an in-memory implementation with an injectable clock, thread-safe under a `Lock`, and a `get_rate_limiter()` provider that tests replace through `app.dependency_overrides`.
- Single process only. The in-memory counter is not shared between processes or app instances; the product owner accepts this as not production ready.
- Check `requests_per_minute` with `is not None`, never truthiness, so `0` is treated as a set value.
- Memory is bounded by the number of distinct keys: the limiter keeps one entry per key for the current window only.
- New modules pass `python -m mypy --strict <file>`, because `make typecheck` does not cover them and the `Makefile` cannot change. `make typecheck` stays clean.
- No new dependencies. No change to `Makefile`, `.gitignore`, or `docker-compose.yml`. No change to the `ApiKey` model or any table.
- Do not add to `app/utils/`. Do not edit `app/middleware/throttle.py`.

## 4. Format
**Effective limit** for a key, from `requests_per_minute` (`rpm`), returned by `effective_limit`:

| `rpm` | `effective_limit(rpm)` | Meaning |
|---|---|---|
| `None` | 100 | default |
| 100 or more | 100 | capped |
| 1 to 99 | `rpm` | lowered |
| 0 or negative | 0 | blocked |

**Window:** fixed, aligned to the clock minute in UTC. For a request at Unix time `t` (seconds), the window starts at `floor(t / 60) * 60` and ends at that plus 60.

**Counting:** each request with a valid key and a non-zero effective limit increments that key's count for the current window. Request number `n` in a window (1-based) is allowed when `n <= limit`; otherwise it is refused and the count stays at `limit`. Counts are kept per `ApiKey.id`. A blocked key (effective limit 0) never reaches the limiter.

**Headers**, on every response with a status below 500 to a request whose key passed authentication: `2xx`, the `429`, and errors the route or request validation raise afterwards, such as the `404` for an unknown workflow or a `422` for a well-formed JSON body that fails the schema. A `422` for a body that is not valid JSON is raised by FastAPI before dependencies run: that request is not authenticated, not counted, and carries no `X-RateLimit-*` headers. `5xx` responses are not covered.

| Header | Allowed request | Refused request | Blocked key |
|---|---|---|---|
| `X-RateLimit-Limit` | limit | limit | `0` |
| `X-RateLimit-Remaining` | `limit - n` | `0` | `0` |
| `X-RateLimit-Reset` | window end, integer Unix seconds | window end, integer Unix seconds | `null` (see Open questions) |

`X-RateLimit-Reset` is an integer for every key that is not blocked; only a blocked key gets the non-integer `null`.

**Refusal:** status `429`, body `{"detail": "rate limit exceeded"}` for a key over its limit, and `{"detail": "api key blocked"}` for a blocked key.

**Requests with a missing or invalid key:** unchanged: `401` with the existing body, not counted, and no rate-limit headers.

**Interfaces** (new module `app/auth/rate_limit.py`):

```python
@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    reset: int  # window end, Unix seconds

class RateLimiter(Protocol):
    def hit(self, key_id: int, limit: int) -> RateLimitDecision: ...
    # limit must be positive

class InMemoryRateLimiter:  # fixed window, Lock, injectable clock
    def __init__(self, clock: Callable[[], datetime] = _default_clock) -> None: ...
    # hit() raises ValueError when limit <= 0

def effective_limit(requests_per_minute: int | None) -> int: ...  # 0 = blocked
def get_rate_limiter() -> RateLimiter: ...  # module-level default instance
```

`api_key_auth` gains `request: Request` and `limiter: RateLimiter = Depends(get_rate_limiter)` parameters. After a key passes authentication it computes `effective_limit(key.requests_per_minute)`. When that is `0`, it stores the blocked-key header values on `request.state` and raises `HTTPException(429, "api key blocked", headers=...)` without calling the limiter. Otherwise it calls `limiter.hit(key.id, limit)`, stores the three header values on `request.state`, and raises `HTTPException(429, "rate limit exceeded", headers=...)` when the decision is not allowed. An HTTP middleware registered in `app/main.py` copies the stored headers onto every response that passes through it.

## 5. Example
A key with `requests_per_minute = None`, first request at `2026-10-08T18:00:05Z` (Unix `1791482405`; the window ends at `1791482460`):

```
POST /workflows/<id>/run   X-API-Key: $KEY   (request 1)
-> 200
   X-RateLimit-Limit: 100
   X-RateLimit-Remaining: 99
   X-RateLimit-Reset: 1791482460

POST /workflows/<id>/run   X-API-Key: $KEY   (request 101, at 18:00:40Z)
-> 429 {"detail": "rate limit exceeded"}
   X-RateLimit-Limit: 100
   X-RateLimit-Remaining: 0
   X-RateLimit-Reset: 1791482460

POST /workflows/<id>/run   X-API-Key: $KEY   (at 18:01:00Z, the next window)
-> 200
   X-RateLimit-Remaining: 99
   X-RateLimit-Reset: 1791482520
```

Same window, a second key `$KEY_B` sends its first request after `$KEY` was refused: `200`, `X-RateLimit-Remaining: 99`.

Failure cases:
- A key with `requests_per_minute = 0`: every request returns `429 {"detail": "api key blocked"}` with `X-RateLimit-Limit: 0`, `X-RateLimit-Remaining: 0`, `X-RateLimit-Reset: null`.
- A missing `X-API-Key`: `401 {"detail": "missing api key"}`, no `X-RateLimit-*` headers.
- A valid key with the body `{"triggered_by":` (not valid JSON): `422`, no `X-RateLimit-*` headers, not counted.

## Acceptance criteria
- AC-1: For a key with `requests_per_minute` of `None`, the first 100 requests within one window are handled normally and the 101st returns `429` with body `{"detail": "rate limit exceeded"}`.
- AC-2: The window is fixed and aligned to the clock minute: once the clock reaches the window's end, the key's next request is handled normally with `X-RateLimit-Remaining` equal to its limit minus 1, and requests at second 59 of one minute and second 0 of the next fall in different windows.
- AC-3: The limit is per key: while one key is being refused with `429`, a second key's first request in the same window is handled normally with `X-RateLimit-Remaining: 99`.
- AC-4: Every response with a status below 500 to a request whose key passed authentication, including `2xx`, `429`, a `404` raised by the route, and a `422` for a well-formed JSON body that fails the schema, carries `X-RateLimit-Limit`, `X-RateLimit-Remaining`, and `X-RateLimit-Reset` with the values in the Format table; for a key that is not blocked, `X-RateLimit-Reset` is the window's end as integer Unix seconds.
- AC-5: A key with `requests_per_minute` from 1 to 99 is limited to that value, and a key with 100 or more is limited to 100.
- AC-6: A key with `requests_per_minute` of 0 or negative gets `429` with body `{"detail": "api key blocked"}` on every request, with `X-RateLimit-Limit: 0`, `X-RateLimit-Remaining: 0`, and `X-RateLimit-Reset: null`, including after the window ends.
- AC-7: A request with a missing or invalid key still returns `401` with the existing body, carries no `X-RateLimit-*` headers, and does not change any key's count.
- AC-8: In the app running in the dev container against Postgres, with tables created and one `ApiKey` row (`requests_per_minute` null) and one workflow seeded, a scripted `curl -i` loop that starts within the first 5 seconds of a minute and prints `date -u +%s` before each request shows: request 1 `200` with the three headers, request 101 `429` within the same minute, and the first request after the next minute boundary `200` with `X-RateLimit-Remaining: 99`.
- AC-9: `python -m mypy --strict app/auth/rate_limit.py` and `make typecheck` both pass.

## Non-goals
- Rate limiting endpoints that do not authenticate with an API key today (`/login`, `/health`, `/hooks/*`, `/runs`, `POST /workflows`).
- Adding API-key auth to any route that lacks it today, mounted or not (for example `POST /workflows`, `/runs`, `app/routes/admin/`, `app/routes/integrations/`).
- A limit shared across processes or instances, including a Redis-backed limiter.
- Raising a key's limit above 100 per minute.
- A `Retry-After` header, sliding windows, token buckets, or burst allowances.
- Limiting by IP address or for unauthenticated requests.
- Rate-limit headers on `5xx` responses, or on `422`s for bodies that are not valid JSON.
- Removing or changing `app/middleware/throttle.py` and its test.
- Any API or admin route to set `requests_per_minute`, and any validation, constraint, or column change for it in `app/models/api_key.py`.
- Committed seed scripts or migrations: the AC-8 setup runs as one-off commands in the container and is recorded in `evidence.md`.

## Risks
- **The limit resets on every restart and is per process.** Running several workers multiplies the effective limit. Accepted by the product owner; a shared backend is a follow-up.
- **Fixed windows allow a burst at the boundary:** up to twice the limit in two seconds across a minute boundary. Inherent to the fixed window the intent asks for.
- **The module-global default limiter carries state between tests** that use the real `api_key_auth`. Tests replace `get_rate_limiter` through `app.dependency_overrides` and clear it in teardown.
- **`X-RateLimit-Reset: null` is not an integer.** A client that parses the header as an integer fails on a blocked key. Only blocked keys see it; see Open questions.
- **AC-8 timing:** if the loop starts late in a minute, the window resets before request 101 and the run proves nothing. The loop waits for the next minute boundary before it starts.

## Design decisions
- **Enforce in `api_key_auth`, not in a path-matching middleware.** Alternatives: a middleware that reads `X-API-Key` itself; a separate dependency added to each route. The dependency already knows the key is valid and which `ApiKey` it is, and its docstring names it as the place for rate limiting, so every API-key route is covered with no per-route change.
- **Headers set by `@app.middleware("http")` from `request.state`.** Alternatives: set them on an injected `Response` in the dependency; a pure ASGI middleware that wraps `send`. An injected `Response` loses its headers when the route raises (the `404`), which would break AC-4. A pure ASGI middleware avoids `BaseHTTPMiddleware` overhead but adds about 20 lines of lower-level code; every API-key route is a plain JSON route with no streaming, so the simpler middleware is enough.
- **Blocked keys are handled in `api_key_auth`, not by the limiter.** Alternative: pass `None` to `hit()` to mean blocked. That gives `None` two meanings (`requests_per_minute = None` is the default of 100), and every future `RateLimiter` would have to reimplement blocking. `hit()` takes only a positive limit, and `effective_limit` returns `0` for blocked.
- **New module `app/auth/rate_limit.py`, not `app/middleware/throttle.py`.** Alternative: finish the placeholder. The placeholder is a sliding window keyed by the raw key string with module-global state and no clock injection; the intent asks for a fixed window, and the repo pattern needs a `Protocol` and an injectable clock. Leaving it alone keeps this change small; removing it is a follow-up that needs approval to delete files.
- **Count per `ApiKey.id`, not per key string.** Keeps raw secrets out of the limiter's memory.
- **Refused requests do not increment past the limit.** Keeps `X-RateLimit-Remaining` at `0` and the stored count bounded; the observable behaviour is the same either way.
- **The clock returns a timezone-aware `datetime`,** matching `idempotency.py`, and the limiter converts to integer Unix seconds.

## Verification
- AC-1: unit test on `InMemoryRateLimiter` with a fixed clock (100 allowed, 101st refused), plus a test on a FastAPI app using the real `api_key_auth` with the limiter override: 100 responses `200`, the 101st `429` with the body.
- AC-2: unit tests with an injectable clock: exhaust the limit, advance the clock to the window's end, the next hit is allowed with `remaining == limit - 1`; hits at `:59` and `:00` of the next minute land in different windows.
- AC-3: unit test and route test with two keys: exhaust key A, key B's first request is `200` with `X-RateLimit-Remaining: 99`.
- AC-4: route tests on the full `app.main.app` with `get_db` and `get_rate_limiter` overridden: a `200` from `POST /workflows/{id}/run`, a `404` for an unknown workflow, a `422` for a schema-invalid JSON body, and a `429`, each asserting all three headers and their values; plus a malformed-JSON `422` asserting no headers.
- AC-5: parametrised tests of `effective_limit` (`None`, `1`, `99`, `100`, `250`, `0`, `-5`) and a route test with `requests_per_minute = 3` refused on the 4th request.
- AC-6: parametrised route tests for `0` and `-5`: every request `429` with the blocked body and headers, also after advancing the clock past the window's end, and the limiter is never called.
- AC-7: route tests with a missing and an invalid key: `401`, no `X-RateLimit-*` headers, and a following valid request still sees `X-RateLimit-Remaining: 99`.
- AC-8: manual: in the dev container, create the tables, seed one `ApiKey` and one workflow with one-off commands, then run a scripted loop that waits for the next minute boundary and, for each request, prints `date -u +%s`, then `curl -i` with the key passed through a variable (`-H "X-API-Key: $KEY"`). `evidence.md` records the setup commands and the output for request 1, request 101, and the first request after the boundary. Signed off by lstod as `manual ok`.
- AC-9: `python -m mypy --strict app/auth/rate_limit.py` and `make typecheck` exit 0.
- All: `make app-test-fast`, then `make clean test` and `make verify`.

## Open questions
- **How is the null reset time sent for a blocked key (AC-6)?** Each option has a cost:
  - (a) `X-RateLimit-Reset: null`, the literal word. Keeps AC-4's "all three headers" true for every keyed response; a client that parses the header as an integer fails on a blocked key.
  - (b) Leave `X-RateLimit-Reset` out for a blocked key. Every value sent stays an integer; AC-4 gains an exception ("except `X-RateLimit-Reset` for a blocked key") and AC-6 changes to "no `X-RateLimit-Reset` header".
  - (c) Send it with an empty value. Same parse problem as (a), and harder to read.

  Suggested default: (a), which matches the intent's "null reset time". The ACs above are written for (a); choosing (b) changes AC-4 and AC-6 as described.

## Revision log
