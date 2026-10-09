"""Tests for the rate limit inside api_key_auth (work item 0100, T2).

The real `api_key_auth` runs on a throwaway FastAPI app with `get_db` on an
in-memory SQLite database and `get_rate_limiter` replaced by a spy around an
`InMemoryRateLimiter` with a manual clock. The app has no middleware, so
headers on a `200` are not visible here (T3 covers them); the spy's recorded
decisions show what the dependency asked the limiter and what it was told.

Each test names the acceptance criterion of
docs/work/0100-api-key-rate-limit/spec.md that it asserts.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth.api_key import api_key_auth, get_db
from app.auth.rate_limit import (
    InMemoryRateLimiter,
    RateLimitDecision,
    get_rate_limiter,
)
from app.db import Base
from app.models.api_key import ApiKey
from app.models.run import Run as _Run
from app.models.workflow import Workflow as _Workflow

_REGISTERED = (_Run, _Workflow)

# Spec section 5: 2026-10-08T18:00:05Z is Unix 1791482405; its window ends at
# 1791482460.
_START = datetime(2026, 10, 8, 18, 0, 5, tzinfo=timezone.utc)
_WINDOW_END = 1791482460

_RATE_HEADERS = ("x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset")

# Seeded keys: raw key string -> requests_per_minute.
_KEYS: dict[str, int | None] = {
    "ak_default_a": None,
    "ak_default_b": None,
    "ak_rpm_1": 1,
    "ak_rpm_3": 3,
    "ak_rpm_99": 99,
    "ak_rpm_100": 100,
    "ak_rpm_250": 250,
    "ak_rpm_0": 0,
    "ak_rpm_neg5": -5,
}


class _ManualClock:
    def __init__(self, start: datetime) -> None:
        self._now = start

    def __call__(self) -> datetime:
        return self._now

    def set(self, now: datetime) -> None:
        self._now = now


class _SpyLimiter:
    """Wraps a real InMemoryRateLimiter and records every hit and decision."""

    def __init__(self, clock: _ManualClock) -> None:
        self._inner = InMemoryRateLimiter(clock=clock)
        self.calls: list[tuple[int, int]] = []
        self.decisions: list[RateLimitDecision] = []

    def hit(self, key_id: int, limit: int) -> RateLimitDecision:
        self.calls.append((key_id, limit))
        decision = self._inner.hit(key_id, limit)
        self.decisions.append(decision)
        return decision


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
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
    try:
        yield factory
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture
def key_ids(session_factory: sessionmaker[Session]) -> dict[str, int]:
    db = session_factory()
    try:
        return {row.key: row.id for row in db.query(ApiKey).all()}
    finally:
        db.close()


@pytest.fixture
def clock() -> _ManualClock:
    return _ManualClock(_START)


@pytest.fixture
def spy(clock: _ManualClock) -> _SpyLimiter:
    return _SpyLimiter(clock)


@pytest.fixture
def client(
    session_factory: sessionmaker[Session], spy: _SpyLimiter
) -> Iterator[TestClient]:
    test_app = FastAPI()

    def _override_db() -> Iterator[Session]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    @test_app.get("/protected")
    def protected(caller: ApiKey = Depends(api_key_auth)) -> dict[str, str]:
        return {"caller_key_name": caller.name}

    test_app.dependency_overrides[get_db] = _override_db
    test_app.dependency_overrides[get_rate_limiter] = lambda: spy
    try:
        yield TestClient(test_app)
    finally:
        test_app.dependency_overrides.clear()


def _has_rate_headers(headers: object) -> list[str]:
    names = {name.lower() for name in headers.keys()}  # type: ignore[attr-defined]
    return [h for h in _RATE_HEADERS if h in names]


# AC-1: default limit of 100 through the real dependency.


def test_ac1_default_key_gets_100_ok_then_429_rate_limit_exceeded(
    client: TestClient, spy: _SpyLimiter, key_ids: dict[str, int]
) -> None:
    statuses = [
        client.get("/protected", headers={"X-API-Key": "ak_default_a"}).status_code
        for _ in range(100)
    ]
    assert statuses == [200] * 100

    refused = client.get("/protected", headers={"X-API-Key": "ak_default_a"})
    assert refused.status_code == 429
    assert refused.json() == {"detail": "rate limit exceeded"}
    # The dependency hit the injected limiter once per request, for this key
    # with the default limit of 100.
    assert spy.calls == [(key_ids["ak_default_a"], 100)] * 101


def test_ac1_rate_limit_429_carries_limit_zero_remaining_and_window_end(
    client: TestClient,
) -> None:
    # Format table, Refused request column, on the 429 the dependency raises.
    for _ in range(100):
        client.get("/protected", headers={"X-API-Key": "ak_default_a"})
    refused = client.get("/protected", headers={"X-API-Key": "ak_default_a"})
    assert refused.status_code == 429
    assert refused.headers["X-RateLimit-Limit"] == "100"
    assert refused.headers["X-RateLimit-Remaining"] == "0"
    assert refused.headers["X-RateLimit-Reset"] == str(_WINDOW_END)


def test_ac1_first_request_is_handled_with_remaining_99(
    client: TestClient, spy: _SpyLimiter, key_ids: dict[str, int]
) -> None:
    resp = client.get("/protected", headers={"X-API-Key": "ak_default_a"})
    assert resp.status_code == 200
    assert resp.json() == {"caller_key_name": "ak_default_a"}
    assert spy.calls == [(key_ids["ak_default_a"], 100)]
    assert spy.decisions == [
        RateLimitDecision(allowed=True, limit=100, remaining=99, reset=_WINDOW_END)
    ]


# AC-3: the limit is per key.


def test_ac3_second_key_unaffected_while_first_key_is_refused(
    client: TestClient, spy: _SpyLimiter, key_ids: dict[str, int]
) -> None:
    for _ in range(100):
        client.get("/protected", headers={"X-API-Key": "ak_default_a"})
    refused = client.get("/protected", headers={"X-API-Key": "ak_default_a"})
    assert refused.status_code == 429
    assert refused.json() == {"detail": "rate limit exceeded"}

    other = client.get("/protected", headers={"X-API-Key": "ak_default_b"})
    assert other.status_code == 200
    assert other.json() == {"caller_key_name": "ak_default_b"}
    assert spy.calls[-1] == (key_ids["ak_default_b"], 100)
    assert spy.decisions[-1] == RateLimitDecision(
        allowed=True, limit=100, remaining=99, reset=_WINDOW_END
    )

    # The first key is still refused afterwards.
    again = client.get("/protected", headers={"X-API-Key": "ak_default_a"})
    assert again.status_code == 429
    assert again.json() == {"detail": "rate limit exceeded"}


# AC-5: lowered limits and the cap of 100.


@pytest.mark.parametrize(
    ("raw_key", "limit"),
    [
        ("ak_rpm_1", 1),
        ("ak_rpm_3", 3),
        ("ak_rpm_99", 99),
    ],
)
def test_ac5_lowered_limit_refuses_the_request_after_the_limit(
    client: TestClient,
    spy: _SpyLimiter,
    key_ids: dict[str, int],
    raw_key: str,
    limit: int,
) -> None:
    statuses = [
        client.get("/protected", headers={"X-API-Key": raw_key}).status_code
        for _ in range(limit)
    ]
    assert statuses == [200] * limit

    refused = client.get("/protected", headers={"X-API-Key": raw_key})
    assert refused.status_code == 429
    assert refused.json() == {"detail": "rate limit exceeded"}
    assert refused.headers["X-RateLimit-Limit"] == str(limit)
    assert spy.calls == [(key_ids[raw_key], limit)] * (limit + 1)


def test_ac5_rpm_3_is_refused_on_the_4th_request(client: TestClient) -> None:
    statuses = [
        client.get("/protected", headers={"X-API-Key": "ak_rpm_3"}).status_code
        for _ in range(4)
    ]
    assert statuses == [200, 200, 200, 429]


@pytest.mark.parametrize("raw_key", ["ak_rpm_100", "ak_rpm_250"])
def test_ac5_limit_of_100_or_more_is_capped_at_100(
    client: TestClient,
    spy: _SpyLimiter,
    key_ids: dict[str, int],
    raw_key: str,
) -> None:
    statuses = [
        client.get("/protected", headers={"X-API-Key": raw_key}).status_code
        for _ in range(100)
    ]
    assert statuses == [200] * 100

    refused = client.get("/protected", headers={"X-API-Key": raw_key})
    assert refused.status_code == 429
    assert refused.json() == {"detail": "rate limit exceeded"}
    assert refused.headers["X-RateLimit-Limit"] == "100"
    assert spy.calls == [(key_ids[raw_key], 100)] * 101


# AC-6: a key with requests_per_minute of 0 or negative is always blocked.


def _assert_blocked(resp: object) -> None:
    assert resp.status_code == 429  # type: ignore[attr-defined]
    assert resp.json() == {"detail": "api key blocked"}  # type: ignore[attr-defined]
    headers = resp.headers  # type: ignore[attr-defined]
    assert headers["X-RateLimit-Limit"] == "0"
    assert headers["X-RateLimit-Remaining"] == "0"
    assert headers["X-RateLimit-Reset"] == "null"


@pytest.mark.parametrize("raw_key", ["ak_rpm_0", "ak_rpm_neg5"])
def test_ac6_blocked_key_gets_429_on_every_request_and_never_hits_limiter(
    client: TestClient, spy: _SpyLimiter, raw_key: str
) -> None:
    for _ in range(3):
        _assert_blocked(client.get("/protected", headers={"X-API-Key": raw_key}))
    assert spy.calls == []


@pytest.mark.parametrize("raw_key", ["ak_rpm_0", "ak_rpm_neg5"])
def test_ac6_blocked_key_stays_blocked_after_the_window_ends(
    client: TestClient, spy: _SpyLimiter, clock: _ManualClock, raw_key: str
) -> None:
    _assert_blocked(client.get("/protected", headers={"X-API-Key": raw_key}))

    clock.set(datetime(2026, 10, 8, 18, 1, 0, tzinfo=timezone.utc))  # window end
    _assert_blocked(client.get("/protected", headers={"X-API-Key": raw_key}))

    clock.set(_START + timedelta(hours=1))
    _assert_blocked(client.get("/protected", headers={"X-API-Key": raw_key}))

    assert spy.calls == []


# AC-7: missing or invalid keys keep their 401 and are not counted.


@pytest.mark.parametrize(
    ("headers", "detail"),
    [
        ({}, "missing api key"),
        ({"X-API-Key": "   "}, "missing api key"),
        ({"X-API-Key": "ak_does_not_exist"}, "invalid api key"),
    ],
)
def test_ac7_rejected_key_is_401_without_headers_and_not_counted(
    client: TestClient,
    spy: _SpyLimiter,
    key_ids: dict[str, int],
    headers: dict[str, str],
    detail: str,
) -> None:
    for _ in range(3):
        resp = client.get("/protected", headers=headers)
        assert resp.status_code == 401
        assert resp.json() == {"detail": detail}
        assert _has_rate_headers(resp.headers) == []
    assert spy.calls == []

    # A following valid request is the key's first in the window.
    ok = client.get("/protected", headers={"X-API-Key": "ak_default_a"})
    assert ok.status_code == 200
    assert spy.calls == [(key_ids["ak_default_a"], 100)]
    assert spy.decisions == [
        RateLimitDecision(allowed=True, limit=100, remaining=99, reset=_WINDOW_END)
    ]


def test_ac7_rejected_requests_between_valid_ones_do_not_change_the_count(
    client: TestClient, spy: _SpyLimiter
) -> None:
    client.get("/protected", headers={"X-API-Key": "ak_default_a"})
    client.get("/protected", headers={"X-API-Key": "ak_default_a"})
    assert client.get("/protected").status_code == 401
    bad = client.get("/protected", headers={"X-API-Key": "ak_does_not_exist"})
    assert bad.status_code == 401
    third = client.get("/protected", headers={"X-API-Key": "ak_default_a"})
    assert third.status_code == 200
    assert [d.remaining for d in spy.decisions] == [99, 98, 97]
