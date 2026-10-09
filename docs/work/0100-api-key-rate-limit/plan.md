# Plan: Rate-limit API endpoints per API key

- Spec: [spec.md](spec.md)
- Spec approved: 2026-10-08
- Status: approved

## Files that change
| Path | Change | Why |
|---|---|---|
| `app/auth/rate_limit.py` | add | AC-1, AC-2, AC-3, AC-5, AC-9 |
| `app/auth/api_key.py` | edit | AC-1, AC-3, AC-5, AC-6, AC-7 |
| `app/main.py` | edit | AC-4 |
| `tests/auth/test_rate_limit.py` | add | AC-1, AC-2, AC-3, AC-5 |
| `tests/auth/test_api_key_rate_limit.py` | add | AC-1, AC-3, AC-5, AC-6, AC-7 |
| `tests/routes/test_rate_limit_headers.py` | add | AC-2, AC-4, AC-6, AC-7 |

## Tasks
- T1: Add `app/auth/rate_limit.py` with `RateLimitDecision`, the `RateLimiter` protocol, `InMemoryRateLimiter` (fixed window aligned to the UTC minute, one entry per key id, a `Lock`, an injectable `datetime` clock, `ValueError` for a limit of 0 or less, refused hits do not count past the limit), `effective_limit` (`None` to 100, capped at 100, 0 or less to 0, checked with `is not None`), and `get_rate_limiter()` returning a module-level default; unit tests in `tests/auth/test_rate_limit.py` for 100 allowed then refused, `remaining` and `reset` values, reset at the window end, `:59` and `:00` in different windows, two keys counted apart, and `effective_limit` for `None`, `1`, `99`, `100`, `250`, `0`, `-5` | ACs: AC-1, AC-2, AC-3, AC-5, AC-9 | Verify: `make app-test-fast && ~/.venvs/cadence/bin/python -m mypy --strict app/auth/rate_limit.py` | Size: M | Depends on: none
- T2: Wire the limiter into `api_key_auth` in `app/auth/api_key.py`: add `request: Request` and `limiter: RateLimiter = Depends(get_rate_limiter)`; after the 401 checks, compute `effective_limit(key.requests_per_minute)`; for `0`, store the blocked headers (`0`, `0`, `null`) on `request.state` and raise `429` `api key blocked` with those headers without calling the limiter; otherwise call `limiter.hit(key.id, limit)`, store the three headers on `request.state`, and raise `429` `rate limit exceeded` with them when refused. Tests in `tests/auth/test_api_key_rate_limit.py` on a throwaway app with the real `api_key_auth`, `get_db` on in-memory SQLite, and `get_rate_limiter` overridden with a fixed-clock limiter: 100 `200`s then the `429` body, a second key unaffected, `requests_per_minute = 3` refused on the 4th, `0` and `-5` blocked on every request (also after the clock passes the window end) with a spy showing the limiter is never called, and missing or invalid keys returning `401` without changing the count | ACs: AC-1, AC-3, AC-5, AC-6, AC-7 | Verify: `make app-test-fast` | Size: M | Depends on: T1
- T3: Register an `@app.middleware("http")` in `app/main.py` that copies the rate-limit headers stored on `request.state` onto the response, and does nothing when none are stored (as for `401`s, unkeyed routes, and the existing tests that stub `api_key_auth`). Tests in `tests/routes/test_rate_limit_headers.py` on the full `app.main.app` with `get_db`, `get_rate_limiter`, and `get_workflow_store` overridden: the three headers with exact values on a `200` from `POST /workflows/{id}/run`, a `404` for an unknown workflow, a `422` for a schema-invalid JSON body, and the `429`; `X-RateLimit-Reset` advances to the next window end after the clock passes it; a malformed-JSON `422` and a `401` carry no `X-RateLimit-*` headers; a blocked key's `429` carries `0`, `0`, `null` | ACs: AC-2, AC-4, AC-6, AC-7 | Verify: `make app-test-fast` | Size: M | Depends on: T2
- T4: Run the strict type checks and the full test suite on a clean `test.db`. no testable behavior: this task runs checks only and changes no code | ACs: AC-9 | Verify: `make typecheck && ~/.venvs/cadence/bin/python -m mypy --strict app/auth/rate_limit.py && make clean test` | Size: S | Depends on: T3
- T5: Run the app in the dev container and exercise the limit live with `curl -i`: `npx -y @devcontainers/cli up --workspace-folder .`; in the container, import every `app.models` module and run `Base.metadata.create_all(engine)`; seed three `ApiKey` rows with random keys held in shell variables (`$KEY` and `$KEY_B` with `requests_per_minute` null, `$KEY_BLOCKED` with `0`); start `make run`; create a workflow with `POST /workflows`; run a scripted loop that waits for the next minute boundary, then for each request prints `date -u +%s` and runs `curl -i -H "X-API-Key: $KEY"`, recording request 1, request 101 (`429` in the same minute), one `$KEY_B` request (`200`, `X-RateLimit-Remaining: 99`), one `$KEY_BLOCKED` request (`429`, `X-RateLimit-Reset: null`), and the first `$KEY` request after the next minute boundary (`200`, `X-RateLimit-Remaining: 99`); append the setup commands and output to `evidence.md`; stop the stack with `docker compose -p <name> down`. no testable behavior: a manual end-to-end run against Postgres in the dev container | ACs: AC-8 | Verify: manual: lstod reads the recorded `curl -i` output in `evidence.md` (keys only as variables) and signs off `manual ok` | Size: M | Depends on: T3

## Parallel groups
- T1, T2, T3 run in order: each builds on the one before.
- T4 and T5 both depend only on T3 and can run in either order. Only one cadence Docker stack runs at a time.

## Checkpoints
- Gate 3: lstod approves this plan before any file outside `docs/work/0100-api-key-rate-limit/` changes.
- After each of T1 to T3: the task's Verify passes, `scripts/implement-check.sh first` and `mutate` proofs are quoted, the Verify output is appended to `evidence.md` under the ACs it proves, and the task is committed.
- After T4: report the `make clean test` pass count.
- After T5: stop and show lstod the `curl -i` output; continue only after `manual ok`.
- Before the push: `make verify` with `verify: 4/4 passed`, then the draft PR into `main`.
- Gate 4: review by `code-reviewer`, `security-auditor`, and `verifier`, then the code owner approves and merges.

## Risks and mitigations
- **The module-level default limiter leaks state between tests.** Every new test overrides `get_rate_limiter` with a fresh `InMemoryRateLimiter` and clears `app.dependency_overrides` in teardown.
- **Existing tests break when `api_key_auth` gains parameters.** `tests/routes/test_workflows.py:42` replaces `api_key_auth` entirely, and the middleware does nothing without stored headers; `tests/auth/test_api_key.py` makes a few requests against the default limiter, far below 100. T4 runs the full suite to confirm.
- **`BaseHTTPMiddleware` and `request.state`.** Starlette shares `request.state` between the middleware and the route through the ASGI scope; T3's tests on the full app prove the headers arrive on `200`, `404`, `422`, and `429`.
- **The dev-container run misses the window.** The loop waits for the next minute boundary before request 1, and 101 local requests take well under a minute.
- **Keys leak into `evidence.md`.** Keys are generated in the container and passed only through shell variables; `make verify` runs gitleaks before the push.
- **`app/auth/api_key.py` is outside `make typecheck`.** It imports untyped SQLAlchemy columns, so strict checking stays on the new module only (spec Constraints).

## Proof
AC to proof:

| AC | Task | Proved by |
|---|---|---|
| AC-1 | T1, T2 | `tests/auth/test_rate_limit.py` (100 allowed, 101st refused); `tests/auth/test_api_key_rate_limit.py` (100 `200`s, then `429` `{"detail": "rate limit exceeded"}`) |
| AC-2 | T1, T3 | `tests/auth/test_rate_limit.py` (reset at the window end with `remaining == limit - 1`; `:59` and `:00` in different windows); `tests/routes/test_rate_limit_headers.py` (`X-RateLimit-Reset` advances to the next window end) |
| AC-3 | T1, T2 | `tests/auth/test_rate_limit.py` (two key ids counted apart); `tests/auth/test_api_key_rate_limit.py` (key B `200` with `X-RateLimit-Remaining: 99` while key A gets `429`) |
| AC-4 | T3 | `tests/routes/test_rate_limit_headers.py` (exact headers on `200`, `404`, schema-invalid `422`, and `429`; none on malformed-JSON `422`) |
| AC-5 | T1, T2 | `tests/auth/test_rate_limit.py` (`effective_limit` cases); `tests/auth/test_api_key_rate_limit.py` (`requests_per_minute = 3` refused on the 4th) |
| AC-6 | T2, T3 | `tests/auth/test_api_key_rate_limit.py` (`0` and `-5` always `429` `api key blocked`, also after the window end, limiter never called); `tests/routes/test_rate_limit_headers.py` (`0`, `0`, `null` headers) |
| AC-7 | T2, T3 | `tests/auth/test_api_key_rate_limit.py` (missing and invalid keys `401`, count unchanged); `tests/routes/test_rate_limit_headers.py` (no `X-RateLimit-*` headers on `401`) |
| AC-8 | T5 | manual: `curl -i` loop in the dev container recorded in `evidence.md`, signed off `manual ok` by lstod |
| AC-9 | T1, T4 | `~/.venvs/cadence/bin/python -m mypy --strict app/auth/rate_limit.py` and `make typecheck` exit 0 |

Overall: `make app-test-fast` passes after each code task, `make clean test` passes with the new tests counted, and `make verify` prints `verify: 4/4 passed` before the push.

## Rollback
Revert the PR's merge commit. The change adds no tables, columns, migrations, or stored data, and the limiter's state is in memory only, so a revert and a restart return the API to no rate limiting. To disable the limit without a revert, override `get_rate_limiter` with a limiter that always allows; this needs a code change, so the revert is the supported path.

## Revision log
- 2026-10-08: Gate 3 approved by lstod (lstod)
- 2026-10-08: T1 done by /implement (claude-code); Verify: pass; attempts: 0; guards: 4 proved
- 2026-10-08: T2 done by /implement (claude-code); Verify: pass; attempts: 0; guards: 0
- 2026-10-08: T3 done by /implement (claude-code); Verify: pass; attempts: 0; guards: 3 proved
- 2026-10-08: T4 done by /implement (claude-code); Verify: pass; attempts: 0; guards: skipped
