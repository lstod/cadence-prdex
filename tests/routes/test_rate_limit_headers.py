"""Rate-limit headers on the full app (work item 0100, T3).

These tests run against `app.main.app` with the real `api_key_auth`, `get_db`
on an in-memory SQLite database seeded with API keys, `get_rate_limiter`
replaced by a fresh `InMemoryRateLimiter` on a manual clock, and
`get_workflow_store` replaced by a fresh dict. They observe only HTTP
responses from `POST /workflows/{id}/run`.

Each test names the acceptance criterion of
docs/work/0100-api-key-rate-limit/spec.md that it asserts.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth.api_key import get_db
from app.auth.rate_limit import InMemoryRateLimiter, get_rate_limiter
from app.db import Base
from app.main import app
from app.models.api_key import ApiKey
from app.models.run import Run as _Run
from app.models.workflow import Workflow as _WorkflowRow
from app.routes.workflows import get_workflow_store
from app.services.workflows.models import Workflow

_REGISTERED = (_Run, _WorkflowRow)

# Spec section 5: 2026-10-08T18:00:05Z is Unix 1791482405; its window ends at
# 1791482460, and the next window ends at 1791482520.
_START = datetime(2026, 10, 8, 18, 0, 5, tzinfo=timezone.utc)
_WINDOW_END = 1791482460
_NEXT_WINDOW_END = 1791482520

_RATE_HEADERS = ("x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset")

# Seeded keys: raw key string -> requests_per_minute.
_KEYS: dict[str, int | None] = {
    "ak_hdr_default": None,
    "ak_hdr_rpm_3": 3,
    "ak_hdr_rpm_0": 0,
    "ak_hdr_rpm_neg5": -5,
}

_RUN_BODY = {"triggered_by": "tester", "input": {"x": 1}}


class _ManualClock:
    def __init__(self, start: datetime) -> None:
        self._now = start

    def __call__(self) -> datetime:
        return self._now

    def set(self, now: datetime) -> None:
        self._now = now


@pytest.fixture
def clock() -> _ManualClock:
    return _ManualClock(_START)


@pytest.fixture
def client(clock: _ManualClock) -> Iterator[TestClient]:
    assert _REGISTERED  # keep model imports live for table registration
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    seed_db = factory()
    for raw, rpm in _KEYS.items():
        seed_db.add(ApiKey(key=raw, user_id=1, name=raw, requests_per_minute=rpm))
    seed_db.commit()
    seed_db.close()

    def _override_db() -> Iterator[Session]:
        db = factory()
        try:
            yield db
        finally:
            db.close()

    limiter = InMemoryRateLimiter(clock=clock)
    store: dict[UUID, Workflow] = {}

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_rate_limiter] = lambda: limiter
    app.dependency_overrides[get_workflow_store] = lambda: store
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture
def workflow_id(client: TestClient) -> str:
    """Create a workflow through the unkeyed POST /workflows route."""
    resp = client.post(
        "/workflows",
        json={
            "account_id": str(uuid4()),
            "name": "rate-limit-headers",
            "description": "T3 fixture",
            "steps": [{"ordinal": 0, "name": "alpha", "action": "noop", "config": {}}],
        },
    )
    assert resp.status_code == 201
    return str(resp.json()["id"])


def _run(
    client: TestClient,
    workflow_id: str,
    key: str | None,
    body: Any = _RUN_BODY,
) -> httpx.Response:
    headers = {} if key is None else {"X-API-Key": key}
    return client.post(f"/workflows/{workflow_id}/run", json=body, headers=headers)


def _rate_headers(resp: httpx.Response) -> dict[str, str]:
    return {
        name.lower(): value
        for name, value in resp.headers.items()
        if name.lower() in _RATE_HEADERS
    }


def _expected(limit: int, remaining: int, reset: int | str) -> dict[str, str]:
    return {
        "x-ratelimit-limit": str(limit),
        "x-ratelimit-remaining": str(remaining),
        "x-ratelimit-reset": str(reset),
    }


# AC-4: the three headers, with Format-table values, on every keyed non-5xx.


def test_ac4_200_from_run_carries_three_headers_with_exact_values(
    client: TestClient, workflow_id: str
) -> None:
    resp = _run(client, workflow_id, "ak_hdr_default")
    assert resp.status_code == 200
    assert resp.json()["status"] == "succeeded"
    assert _rate_headers(resp) == _expected(100, 99, _WINDOW_END)


def test_ac4_remaining_decreases_on_each_200_in_the_window(
    client: TestClient, workflow_id: str
) -> None:
    seen = []
    for _ in range(3):
        resp = _run(client, workflow_id, "ak_hdr_default")
        assert resp.status_code == 200
        seen.append(_rate_headers(resp))
    assert seen == [
        _expected(100, 99, _WINDOW_END),
        _expected(100, 98, _WINDOW_END),
        _expected(100, 97, _WINDOW_END),
    ]


def test_ac4_lowered_limit_200_carries_its_own_limit(
    client: TestClient, workflow_id: str
) -> None:
    resp = _run(client, workflow_id, "ak_hdr_rpm_3")
    assert resp.status_code == 200
    assert _rate_headers(resp) == _expected(3, 2, _WINDOW_END)


def test_ac4_404_for_unknown_workflow_carries_three_headers(
    client: TestClient,
) -> None:
    resp = _run(client, str(uuid4()), "ak_hdr_default")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "workflow not found"}
    assert _rate_headers(resp) == _expected(100, 99, _WINDOW_END)


def test_ac4_422_for_schema_invalid_json_body_carries_three_headers(
    client: TestClient, workflow_id: str
) -> None:
    # Well-formed JSON that fails WorkflowRunIn: triggered_by is missing.
    resp = _run(client, workflow_id, "ak_hdr_default", body={"input": {}})
    assert resp.status_code == 422
    assert _rate_headers(resp) == _expected(100, 99, _WINDOW_END)


def test_ac4_429_rate_limit_exceeded_carries_exact_headers_once(
    client: TestClient, workflow_id: str
) -> None:
    for n in range(1, 101):
        resp = _run(client, workflow_id, "ak_hdr_default")
        assert resp.status_code == 200
        assert _rate_headers(resp) == _expected(100, 100 - n, _WINDOW_END)

    refused = _run(client, workflow_id, "ak_hdr_default")
    assert refused.status_code == 429
    assert refused.json() == {"detail": "rate limit exceeded"}
    # Exact values also catch a header written twice (joined as "0, 0").
    assert _rate_headers(refused) == _expected(100, 0, _WINDOW_END)


def test_ac4_malformed_json_422_carries_no_headers_and_is_not_counted(
    client: TestClient, workflow_id: str
) -> None:
    resp = client.post(
        f"/workflows/{workflow_id}/run",
        content=b'{"triggered_by":',
        headers={"X-API-Key": "ak_hdr_default", "Content-Type": "application/json"},
    )
    assert resp.status_code == 422
    assert _rate_headers(resp) == {}

    ok = _run(client, workflow_id, "ak_hdr_default")
    assert ok.status_code == 200
    assert _rate_headers(ok) == _expected(100, 99, _WINDOW_END)


# AC-2: X-RateLimit-Reset follows the fixed, minute-aligned window.


def test_ac2_reset_advances_to_next_window_end_after_clock_passes_it(
    client: TestClient, workflow_id: str, clock: _ManualClock
) -> None:
    for _ in range(100):
        _run(client, workflow_id, "ak_hdr_default")
    refused = _run(client, workflow_id, "ak_hdr_default")
    assert refused.status_code == 429

    clock.set(datetime(2026, 10, 8, 18, 1, 0, tzinfo=timezone.utc))  # window end
    resp = _run(client, workflow_id, "ak_hdr_default")
    assert resp.status_code == 200
    assert _rate_headers(resp) == _expected(100, 99, _NEXT_WINDOW_END)


def test_ac2_second_59_and_second_0_fall_in_different_windows(
    client: TestClient, workflow_id: str, clock: _ManualClock
) -> None:
    clock.set(datetime(2026, 10, 8, 18, 0, 59, tzinfo=timezone.utc))
    late = _run(client, workflow_id, "ak_hdr_rpm_3")
    assert late.status_code == 200
    assert _rate_headers(late) == _expected(3, 2, _WINDOW_END)

    clock.set(datetime(2026, 10, 8, 18, 1, 0, tzinfo=timezone.utc))
    early = _run(client, workflow_id, "ak_hdr_rpm_3")
    assert early.status_code == 200
    assert _rate_headers(early) == _expected(3, 2, _NEXT_WINDOW_END)


# AC-6: a blocked key's 429 carries 0, 0, null, also after the window ends.


@pytest.mark.parametrize("raw_key", ["ak_hdr_rpm_0", "ak_hdr_rpm_neg5"])
def test_ac6_blocked_key_429_carries_zero_zero_null(
    client: TestClient, workflow_id: str, clock: _ManualClock, raw_key: str
) -> None:
    for now in (
        _START,
        datetime(2026, 10, 8, 18, 1, 0, tzinfo=timezone.utc),
        _START + timedelta(hours=1),
    ):
        clock.set(now)
        resp = _run(client, workflow_id, raw_key)
        assert resp.status_code == 429
        assert resp.json() == {"detail": "api key blocked"}
        assert _rate_headers(resp) == _expected(0, 0, "null")


def test_ac6_blocked_key_404_path_still_gets_blocked_429(
    client: TestClient,
) -> None:
    # Authentication runs before the route's 404, so the block wins.
    resp = _run(client, str(uuid4()), "ak_hdr_rpm_0")
    assert resp.status_code == 429
    assert resp.json() == {"detail": "api key blocked"}
    assert _rate_headers(resp) == _expected(0, 0, "null")


# AC-7: a 401 carries no X-RateLimit-* headers and changes no count.


@pytest.mark.parametrize(
    ("raw_key", "detail"),
    [
        (None, "missing api key"),
        ("ak_hdr_does_not_exist", "invalid api key"),
    ],
)
def test_ac7_401_carries_no_rate_headers(
    client: TestClient, workflow_id: str, raw_key: str | None, detail: str
) -> None:
    resp = _run(client, workflow_id, raw_key)
    assert resp.status_code == 401
    assert resp.json() == {"detail": detail}
    assert _rate_headers(resp) == {}


def test_ac7_401_between_keyed_requests_has_no_headers_and_no_count(
    client: TestClient, workflow_id: str
) -> None:
    first = _run(client, workflow_id, "ak_hdr_default")
    assert first.status_code == 200
    assert _rate_headers(first) == _expected(100, 99, _WINDOW_END)

    missing = _run(client, workflow_id, None)
    assert missing.status_code == 401
    assert missing.json() == {"detail": "missing api key"}
    assert _rate_headers(missing) == {}

    invalid = _run(client, workflow_id, "ak_hdr_does_not_exist")
    assert invalid.status_code == 401
    assert invalid.json() == {"detail": "invalid api key"}
    assert _rate_headers(invalid) == {}

    second = _run(client, workflow_id, "ak_hdr_default")
    assert second.status_code == 200
    assert _rate_headers(second) == _expected(100, 98, _WINDOW_END)
