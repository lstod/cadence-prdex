# Evidence: Rate-limit API endpoints per API key

- Spec: [spec.md](spec.md)
- Plan: [plan.md](plan.md)

## Test proof

### T1

`scripts/implement-check.sh first` on the 28 new test IDs in `tests/auth/test_rate_limit.py`, run against a placeholder `app/auth/rate_limit.py` (allow every hit, `remaining = limit`, `reset = 0`, `effective_limit` always 100, a new limiter per `get_rate_limiter()` call):

```
implement-check: first 23 assertion, 5 guard, 0 not proved in 0s
```

Fails on an assertion:

```
test_ac1_first_100_hits_allowed_then_101st_refused (assert True is False)
test_ac1_remaining_counts_down_and_reset_is_window_end (AssertionError: assert RateLimitDecision(allowed=True, limit=100, remaining=100,)
test_ac1_refused_hit_reports_limit_zero_remaining_and_window_end (AssertionError: assert RateLimitDecision(allowed=True, limit=100, remaining=100,)
test_ac1_repeated_refusals_stay_refused_with_zero_remaining (assert True is False)
test_ac2_next_window_allows_again_with_remaining_limit_minus_one (assert True is False)
test_ac2_window_reset_with_lowered_limit_gives_limit_minus_one (assert True is False)
test_ac2_second_59_and_second_0_fall_in_different_windows (assert 0 == 1791482460)
test_ac2_second_59_is_still_in_the_window_started_at_second_5 (assert True is False)
test_ac2_window_is_aligned_to_the_minute_not_to_the_first_hit (assert 0 == 1791482520)
test_ac2_window_alignment_uses_utc_for_aware_non_utc_clock (assert 0 == 1791482460)
test_ac2_default_clock_reset_is_next_minute_boundary (assert 1791511208.470937 < 0)
test_ac3_second_key_unaffected_while_first_key_is_refused (assert True is False)
test_ac3_interleaved_keys_count_separately (assert 100 == 89)
test_ac3_separate_limiter_instances_do_not_share_counts (assert True is False)
test_ac5_effective_limit[1-1] (assert 100 == 1)
test_ac5_effective_limit[99-99] (assert 100 == 99)
test_ac5_effective_limit[0-0] (assert 100 == 0)
test_ac5_effective_limit[-5-0] (assert 100 == 0)
test_ac5_lowered_limit_refuses_the_request_after_the_limit (assert [True, True, True, True] == [True, True, True, False])
test_ac5_hit_rejects_non_positive_limit[0] (Failed: DID NOT RAISE ValueError)
test_ac5_hit_rejects_non_positive_limit[-1] (Failed: DID NOT RAISE ValueError)
test_ac5_hit_rejects_non_positive_limit[-5] (Failed: DID NOT RAISE ValueError)
test_get_rate_limiter_returns_one_shared_default_instance (assert <app.auth.rate_limit.InMemoryRateLimiter object at 0x106b9e950> is <app.a)
```

The fifth guard, `test_spec_example_timestamps_are_consistent`, checked only the test file's own constants and called no app code, so no break in a plan file could kill it (`bad break 1: test path: tests/auth/test_rate_limit.py`). The run stopped; lstod chose to remove that test, since the other tests already compare the limiter's output against the same constants. 27 tests remain, with 4 guards.

```
implement-check: killed tests/auth/test_rate_limit.py::test_ac1_reset_is_integer_unix_seconds (app/auth/rate_limit.py:56; assert (1791482461 % 60) == 0)
implement-check: killed tests/auth/test_rate_limit.py::test_ac5_effective_limit[None-100] (app/auth/rate_limit.py:76; assert 99 == 100)
implement-check: killed tests/auth/test_rate_limit.py::test_ac5_effective_limit[100-100] (app/auth/rate_limit.py:79; assert 99 == 100)
implement-check: killed tests/auth/test_rate_limit.py::test_ac5_effective_limit[250-100] (app/auth/rate_limit.py:79; assert 250 == 100)
implement-check: mutate 4 breaks, 4 killed in 1s
```

| Task | Test | At | Break | Result |
|---|---|---|---|---|
| T1 | `tests/auth/test_rate_limit.py::test_ac1_reset_is_integer_unix_seconds` | `app/auth/rate_limit.py:56` | `reset = window_start + WINDOW_SECONDS` -> `reset = window_start + WINDOW_SECONDS + 1` | killed |
| T1 | `tests/auth/test_rate_limit.py::test_ac5_effective_limit[None-100]` | `app/auth/rate_limit.py:76` | `return DEFAULT_LIMIT` -> `return DEFAULT_LIMIT - 1` | killed |
| T1 | `tests/auth/test_rate_limit.py::test_ac5_effective_limit[100-100]` | `app/auth/rate_limit.py:79` | `return min(requests_per_minute, DEFAULT_LIMIT)` -> `return min(requests_per_minute, DEFAULT_LIMIT - 1)` | killed |
| T1 | `tests/auth/test_rate_limit.py::test_ac5_effective_limit[250-100]` | `app/auth/rate_limit.py:79` | `return min(requests_per_minute, DEFAULT_LIMIT)` -> `return max(requests_per_minute, DEFAULT_LIMIT)` | killed |

### T2

`scripts/implement-check.sh first` on the 11 new test functions in `tests/auth/test_api_key_rate_limit.py` (an ID without `[` covers its parametrized cases), run against `api_key_auth` before the change:

```
implement-check: fails on an assertion: tests/auth/test_api_key_rate_limit.py::test_ac1_default_key_gets_100_ok_then_429_rate_limit_exceeded (assert 200 == 429)
implement-check: fails on an assertion: tests/auth/test_api_key_rate_limit.py::test_ac1_rate_limit_429_carries_limit_zero_remaining_and_window_end (assert 200 == 429)
implement-check: fails on an assertion: tests/auth/test_api_key_rate_limit.py::test_ac1_first_request_is_handled_with_remaining_99 (assert [] == [(1, 100)])
implement-check: fails on an assertion: tests/auth/test_api_key_rate_limit.py::test_ac3_second_key_unaffected_while_first_key_is_refused (assert 200 == 429)
implement-check: fails on an assertion: tests/auth/test_api_key_rate_limit.py::test_ac5_lowered_limit_refuses_the_request_after_the_limit (assert 200 == 429)
implement-check: fails on an assertion: tests/auth/test_api_key_rate_limit.py::test_ac5_rpm_3_is_refused_on_the_4th_request (assert [200, 200, 200, 200] == [200, 200, 200, 429])
implement-check: fails on an assertion: tests/auth/test_api_key_rate_limit.py::test_ac5_limit_of_100_or_more_is_capped_at_100 (assert 200 == 429)
implement-check: fails on an assertion: tests/auth/test_api_key_rate_limit.py::test_ac6_blocked_key_gets_429_on_every_request_and_never_hits_limiter (assert 200 == 429)
implement-check: fails on an assertion: tests/auth/test_api_key_rate_limit.py::test_ac6_blocked_key_stays_blocked_after_the_window_ends (assert 200 == 429)
implement-check: fails on an assertion: tests/auth/test_api_key_rate_limit.py::test_ac7_rejected_key_is_401_without_headers_and_not_counted (assert [] == [(1, 100)])
implement-check: fails on an assertion: tests/auth/test_api_key_rate_limit.py::test_ac7_rejected_requests_between_valid_ones_do_not_change_the_count (assert [] == [99, 98, 97])
implement-check: first 11 assertion, 0 guard, 0 not proved in 1s
```

No guards, so `mutate` did not run.

### T3

`scripts/implement-check.sh first` on the 13 new test functions in `tests/routes/test_rate_limit_headers.py`, run before the middleware existed:

```
implement-check: fails on an assertion: tests/routes/test_rate_limit_headers.py::test_ac4_200_from_run_carries_three_headers_with_exact_values (AssertionError: assert {} == {'x-ratelimit-limit': '100', 'x-ratelimit-remaining)
implement-check: fails on an assertion: tests/routes/test_rate_limit_headers.py::test_ac4_remaining_decreases_on_each_200_in_the_window (AssertionError: assert [{}, {}, {}] == [{'x-ratelimit-limit': '100', 'x-ratelimi)
implement-check: fails on an assertion: tests/routes/test_rate_limit_headers.py::test_ac4_lowered_limit_200_carries_its_own_limit (AssertionError: assert {} == {'x-ratelimit-limit': '3', 'x-ratelimit-remaining':)
implement-check: fails on an assertion: tests/routes/test_rate_limit_headers.py::test_ac4_404_for_unknown_workflow_carries_three_headers (AssertionError: assert {} == {'x-ratelimit-limit': '100', 'x-ratelimit-remaining)
implement-check: fails on an assertion: tests/routes/test_rate_limit_headers.py::test_ac4_422_for_schema_invalid_json_body_carries_three_headers (AssertionError: assert {} == {'x-ratelimit-limit': '100', 'x-ratelimit-remaining)
implement-check: fails on an assertion: tests/routes/test_rate_limit_headers.py::test_ac4_429_rate_limit_exceeded_carries_exact_headers_once (AssertionError: assert {} == {'x-ratelimit-limit': '100', 'x-ratelimit-remaining)
implement-check: fails on an assertion: tests/routes/test_rate_limit_headers.py::test_ac4_malformed_json_422_carries_no_headers_and_is_not_counted (AssertionError: assert {} == {'x-ratelimit-limit': '100', 'x-ratelimit-remaining)
implement-check: fails on an assertion: tests/routes/test_rate_limit_headers.py::test_ac2_reset_advances_to_next_window_end_after_clock_passes_it (AssertionError: assert {} == {'x-ratelimit-limit': '100', 'x-ratelimit-remaining)
implement-check: fails on an assertion: tests/routes/test_rate_limit_headers.py::test_ac2_second_59_and_second_0_fall_in_different_windows (AssertionError: assert {} == {'x-ratelimit-limit': '3', 'x-ratelimit-remaining':)
implement-check: passes before the change (guard): tests/routes/test_rate_limit_headers.py::test_ac6_blocked_key_429_carries_zero_zero_null
implement-check: passes before the change (guard): tests/routes/test_rate_limit_headers.py::test_ac6_blocked_key_404_path_still_gets_blocked_429
implement-check: passes before the change (guard): tests/routes/test_rate_limit_headers.py::test_ac7_401_carries_no_rate_headers
implement-check: fails on an assertion: tests/routes/test_rate_limit_headers.py::test_ac7_401_between_keyed_requests_has_no_headers_and_no_count (AssertionError: assert {} == {'x-ratelimit-limit': '100', 'x-ratelimit-remaining)
implement-check: first 10 assertion, 3 guard, 0 not proved in 1s
```

```
implement-check: killed tests/routes/test_rate_limit_headers.py::test_ac6_blocked_key_429_carries_zero_zero_null (app/auth/api_key.py:60; AssertionError: assert {'x-ratelimit-limit': '0', 'x-ratelimit-remaining': '1', )
implement-check: killed tests/routes/test_rate_limit_headers.py::test_ac6_blocked_key_404_path_still_gets_blocked_429 (app/auth/api_key.py:64; AssertionError: assert {'detail': 'api key disabled'} == {'detail': 'api key blo)
implement-check: killed tests/routes/test_rate_limit_headers.py::test_ac7_401_carries_no_rate_headers (app/main.py:14; AssertionError: assert {'x-ratelimit-limit': '100'} == {})
implement-check: mutate 3 breaks, 3 killed in 2s
```

| Task | Test | At | Break | Result |
|---|---|---|---|---|
| T3 | `tests/routes/test_rate_limit_headers.py::test_ac6_blocked_key_429_carries_zero_zero_null` | `app/auth/api_key.py:60` | `headers = _rate_limit_headers(0, 0, None)` -> `headers = _rate_limit_headers(0, 1, None)` | killed |
| T3 | `tests/routes/test_rate_limit_headers.py::test_ac6_blocked_key_404_path_still_gets_blocked_429` | `app/auth/api_key.py:64` | `detail="api key blocked",` -> `detail="api key disabled",` | killed |
| T3 | `tests/routes/test_rate_limit_headers.py::test_ac7_401_carries_no_rate_headers` | `app/main.py:14` | `headers = getattr(request.state, "rate_limit_headers", None)` -> `headers = getattr(request.state, "rate_limit_headers", {"X-RateLimit-Limit": "100"})` | killed |

## Verify

### T1 (AC-1, AC-2, AC-3, AC-5, AC-9)

```
$ make app-test-fast && ~/.venvs/cadence/bin/python -m mypy --strict app/auth/rate_limit.py
app-lint-changed: no new ruff errors in 2 changed files
rm -f test.db
DATABASE_URL=sqlite:///./test.db /Users/mikaylastewart/.venvs/cadence/bin/python -m pytest -q --no-cov -x
113 passed, 2 warnings in 4.43s
Success: no issues found in 1 source file
```

### T2 (AC-1, AC-3, AC-5, AC-6, AC-7)

```
$ make app-test-fast
app-lint-changed: no new ruff errors in 4 changed files
rm -f test.db
DATABASE_URL=sqlite:///./test.db /Users/mikaylastewart/.venvs/cadence/bin/python -m pytest -q --no-cov -x
131 passed, 2 warnings in 5.62s
```

### T3 (AC-2, AC-4, AC-6, AC-7)

```
$ make app-test-fast
app-lint-changed: no new ruff errors in 6 changed files
rm -f test.db
DATABASE_URL=sqlite:///./test.db /Users/mikaylastewart/.venvs/cadence/bin/python -m pytest -q --no-cov -x
146 passed, 2 warnings in 6.20s
```

### T4 (AC-9)

No testable behavior: this task runs checks only and changes no code.

```
$ make typecheck && ~/.venvs/cadence/bin/python -m mypy --strict app/auth/rate_limit.py && make clean test
/Users/mikaylastewart/.venvs/cadence/bin/python -m mypy --strict \
	    app/services/workflows \
	    app/services/billing \
	    app/auth/passwords.py \
	    app/auth/migrations.py
Success: no issues found in 11 source files
Success: no issues found in 1 source file
rm -rf test.db .pytest_cache .mypy_cache .ruff_cache
DATABASE_URL=sqlite:///./test.db /Users/mikaylastewart/.venvs/cadence/bin/python -m pytest -q --no-cov
146 passed, 2 warnings in 6.29s
```
