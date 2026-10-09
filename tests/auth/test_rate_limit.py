"""Unit tests for the per-API-key fixed-window rate limiter (work item 0100, T1).

Each test names the acceptance criterion of
docs/work/0100-api-key-rate-limit/spec.md that it asserts.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.auth import rate_limit

InMemoryRateLimiter = rate_limit.InMemoryRateLimiter
RateLimitDecision = rate_limit.RateLimitDecision
effective_limit = rate_limit.effective_limit
get_rate_limiter = rate_limit.get_rate_limiter

# Spec section 5: 2026-10-08T18:00:05Z is Unix 1791482405; its window ends at
# 1791482460, and the next window ends at 1791482520.
_START = datetime(2026, 10, 8, 18, 0, 5, tzinfo=timezone.utc)
_WINDOW_END = 1791482460
_NEXT_WINDOW_END = 1791482520


class _ManualClock:
    def __init__(self, start: datetime) -> None:
        self._now = start

    def __call__(self) -> datetime:
        return self._now

    def set(self, now: datetime) -> None:
        self._now = now

    def advance(self, delta: timedelta) -> None:
        self._now += delta


def _limiter(start: datetime = _START) -> tuple[InMemoryRateLimiter, _ManualClock]:
    clock = _ManualClock(start)
    return InMemoryRateLimiter(clock=clock), clock


# AC-1: default limit of 100, the 101st request in a window is refused.


def test_ac1_first_100_hits_allowed_then_101st_refused() -> None:
    limiter, clock = _limiter()
    decisions = []
    for _ in range(100):
        decisions.append(limiter.hit(1, 100))
        clock.advance(timedelta(milliseconds=300))  # stays inside 18:00
    assert all(d.allowed for d in decisions)

    refused = limiter.hit(1, 100)
    assert refused.allowed is False


def test_ac1_remaining_counts_down_and_reset_is_window_end() -> None:
    limiter, _ = _limiter()
    first = limiter.hit(1, 100)
    assert first == RateLimitDecision(
        allowed=True, limit=100, remaining=99, reset=_WINDOW_END
    )

    for _ in range(98):
        limiter.hit(1, 100)
    hundredth = limiter.hit(1, 100)
    assert hundredth == RateLimitDecision(
        allowed=True, limit=100, remaining=0, reset=_WINDOW_END
    )


def test_ac1_refused_hit_reports_limit_zero_remaining_and_window_end() -> None:
    limiter, clock = _limiter()
    for _ in range(100):
        limiter.hit(1, 100)
    clock.set(datetime(2026, 10, 8, 18, 0, 40, tzinfo=timezone.utc))
    refused = limiter.hit(1, 100)
    assert refused == RateLimitDecision(
        allowed=False, limit=100, remaining=0, reset=_WINDOW_END
    )


def test_ac1_repeated_refusals_stay_refused_with_zero_remaining() -> None:
    limiter, _ = _limiter()
    for _ in range(100):
        limiter.hit(1, 100)
    for _ in range(5):
        refused = limiter.hit(1, 100)
        assert refused.allowed is False
        assert refused.remaining == 0
        assert refused.limit == 100


def test_ac1_reset_is_integer_unix_seconds() -> None:
    limiter, _ = _limiter()
    decision = limiter.hit(1, 100)
    assert type(decision.reset) is int
    assert decision.reset % 60 == 0


# AC-2: fixed window aligned to the UTC clock minute.


def test_ac2_next_window_allows_again_with_remaining_limit_minus_one() -> None:
    limiter, clock = _limiter()
    for _ in range(100):
        limiter.hit(1, 100)
    assert limiter.hit(1, 100).allowed is False

    clock.set(datetime(2026, 10, 8, 18, 1, 0, tzinfo=timezone.utc))  # the window's end
    after = limiter.hit(1, 100)
    assert after == RateLimitDecision(
        allowed=True, limit=100, remaining=99, reset=_NEXT_WINDOW_END
    )


def test_ac2_window_reset_with_lowered_limit_gives_limit_minus_one() -> None:
    limiter, clock = _limiter()
    for _ in range(3):
        limiter.hit(1, 3)
    assert limiter.hit(1, 3).allowed is False

    clock.set(datetime(2026, 10, 8, 18, 1, 0, tzinfo=timezone.utc))
    after = limiter.hit(1, 3)
    assert after.allowed is True
    assert after.remaining == 2
    assert after.reset == _NEXT_WINDOW_END


def test_ac2_second_59_and_second_0_fall_in_different_windows() -> None:
    limiter, clock = _limiter(datetime(2026, 10, 8, 18, 0, 59, tzinfo=timezone.utc))
    at_59 = limiter.hit(1, 1)
    assert at_59.allowed is True
    assert at_59.reset == _WINDOW_END

    clock.set(datetime(2026, 10, 8, 18, 1, 0, tzinfo=timezone.utc))
    at_00 = limiter.hit(1, 1)
    assert at_00.allowed is True
    assert at_00.remaining == 0
    assert at_00.reset == _NEXT_WINDOW_END


def test_ac2_second_59_is_still_in_the_window_started_at_second_5() -> None:
    limiter, clock = _limiter()  # 18:00:05
    assert limiter.hit(1, 1).allowed is True

    clock.set(datetime(2026, 10, 8, 18, 0, 59, 999000, tzinfo=timezone.utc))
    late = limiter.hit(1, 1)
    assert late.allowed is False
    assert late.reset == _WINDOW_END


def test_ac2_window_is_aligned_to_the_minute_not_to_the_first_hit() -> None:
    # First hit at 18:00:05. A window measured from the first hit would last
    # until 18:01:05; the fixed window ends at 18:01:00.
    limiter, clock = _limiter()
    assert limiter.hit(1, 1).allowed is True

    clock.set(datetime(2026, 10, 8, 18, 1, 2, tzinfo=timezone.utc))
    decision = limiter.hit(1, 1)
    assert decision.allowed is True
    assert decision.reset == _NEXT_WINDOW_END


def test_ac2_window_alignment_uses_utc_for_aware_non_utc_clock() -> None:
    # 18:00:05Z expressed in UTC-08:00. Same instant, same window end.
    pacific = timezone(timedelta(hours=-8))
    limiter, _ = _limiter(datetime(2026, 10, 8, 10, 0, 5, tzinfo=pacific))
    assert limiter.hit(1, 100).reset == _WINDOW_END


def test_ac2_default_clock_reset_is_next_minute_boundary() -> None:
    limiter = InMemoryRateLimiter()
    before = datetime.now(tz=timezone.utc).timestamp()
    decision = limiter.hit(1, 100)
    after = datetime.now(tz=timezone.utc).timestamp()
    assert decision.reset % 60 == 0
    assert before < decision.reset <= after + 60
    assert decision.reset - 60 <= after


# AC-3: counts are kept per key id.


def test_ac3_second_key_unaffected_while_first_key_is_refused() -> None:
    limiter, _ = _limiter()
    for _ in range(100):
        limiter.hit(1, 100)
    assert limiter.hit(1, 100).allowed is False

    other = limiter.hit(2, 100)
    assert other == RateLimitDecision(
        allowed=True, limit=100, remaining=99, reset=_WINDOW_END
    )

    # And the first key is still refused afterwards.
    assert limiter.hit(1, 100).allowed is False


def test_ac3_interleaved_keys_count_separately() -> None:
    limiter, _ = _limiter()
    for _ in range(10):
        limiter.hit(1, 100)
    for _ in range(3):
        limiter.hit(2, 100)
    assert limiter.hit(1, 100).remaining == 89
    assert limiter.hit(2, 100).remaining == 96


def test_ac3_separate_limiter_instances_do_not_share_counts() -> None:
    first, _ = _limiter()
    second, _ = _limiter()
    for _ in range(5):
        first.hit(1, 5)
    assert first.hit(1, 5).allowed is False
    assert second.hit(1, 5).remaining == 4


# AC-5: lowered limits, the cap of 100, and the blocked value.


@pytest.mark.parametrize(
    ("requests_per_minute", "expected"),
    [
        (None, 100),
        (1, 1),
        (99, 99),
        (100, 100),
        (250, 100),
        (0, 0),
        (-5, 0),
    ],
)
def test_ac5_effective_limit(requests_per_minute: int | None, expected: int) -> None:
    assert effective_limit(requests_per_minute) == expected


def test_ac5_lowered_limit_refuses_the_request_after_the_limit() -> None:
    limiter, _ = _limiter()
    limit = effective_limit(3)
    results = [limiter.hit(1, limit) for _ in range(4)]
    assert [r.allowed for r in results] == [True, True, True, False]
    assert [r.remaining for r in results] == [2, 1, 0, 0]
    assert all(r.limit == 3 for r in results)


@pytest.mark.parametrize("limit", [0, -1, -5])
def test_ac5_hit_rejects_non_positive_limit(limit: int) -> None:
    # Blocked keys (effective limit 0) never reach the limiter; hit() takes a
    # positive limit only (spec section 4, Interfaces).
    limiter, _ = _limiter()
    with pytest.raises(ValueError):
        limiter.hit(1, limit)


# Provider used by api_key_auth and replaced in tests through dependency_overrides.


def test_get_rate_limiter_returns_one_shared_default_instance() -> None:
    first = get_rate_limiter()
    second = get_rate_limiter()
    assert first is second
    assert callable(getattr(first, "hit", None))
