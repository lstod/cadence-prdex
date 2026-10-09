"""Per-API-key fixed-window rate limiter.

The in-memory implementation is a placeholder for development and
single-process deployments: counts live in this process only and reset
on restart. The interface is shaped to swap to a Redis-backed
implementation without touching `api_key_auth` — anything that
satisfies the protocol can be injected.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Callable, Protocol

DEFAULT_LIMIT = 100
WINDOW_SECONDS = 60


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    reset: int  # window end, Unix seconds


def _default_clock() -> datetime:
    return datetime.now(tz=timezone.utc)


class RateLimiter(Protocol):
    """Counts requests per API key id in fixed one-minute windows."""

    def hit(self, key_id: int, limit: int) -> RateLimitDecision: ...


class InMemoryRateLimiter:
    """Fixed-window counter keyed by API key id.

    Keeps one entry per key for its current window only. Thread-safe
    within a single process; not shared between processes.
    """

    def __init__(self, clock: Callable[[], datetime] = _default_clock) -> None:
        self._clock = clock
        self._windows: dict[int, tuple[int, int]] = {}
        self._lock = Lock()

    def hit(self, key_id: int, limit: int) -> RateLimitDecision:
        if limit <= 0:
            raise ValueError("limit must be positive")
        now = int(self._clock().timestamp())
        window_start = now - now % WINDOW_SECONDS
        reset = window_start + WINDOW_SECONDS
        with self._lock:
            start, count = self._windows.get(key_id, (window_start, 0))
            if start != window_start:
                count = 0
            if count >= limit:
                self._windows[key_id] = (window_start, count)
                return RateLimitDecision(
                    allowed=False, limit=limit, remaining=0, reset=reset
                )
            count += 1
            self._windows[key_id] = (window_start, count)
            return RateLimitDecision(
                allowed=True, limit=limit, remaining=limit - count, reset=reset
            )


def effective_limit(requests_per_minute: int | None) -> int:
    """The key's limit per window; 0 means the key is blocked."""
    if requests_per_minute is None:
        return DEFAULT_LIMIT
    if requests_per_minute <= 0:
        return 0
    return min(requests_per_minute, DEFAULT_LIMIT)


_default_rate_limiter: RateLimiter = InMemoryRateLimiter()


def get_rate_limiter() -> RateLimiter:
    return _default_rate_limiter
