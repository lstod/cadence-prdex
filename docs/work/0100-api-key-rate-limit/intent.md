# Intent: Rate-limit API endpoints per API key

- ID: 0100
- Author: lstod
- Date: 2026-10-08
- Source: idea
- Risk: high
- Status: accepted

## Problem
Endpoints that authenticate with an API key accept unlimited requests. One client can send requests as fast as it likes and exhaust service capacity for every other client. The product owner and every API client feel this; the API-key auth dependency already carries a `TODO: rate limiting`.

## Proposed outcome
Each API key may make at most 100 requests per minute to endpoints that authenticate with an API key. When a key goes over its limit, those endpoints return `429 Too Many Requests`.

- The window is fixed and resets every minute.
- The limit is counted per API key, not globally: one key reaching its limit does not affect another key.
- A key whose `requests_per_minute` is set to a value lower than 100 is limited to that value instead.
- A key whose `requests_per_minute` is 0 or negative is blocked: every request returns `429 Too Many Requests` with a null reset time, since waiting does not lift the block.
- Responses include `X-RateLimit-Limit`, `X-RateLimit-Remaining`, and `X-RateLimit-Reset` headers. `X-RateLimit-Reset` is the Unix time, in seconds, when the current window ends.

## Affected users and systems
- API clients that call endpoints authenticated with an API key (`X-API-Key`).
- The shared API-key auth dependency, which is a security control.
- The `ApiKey.requests_per_minute` setting, which is honoured when it is below 100.

## Constraints
- The default limit is 100 requests per minute per API key; a per-key setting can only lower it, never raise it.
- Not production ready by design: the limit may hold within a single app process only. The product owner accepts this.

## Out of scope
- Endpoints that do not authenticate with an API key, for example `/login`, `/health`, the webhook routes, and `/runs`.
- A limit shared across several app instances (for example a Redis-backed counter). That is a follow-up.
- Raising a key's limit above 100 per minute.

## Open questions
- How a null reset time appears on the wire for a blocked key (`X-RateLimit-Reset` omitted, or sent with an empty or literal `null` value) is to be settled in the spec.
